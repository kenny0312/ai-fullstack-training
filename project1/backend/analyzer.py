
def analyze_event(content: str) -> dict:
    keywords = ["事故", "召回", "投诉", "质量"]
    tags = []

    for word in keywords:
        if word in content:
            tags.append(word)

    if "事故" in tags or "召回" in tags:
        risk_level = "high"
        summary = "命中事故或召回关键词，建议人工复核。"
    elif "投诉" in tags or "质量" in tags:
        risk_level = "medium"
        summary = "命中投诉或质量关键词，建议关注。"
    else:
        risk_level = "low"
        summary = "未命中预设关键词，仅供演示。"

    return {
        "risk_level": risk_level,
        "summary": summary,
        "tags": tags,
    }
    