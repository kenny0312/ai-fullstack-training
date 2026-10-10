"""配置驱动的演示规则引擎；新增同类型规则，只需编辑 rules.json。"""

import json
import logging
import re
from pathlib import Path

import llm


RULES_PATH = Path(__file__).with_name("rules.json")
MAX_EVIDENCE = 20
SNIPPET_CONTEXT = 35
AI_LABEL = "AI 判断，需人工复核"
_JUDGE_FROM_ENV = object()
logger = logging.getLogger(__name__)


class RuleConfigError(ValueError):
    """配置错误与用户输入错误分开处理，避免向客户端暴露内部路径。"""


def validate_rules(rules: object) -> list[dict]:
    if not isinstance(rules, list) or not 1 <= len(rules) <= 100:
        raise RuleConfigError("规则配置必须是包含 1 到 100 条规则的数组")
    seen_ids = set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise RuleConfigError("每条规则必须是对象")
        for field in ("id", "name", "description", "pattern"):
            value = rule.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > 2000:
                raise RuleConfigError(f"规则字段 {field} 必须是非空字符串，且不超过 2000 字符")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", rule["id"]):
            raise RuleConfigError("规则 id 只能包含字母、数字、下划线和连字符，最多 80 字符")
        if rule["id"] in seen_ids:
            raise RuleConfigError("规则 id 不能重复")
        seen_ids.add(rule["id"])
        if rule.get("type") not in ("keyword", "regex"):
            raise RuleConfigError("规则 type 只能是 keyword 或 regex")
        if rule.get("risk_when") not in ("matched", "missing"):
            raise RuleConfigError("规则 risk_when 只能是 matched 或 missing")
        if not isinstance(rule.get("llm_fallback", False), bool):
            raise RuleConfigError("规则 llm_fallback 只能是 true 或 false")
        question = rule.get("llm_question")
        if question is not None and (not isinstance(question, str) or not question.strip() or len(question) > 2000):
            raise RuleConfigError("规则 llm_question 必须是非空字符串，且不超过 2000 字符")
        if rule["type"] == "regex":
            try:
                compiled = re.compile(rule["pattern"])
            except re.error as error:
                raise RuleConfigError("规则包含无效正则表达式") from error
            if compiled.search("") is not None:
                raise RuleConfigError("规则正则不能匹配空字符串")
    return rules


def load_rules(path: str | Path | None = None) -> list[dict]:
    # 每次审查重新读取：修改配置后不必修改代码或重启后端。
    try:
        rules = json.loads(Path(path or RULES_PATH).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuleConfigError("无法读取规则配置") from error
    return validate_rules(rules)


def _evidence(content: str, start: int, end: int) -> dict:
    return {
        "text": content[start:end],
        "start": start,
        "end": end,
        "line": content.count("\n", 0, start) + 1,
        "snippet": content[max(0, start - SNIPPET_CONTEXT):min(len(content), end + SNIPPET_CONTEXT)],
    }


def _status(rule: dict, found: bool) -> str:
    is_risk = found if rule["risk_when"] == "matched" else not found
    return "risk" if is_risk else "pass"


def analyze_contract(content: str, rules: list[dict] | None = None, judge=_JUDGE_FROM_ENV) -> list[dict]:
    rules = load_rules() if rules is None else validate_rules(rules)
    if judge is _JUDGE_FROM_ENV:
        judge = llm.get_judge()
    results = []
    for rule in rules:
        # keyword 使用转义后的正则，确保其中的符号只被当作普通文字。
        pattern = re.escape(rule["pattern"]) if rule["type"] == "keyword" else rule["pattern"]
        evidence = []
        for match in re.finditer(pattern, content):
            # 零宽匹配没有可展示的原文，不作为合同证据。
            if match.start() == match.end():
                continue
            evidence.append(_evidence(content, *match.span()))
            if len(evidence) >= MAX_EVIDENCE:
                break

        matched = bool(evidence)
        status = _status(rule, matched)
        if rule["risk_when"] == "missing":
            message = "已找到规则要求的内容。" if matched else "未找到规则要求的内容，请人工复核。"
        else:
            message = "命中预设风险规则，请人工复核。" if matched else "未命中此项预设风险规则。"
        if len(evidence) == MAX_EVIDENCE:
            message += f" 证据最多展示前 {MAX_EVIDENCE} 处。"
        results.append({
            "rule_id": rule["id"],
            "name": rule["name"],
            "description": rule["description"],
            "original_status": status,
            "final_status": status,
            "evidence": evidence,
            "message": message,
            "source": "rule",
        })

    # AI 兜底：只处理开启了 llm_fallback 且关键词/正则未命中的规则，整份合同只调用一次模型。
    pending = [(rule, result) for rule, result in zip(rules, results)
               if rule.get("llm_fallback") and not result["evidence"]]
    if judge is None or not pending:
        return results
    try:
        judgements = judge(content, [rule for rule, _ in pending])
    except Exception as error:  # 模型故障不能让审查失败，回退到规则结果。
        logger.warning("AI 兜底调用失败：%s", error)
        for _, result in pending:
            result["message"] += " AI 兜底暂不可用，已按规则结果处理。"
        return results
    for rule, result in pending:
        judgement = judgements.get(rule["id"])
        if judgement is None:
            result["message"] += " AI 兜底未返回该规则的判断，已按规则结果处理。"
            continue
        quote = judgement.quote.strip()
        position = content.find(quote) if quote else -1
        status = _status(rule, judgement.found)
        result.update({
            "original_status": status,
            "final_status": status,
            "evidence": [_evidence(content, position, position + len(quote))] if position >= 0 else [],
            "message": f"{AI_LABEL}：{judgement.reason}" + (
                "" if position >= 0 or not quote else "（模型摘录的原文未能在合同中逐字定位）"),
            "source": "llm",
        })
    return results
