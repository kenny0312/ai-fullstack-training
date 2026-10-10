"""配置驱动的演示规则引擎；新增同类型规则，只需编辑 rules.json。"""

import json
import re
from pathlib import Path


RULES_PATH = Path(__file__).with_name("rules.json")
MAX_EVIDENCE = 20


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


def analyze_contract(content: str, rules: list[dict] | None = None) -> list[dict]:
    rules = load_rules() if rules is None else validate_rules(rules)
    results = []
    for rule in rules:
        # keyword 使用转义后的正则，确保其中的符号只被当作普通文字。
        pattern = re.escape(rule["pattern"]) if rule["type"] == "keyword" else rule["pattern"]
        evidence = []
        for match in re.finditer(pattern, content):
            # 零宽匹配没有可展示的原文，不作为合同证据。
            if match.start() == match.end():
                continue
            start, end = match.span()
            evidence.append({
                "text": match.group(),
                "start": start,
                "end": end,
                "line": content.count("\n", 0, start) + 1,
                "snippet": content[max(0, start - 35):min(len(content), end + 35)],
            })
            if len(evidence) >= MAX_EVIDENCE:
                break

        matched = bool(evidence)
        is_risk = matched if rule["risk_when"] == "matched" else not matched
        status = "risk" if is_risk else "pass"
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
        })
    return results
