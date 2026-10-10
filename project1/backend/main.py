from contextlib import asynccontextmanager
import html
import json
import logging
import re
import sqlite3

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from fastapi.middleware.cors import CORSMiddleware
from typing import Literal
from analyzer import analyze_event
from rule_engine import RuleConfigError, analyze_contract
from schemas import ReportRequest, Review, ReviewList, ReviewRequest, UpdateResultRequest
import storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.init_db()
    yield


app = FastAPI(title="Contract Review Mini", lifespan=lifespan)


"""
加入cors中间件，告诉浏览器：允许哪个来源的网页访问这个后端
"""
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["POST", "GET", "PATCH"],
    allow_headers=["Content-Type"],
)

class EchoRequest(BaseModel):
    name:str

class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=2000)


class AnalyzeResponse(BaseModel):
    risk_level: Literal["low", "medium", "high"]
    summary: str
    tags: list[str]



@app.post("/api/echo")
def echo(data: EchoRequest):
    return  {"message": f"hello {data.name}"} 


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/analyze-preview", response_model=AnalyzeResponse)
def analyze_preview(data: AnalyzeRequest):
    return analyze_event(data.content)


@app.exception_handler(sqlite3.Error)
async def database_error_handler(request, error):
    logging.getLogger(__name__).error("数据库操作失败：%s", type(error).__name__)
    return JSONResponse(status_code=503, content={"detail": "数据库暂时不可用，请稍后重试"})


@app.post("/review", response_model=Review)
def review_contract(data: ReviewRequest):
    try:
        results = analyze_contract(data.content)
    except RuleConfigError:
        raise HTTPException(status_code=503, detail="规则配置不可用，请检查后端 rules.json") from None
    return storage.create_review(data.filename, data.content, results)


@app.get("/reviews", response_model=ReviewList)
def review_history():
    return {"items": storage.list_reviews()}


def require_review(review_id: str) -> dict:
    try:
        return storage.get_review(review_id)
    except storage.ReviewNotFound:
        raise HTTPException(status_code=404, detail="审查记录不存在") from None


@app.get("/reviews/{review_id}", response_model=Review)
def review_detail(review_id: str):
    return require_review(review_id)


@app.patch("/reviews/{review_id}/results/{rule_id}", response_model=Review)
def change_result(review_id: str, rule_id: str, data: UpdateResultRequest):
    try:
        return storage.update_result(review_id, rule_id, **data.model_dump())
    except (storage.ReviewNotFound, storage.ResultNotFound):
        raise HTTPException(status_code=404, detail="审查记录或规则结果不存在") from None
    except storage.StaleStatus as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except storage.NoStatusChange as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


DISCLAIMER = "本工具仅用于编程训练。规则匹配结果不构成法律意见；未命中规则不代表合同安全或有效。人工操作人由使用者填写，未经身份认证。"
STATUS_LABELS = {"pass": "通过", "risk": "风险", "not_applicable": "不适用"}


def markdown_text(value: str) -> str:
    """用户输入只作为文字导出，不能变成 Markdown 链接、HTML 或新段落。"""
    text = html.escape(value, quote=True).replace("\r", " ").replace("\n", " / ")
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~\-])", r"\\\1", text)


def markdown_code(value: str) -> str:
    # 围栏比原文中最长的反引号序列更长，原文就无法提前关闭代码块。
    longest = max((len(run) for run in re.findall(r"`+", value)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{value}\n{fence}"


def build_markdown_report(review: dict, exported_at: str) -> str:
    lines = [
        "# 合同规则审查报告", "", DISCLAIMER, "",
        f"- 审查编号：{review['id']}",
        f"- 文件名：{markdown_text(review['filename'])}",
        f"- 创建时间（UTC）：{review['created_at']}",
        f"- 最近修改（UTC）：{review['updated_at']}",
        f"- 导出时间（UTC）：{exported_at}", "", "## 规则结果", "",
    ]
    for result in review["results"]:
        lines += [
            f"### {markdown_text(result['rule_id'])} · {markdown_text(result['name'])}", "",
            f"- 规则说明：{markdown_text(result['description'])}",
            f"- 原始状态：{STATUS_LABELS[result['original_status']]}",
            f"- 最终状态：{STATUS_LABELS[result['final_status']]}",
            f"- 判断说明：{markdown_text(result['message'])}", "",
        ]
        if not result["evidence"]:
            lines += ["无命中证据；缺失类风险表示未找到要求的内容。", ""]
        for evidence in result["evidence"]:
            lines += [
                f"证据：第 {evidence['line']} 行，字符区间 [{evidence['start']}, {evidence['end']})。", "",
                "命中文本：", "", markdown_code(evidence["text"]), "",
                "上下文片段：", "", markdown_code(evidence["snippet"]), "",
            ]
    lines += ["## 人工修改审计", ""]
    if not review["audit"]:
        lines += ["尚无人工修改。", ""]
    for entry in review["audit"]:
        lines += [
            f"### 修改 #{entry['id']}", "",
            f"- 规则：{markdown_text(entry['rule_id'])} · {markdown_text(entry['rule_name'])}",
            f"- 操作人：{markdown_text(entry['actor'])}",
            f"- 时间（UTC）：{entry['created_at']}",
            f"- 修改：{STATUS_LABELS[entry['old_status']]} → {STATUS_LABELS[entry['new_status']]}",
            f"- 原因：{markdown_text(entry['reason'])}", "",
        ]
    lines += ["## 合同原文", "", markdown_code(review["content"]), ""]
    return "\n".join(lines)


@app.post("/report")
def export_report(data: ReportRequest):
    # 只接受审查 ID，绝不信任浏览器上传的结果或审计副本。
    review = require_review(data.review_id)
    exported_at = storage.utc_now()
    if data.format == "json":
        content = json.dumps({**review, "exported_at": exported_at, "disclaimer": DISCLAIMER},
                             ensure_ascii=False, indent=2)
        media_type, extension = "application/json", "json"
    else:
        content = build_markdown_report(review, exported_at)
        media_type, extension = "text/markdown", "md"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="review-{review["id"]}.{extension}"'},
    )
