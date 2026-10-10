r"""Contract-review regression tests; every test uses its own disposable SQLite DB.

Run from backend: .\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
"""

import importlib
import json
import os
import sqlite3
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timedelta
from contextlib import closing
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PASS_CONTENT = (PROJECT_ROOT / "samples" / "pass.txt").read_text(encoding="utf-8")
RISK_CONTENT = (PROJECT_ROOT / "samples" / "risk.txt").read_text(encoding="utf-8")


class ContractApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="eventinsight-contract-tests-")
        self.old_db_path = os.environ.get("CONTRACT_DB_PATH")
        self.db_path = Path(self.temp_dir.name) / "test.sqlite3"
        os.environ["CONTRACT_DB_PATH"] = str(self.db_path)
        # Import only after pointing persistence at a disposable location.
        self.app = importlib.import_module("main").app
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if self.old_db_path is None:
            os.environ.pop("CONTRACT_DB_PATH", None)
        else:
            os.environ["CONTRACT_DB_PATH"] = self.old_db_path
        self.temp_dir.cleanup()

    def create_review(self, content=PASS_CONTENT, filename="示例合同.txt"):
        response = self.client.post("/review", json={"filename": filename, "content": content})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def update(self, review_id, rule_id="R001", **overrides):
        payload = {
            "status": "pass",
            "actor": "测试复核员",
            "reason": "已与双方确认该条款的适用范围。",
            "expected_status": "risk",
        }
        payload.update(overrides)
        return self.client.patch(f"/reviews/{review_id}/results/{rule_id}", json=payload)

    def assert_utc(self, value):
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        self.assertIsNotNone(timestamp.tzinfo)
        self.assertEqual(timestamp.utcoffset(), timedelta(0))

    @staticmethod
    def result_map(review):
        return {result["rule_id"]: result for result in review["results"]}

    def test_existing_training_endpoints_remain_available(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        response = self.client.post("/api/echo", json={"name": "小明"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"message": "hello 小明"})
        response = self.client.post("/api/analyze-preview", json={"content": "事故引发投诉"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["risk_level"], "high")
        self.assertEqual(response.json()["tags"], ["事故", "投诉"])

    def test_pass_sample_has_four_passing_rules(self):
        review = self.create_review()
        self.assertEqual(review["content"], PASS_CONTENT)
        self.assertEqual(review["filename"], "示例合同.txt")
        self.assertEqual(len(review["results"]), 4)
        self.assertEqual({r["original_status"] for r in review["results"]}, {"pass"})
        self.assertEqual({r["final_status"] for r in review["results"]}, {"pass"})
        self.assertEqual(review["audit"], [])
        self.assert_utc(review["created_at"])
        self.assert_utc(review["updated_at"])

    def test_risk_sample_has_exactly_two_risks(self):
        review = self.create_review(RISK_CONTENT)
        risk_ids = {r["rule_id"] for r in review["results"] if r["final_status"] == "risk"}
        self.assertEqual(risk_ids, {"R001", "R002"})

    def test_review_input_validation(self):
        for payload in ({}, {"content": ""}, {"content": " \n\t "}, {"content": "字" * 100001},
                        {"content": PASS_CONTENT, "filename": "x" * 201}):
            with self.subTest(payload_keys=list(payload), content_length=len(payload.get("content", ""))):
                response = self.client.post("/review", json=payload)
                self.assertEqual(response.status_code, 422, response.text)
        response = self.client.post("/review", json={"content": PASS_CONTENT})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["filename"], "合同.txt")

    def test_evidence_offsets_keep_unicode_and_original_whitespace(self):
        content = "  \n😀合同标题\n甲方可随时解除合同。\n不承担任何责任。\n争议解决：协商。\n  "
        review = self.create_review(content)
        self.assertEqual(review["content"], content)
        evidence_count = 0
        for result in review["results"]:
            for evidence in result["evidence"]:
                evidence_count += 1
                start, end = evidence["start"], evidence["end"]
                self.assertGreaterEqual(start, 0)
                self.assertGreater(end, start)
                self.assertEqual(content[start:end], evidence["text"])
                self.assertEqual(evidence["line"], content[:start].count("\n") + 1)
                self.assertIn(evidence["text"], evidence["snippet"])
        self.assertGreaterEqual(evidence_count, 2)

    def test_missing_and_regex_rules(self):
        review = self.create_review("合同金额：____ 元；签署日期：待填写。")
        results = self.result_map(review)
        self.assertEqual(results["R003"]["final_status"], "risk")
        self.assertEqual(results["R004"]["final_status"], "risk")
        self.assertEqual(results["R004"]["evidence"], [])
        self.assertTrue(results["R004"]["message"])
        self.assertTrue(any(e["text"] in {"____", "待填写"} for e in results["R003"]["evidence"]))

    def test_unknown_resources_return_404(self):
        missing_id = str(uuid4())
        self.assertEqual(self.client.get(f"/reviews/{missing_id}").status_code, 404)
        self.assertEqual(self.update(missing_id).status_code, 404)
        review = self.create_review()
        self.assertEqual(self.update(review["id"], rule_id="UNKNOWN").status_code, 404)
        response = self.client.post("/report", json={"review_id": missing_id, "format": "json"})
        self.assertEqual(response.status_code, 404)

    def test_manual_update_preserves_original_and_saves_audit(self):
        review = self.create_review(RISK_CONTENT)
        response = self.update(review["id"])
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        result = self.result_map(updated)["R001"]
        self.assertEqual(result["original_status"], "risk")
        self.assertEqual(result["final_status"], "pass")
        self.assertEqual(len(updated["audit"]), 1)
        audit = updated["audit"][0]
        self.assertIsInstance(audit["id"], int)
        self.assertEqual(audit["rule_id"], "R001")
        self.assertEqual(audit["rule_name"], result["name"])
        self.assertEqual(audit["actor"], "测试复核员")
        self.assertEqual(audit["reason"], "已与双方确认该条款的适用范围。")
        self.assertEqual((audit["old_status"], audit["new_status"]), ("risk", "pass"))
        self.assert_utc(audit["created_at"])
        self.assertEqual(self.client.get(f"/reviews/{review['id']}").json(), updated)

    def test_all_manual_statuses_and_repeated_audit_history(self):
        review = self.create_review(RISK_CONTENT)
        response = self.update(review["id"], status="not_applicable")
        self.assertEqual(response.status_code, 200, response.text)
        response = self.update(review["id"], status="risk", expected_status="not_applicable", reason="补充材料后重新评估。")
        self.assertEqual(response.status_code, 200, response.text)
        response = self.update(review["id"], status="pass", expected_status="risk")
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        self.assertEqual(len(updated["audit"]), 3)
        self.assertEqual(self.result_map(updated)["R001"]["original_status"], "risk")
        transitions = {(a["old_status"], a["new_status"]) for a in updated["audit"]}
        self.assertEqual(transitions, {("risk", "not_applicable"), ("not_applicable", "risk"), ("risk", "pass")})

    def test_invalid_update_inputs_do_not_write_audit(self):
        review = self.create_review(RISK_CONTENT)
        base = {"status": "pass", "actor": "操作人", "reason": "复核原因", "expected_status": "risk"}
        invalid_payloads = []
        for key in ("actor", "reason", "expected_status"):
            payload = base.copy()
            payload.pop(key)
            invalid_payloads.append(payload)
        for changes in ({"actor": "  "}, {"reason": " \n "}, {"actor": "字" * 81},
                        {"reason": "字" * 1001}, {"status": "approved"}):
            invalid_payloads.append({**base, **changes})
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.patch(f"/reviews/{review['id']}/results/R001", json=payload)
                self.assertEqual(response.status_code, 422, response.text)
        current = self.client.get(f"/reviews/{review['id']}").json()
        self.assertEqual(current["audit"], [])
        self.assertEqual(self.result_map(current)["R001"]["final_status"], "risk")

    def test_unchanged_and_stale_updates_do_not_write_extra_audit(self):
        review = self.create_review(RISK_CONTENT)
        response = self.update(review["id"], status="risk")
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.update(review["id"]).status_code, 200)
        response = self.update(review["id"], status="not_applicable", expected_status="risk")
        self.assertEqual(response.status_code, 409, response.text)
        current = self.client.get(f"/reviews/{review['id']}").json()
        self.assertEqual(len(current["audit"]), 1)
        self.assertEqual(self.result_map(current)["R001"]["final_status"], "pass")

    def test_saved_changes_survive_application_restart(self):
        review = self.create_review(RISK_CONTENT)
        self.assertEqual(self.update(review["id"]).status_code, 200)
        self.assertTrue(self.db_path.exists())
        self.client.__exit__(None, None, None)
        self.client = TestClient(self.app)
        self.client.__enter__()
        response = self.client.get(f"/reviews/{review['id']}")
        self.assertEqual(response.status_code, 200, response.text)
        persisted = response.json()
        self.assertEqual(self.result_map(persisted)["R001"]["final_status"], "pass")
        self.assertEqual(len(persisted["audit"]), 1)

    def test_history_lists_latest_reviews_and_current_risk_count(self):
        first = self.create_review(PASS_CONTENT, "先创建.txt")
        second = self.create_review(RISK_CONTENT, "后创建.txt")
        self.assertEqual(self.update(second["id"]).status_code, 200)
        response = self.client.get("/reviews")
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["items"]
        self.assertEqual([item["id"] for item in items], [second["id"], first["id"]])
        self.assertEqual(items[0]["risk_count"], 1)
        self.assertEqual(items[0]["total_count"], 4)
        self.assertEqual(items[1]["risk_count"], 0)

    def test_json_report_uses_persisted_results_and_audit(self):
        review = self.create_review(RISK_CONTENT)
        self.assertEqual(self.update(review["id"]).status_code, 200)
        response = self.client.post("/report", json={"review_id": review["id"], "format": "json"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("attachment", response.headers.get("content-disposition", ""))
        self.assertIn("application/json", response.headers.get("content-type", ""))
        report = response.json()
        self.assertEqual(report["id"], review["id"])
        self.assertEqual(report["content"], RISK_CONTENT)
        self.assertEqual(self.result_map(report)["R001"]["original_status"], "risk")
        self.assertEqual(self.result_map(report)["R001"]["final_status"], "pass")
        self.assertEqual(len(report["audit"]), 1)
        self.assertEqual(report["audit"][0]["actor"], "测试复核员")

    def test_markdown_report_and_invalid_format(self):
        review = self.create_review(RISK_CONTENT)
        self.assertEqual(self.update(review["id"]).status_code, 200)
        response = self.client.post("/report", json={"review_id": review["id"], "format": "markdown"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("attachment", response.headers.get("content-disposition", ""))
        self.assertIn("测试复核员", response.text)
        self.assertIn("已与双方确认该条款的适用范围。", response.text)
        self.assertIn("甲方可随时解除合同", response.text)
        self.assertIn("原始", response.text)
        self.assertIn("最终", response.text)
        self.assertIn("审计", response.text)
        response = self.client.post("/report", json={"review_id": review["id"], "format": "pdf"})
        self.assertEqual(response.status_code, 422, response.text)

    def test_rules_can_be_extended_with_configuration_only(self):
        engine = importlib.import_module("rule_engine")
        rules = engine.load_rules()
        self.assertGreaterEqual(len(rules), 4)
        extra_rule = {
            "id": "R005", "name": "新增配置规则", "description": "无需改动 Python 代码",
            "type": "keyword", "pattern": "禁止转让", "risk_when": "matched",
        }
        config_path = Path(self.temp_dir.name) / "extended_rules.json"
        config_path.write_text(json.dumps([*rules, extra_rule], ensure_ascii=False), encoding="utf-8")
        extended_rules = engine.load_rules(config_path)
        results = engine.analyze_contract("争议解决：协商。未经同意禁止转让。", rules=extended_rules)
        result = next(result for result in results if result["rule_id"] == "R005")
        self.assertEqual(result["original_status"], "risk")
        self.assertEqual(result["evidence"][0]["text"], "禁止转让")

    def test_cors_allows_manual_update_from_frontend(self):
        response = self.client.options("/reviews/demo/results/R001", headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:5173")
        self.assertIn("PATCH", response.headers["access-control-allow-methods"])

    def test_failed_audit_insert_rolls_back_status_update(self):
        review = self.create_review(RISK_CONTENT)
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("""CREATE TRIGGER test_block_audit BEFORE INSERT ON audit
                BEGIN SELECT RAISE(ABORT, 'simulated audit write failure'); END""")
            connection.commit()
        response = self.update(review["id"])
        self.assertEqual(response.status_code, 503, response.text)
        current = self.client.get(f"/reviews/{review['id']}").json()
        self.assertEqual(current, review)

    def test_bad_rule_config_returns_safe_error_without_saving_review(self):
        engine = importlib.import_module("rule_engine")
        with patch("main.analyze_contract", side_effect=engine.RuleConfigError("private/path")):
            response = self.client.post("/review", json={"content": PASS_CONTENT})
        self.assertEqual(response.status_code, 503, response.text)
        self.assertNotIn("private/path", response.text)
        self.assertEqual(self.client.get("/reviews").json()["items"], [])

    def test_history_is_bounded_to_twenty_latest_items(self):
        reviews = [self.create_review(filename=f"样例-{number}.txt") for number in range(22)]
        items = self.client.get("/reviews").json()["items"]
        self.assertEqual(len(items), 20)
        self.assertEqual([item["id"] for item in items], [review["id"] for review in reversed(reviews[-20:])])

    def test_report_preserves_literal_text_without_markdown_injection(self):
        content = RISK_CONTENT + "\n```\n<script>alert('demo')</script>\n"
        review = self.create_review(content, filename="[合同](https://example.invalid).txt")
        self.assertEqual(self.update(review["id"], reason="<b>测试</b>\n[外部](https://example.invalid)").status_code, 200)
        response = self.client.post("/report", json={"review_id": review["id"], "format": "markdown"})
        self.assertIn("````text\n" + content + "\n````", response.text)
        self.assertNotIn("- 文件名：[合同](", response.text)
        self.assertNotIn("- 原因：<b>", response.text)
        report = self.client.post("/report", json={"review_id": review["id"], "format": "json"}).json()
        self.assertEqual(report["content"], content)

    def test_invalid_rule_definitions_are_rejected(self):
        engine = importlib.import_module("rule_engine")
        base = engine.load_rules()[0]
        cases = [[], [base, base], [{**base, "type": "unknown"}],
                 [{**base, "type": "regex", "pattern": "["}],
                 [{**base, "type": "regex", "pattern": ".*"}]]
        for rules in cases:
            with self.subTest(rules=rules), self.assertRaises(engine.RuleConfigError):
                engine.analyze_contract(PASS_CONTENT, rules=rules)


if __name__ == "__main__":
    unittest.main()
