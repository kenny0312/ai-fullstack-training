r"""AI 兜底算子测试：全部使用假模型或本地模拟，不会联网，也不需要 API key。

Run from backend: .\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
"""

import importlib
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import anthropic
from fastapi.testclient import TestClient

import llm
import rule_engine
from llm import JudgementBatch, RuleJudgement


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PASS_CONTENT = (PROJECT_ROOT / "samples" / "pass.txt").read_text(encoding="utf-8")
RISK_CONTENT = (PROJECT_ROOT / "samples" / "risk.txt").read_text(encoding="utf-8")
# 关键词「甲方可随时解除合同」「争议解决」都没有逐字出现，但语义上存在。
PARAPHRASED = "甲方有权在任何时候单方终止本协议。\n如发生纠纷，提交上海仲裁委员会仲裁。"


def fake_judge(found_by_rule, calls=None):
    def judge(content, rules):
        if calls is not None:
            calls.append([rule["id"] for rule in rules])
        return {
            rule["id"]: RuleJudgement(rule_id=rule["id"], found=found_by_rule[rule["id"]][0],
                                      quote=found_by_rule[rule["id"]][1], reason="测试理由")
            for rule in rules if rule["id"] in found_by_rule
        }
    return judge


class LlmFallbackEngineTests(unittest.TestCase):
    def results(self, content, judge):
        return {r["rule_id"]: r for r in rule_engine.analyze_contract(content, judge=judge)}

    def test_semantic_hit_sets_ai_source_status_and_located_evidence(self):
        quote_r001 = "甲方有权在任何时候单方终止本协议"
        quote_r004 = "提交上海仲裁委员会仲裁"
        judge = fake_judge({"R001": (True, quote_r001), "R002": (False, ""), "R004": (True, quote_r004)})
        results = self.results(PARAPHRASED, judge)

        r001 = results["R001"]
        self.assertEqual(r001["source"], "llm")
        self.assertEqual((r001["original_status"], r001["final_status"]), ("risk", "risk"))
        self.assertTrue(r001["message"].startswith(rule_engine.AI_LABEL))
        evidence = r001["evidence"][0]
        self.assertEqual(PARAPHRASED[evidence["start"]:evidence["end"]], quote_r001)
        self.assertEqual(evidence["line"], 1)

        # missing 类规则：AI 找到了争议解决条款，所以判定通过。
        self.assertEqual(results["R004"]["final_status"], "pass")
        self.assertEqual(results["R004"]["evidence"][0]["line"], 2)
        # R002 的 AI 结论是未发现，matched 类规则因此通过，但仍标注为 AI 判断。
        self.assertEqual((results["R002"]["final_status"], results["R002"]["source"]), ("pass", "llm"))
        # R003 没有开启 AI 兜底，保持规则结果。
        self.assertEqual(results["R003"]["source"], "rule")

    def test_judge_only_receives_unmatched_rules_with_fallback_enabled(self):
        calls = []
        judge = fake_judge({"R001": (False, ""), "R002": (False, ""), "R004": (False, "")}, calls)
        results = self.results(RISK_CONTENT, judge)
        # 风险样例中 R001、R002、R004 的关键词都已命中，不需要调用模型。
        self.assertEqual(calls, [])
        self.assertEqual({r["source"] for r in results.values()}, {"rule"})

        self.results(PASS_CONTENT, judge)
        self.assertEqual(calls, [["R001", "R002"]])

    def test_judge_failure_keeps_rule_results(self):
        def broken(content, rules):
            raise llm.LLMUnavailable("网络不可用")
        baseline = self.results(PARAPHRASED, None)
        results = self.results(PARAPHRASED, broken)
        for rule_id, result in results.items():
            self.assertEqual(result["final_status"], baseline[rule_id]["final_status"])
            self.assertEqual(result["source"], "rule")
        self.assertIn("AI 兜底暂不可用", results["R001"]["message"])
        self.assertNotIn("AI 兜底", results["R003"]["message"])

    def test_unlocatable_quote_gives_no_evidence_but_keeps_judgement(self):
        judge = fake_judge({"R001": (True, "模型改写过的句子"), "R002": (False, ""), "R004": (True, "")})
        results = self.results(PARAPHRASED, judge)
        self.assertEqual(results["R001"]["final_status"], "risk")
        self.assertEqual(results["R001"]["evidence"], [])
        self.assertIn("未能在合同中逐字定位", results["R001"]["message"])

    def test_missing_judgement_for_a_rule_keeps_rule_result(self):
        results = self.results(PARAPHRASED, fake_judge({"R001": (True, "")}))
        self.assertEqual(results["R004"]["source"], "rule")
        self.assertIn("未返回该规则的判断", results["R004"]["message"])

    def test_invalid_llm_rule_fields_are_rejected(self):
        base = rule_engine.load_rules()[0]
        for changes in ({"llm_fallback": "yes"}, {"llm_question": ""}, {"llm_question": 3}):
            with self.subTest(changes=changes), self.assertRaises(rule_engine.RuleConfigError):
                rule_engine.validate_rules([{**base, **changes}])


class GetJudgeTests(unittest.TestCase):
    def test_mode_selection(self):
        for value, expected in (("", None), ("off", None), ("mock", llm.mock_judge),
                                ("Claude", llm.claude_judge), ("unknown", None)):
            with self.subTest(value=value), patch.dict(os.environ, {"LLM_FALLBACK": value}):
                self.assertIs(llm.get_judge(), expected)
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(llm.get_judge())


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.kwargs = response, error, None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


class ClaudeJudgeTests(unittest.TestCase):
    rules = [rule for rule in rule_engine.load_rules() if rule["id"] in ("R001", "R004")]

    def run_judge(self, messages, env=None):
        client = SimpleNamespace(messages=messages)
        with patch.object(anthropic, "Anthropic", return_value=client), patch.dict(os.environ, env or {}):
            return llm.claude_judge(PARAPHRASED, self.rules)

    def test_request_shape_and_parsed_output(self):
        batch = JudgementBatch(judgements=[
            RuleJudgement(rule_id="R001", found=True, quote="甲方有权在任何时候单方终止本协议", reason="单方终止"),
            RuleJudgement(rule_id="R999", found=True, quote="", reason="不在请求中的规则应被忽略"),
        ])
        messages = FakeMessages(SimpleNamespace(stop_reason="end_turn", parsed_output=batch))
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LLM_MODEL", None)
            judgements = self.run_judge(messages)
        self.assertEqual(set(judgements), {"R001"})
        self.assertEqual(messages.kwargs["model"], llm.DEFAULT_MODEL)
        self.assertIs(messages.kwargs["output_format"], JudgementBatch)
        prompt = messages.kwargs["messages"][0]["content"]
        self.assertIn(PARAPHRASED, prompt)
        self.assertIn("rule_id=R004", prompt)
        self.assertIn("<contract>", prompt)

    def test_model_can_be_overridden(self):
        messages = FakeMessages(SimpleNamespace(stop_reason="end_turn", parsed_output=JudgementBatch(judgements=[])))
        self.run_judge(messages, {"LLM_MODEL": "claude-sonnet-5-5"})
        self.assertEqual(messages.kwargs["model"], "claude-sonnet-5-5")

    def test_refusal_and_api_errors_become_unavailable(self):
        refusal = FakeMessages(SimpleNamespace(stop_reason="refusal", parsed_output=None))
        with self.assertRaises(llm.LLMUnavailable):
            self.run_judge(refusal)
        with self.assertRaises(llm.LLMUnavailable):
            self.run_judge(FakeMessages(error=anthropic.AnthropicError("no credentials")))


class LlmFallbackApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="contract-llm-tests-")
        self.db_path = Path(self.temp_dir.name) / "test.sqlite3"
        self.env = patch.dict(os.environ, {"CONTRACT_DB_PATH": str(self.db_path), "LLM_FALLBACK": "mock"})
        self.env.start()
        self.client = TestClient(importlib.import_module("main").app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.temp_dir.cleanup()

    def review(self, content):
        response = self.client.post("/review", json={"filename": "样例.txt", "content": content})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_mock_mode_labels_ai_results_without_changing_sample_outcomes(self):
        passed = self.review(PASS_CONTENT)
        self.assertEqual({r["final_status"] for r in passed["results"]}, {"pass"})
        sources = {r["rule_id"]: r["source"] for r in passed["results"]}
        self.assertEqual(sources, {"R001": "llm", "R002": "llm", "R003": "rule", "R004": "rule"})

        risky = self.review(RISK_CONTENT)
        self.assertEqual({r["rule_id"] for r in risky["results"] if r["final_status"] == "risk"}, {"R001", "R002"})

        report = self.client.post("/report", json={"review_id": passed["id"], "format": "markdown"})
        self.assertIn("判断来源：AI 判断，需人工复核", report.text)

    def test_reviews_saved_before_source_field_still_load(self):
        review = self.review(RISK_CONTENT)
        legacy = [{k: v for k, v in r.items() if k != "source"} for r in review["results"]]
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("UPDATE reviews SET results_json = ? WHERE id = ?",
                               (json.dumps(legacy, ensure_ascii=False), review["id"]))
            connection.commit()
        response = self.client.get(f"/reviews/{review['id']}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual({r["source"] for r in response.json()["results"]}, {"rule"})
        report = self.client.post("/report", json={"review_id": review["id"], "format": "markdown"})
        self.assertEqual(report.status_code, 200, report.text)
        self.assertIn("判断来源：规则匹配", report.text)


if __name__ == "__main__":
    unittest.main()
