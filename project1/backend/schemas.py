"""接口的输入和输出格式：Pydantic 会自动校验请求，并生成接口文档。"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


Status = Literal["pass", "risk", "not_applicable"]


class ReviewRequest(BaseModel):
    filename: str = Field(default="合同.txt", min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100000)

    @field_validator("filename", mode="before")
    @classmethod
    def clean_filename(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("content")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("合同内容不能为空白")
        # 不能 strip 返回值，否则证据位置就不再对应用户上传的原文。
        return value


class Evidence(BaseModel):
    text: str
    start: int
    end: int
    line: int
    snippet: str


class RuleResult(BaseModel):
    rule_id: str
    name: str
    description: str
    original_status: Status
    final_status: Status
    evidence: list[Evidence]
    message: str


class AuditEntry(BaseModel):
    id: int
    rule_id: str
    rule_name: str
    actor: str
    reason: str
    old_status: Status
    new_status: Status
    created_at: str


class Review(BaseModel):
    id: str
    filename: str
    content: str
    created_at: str
    updated_at: str
    results: list[RuleResult]
    audit: list[AuditEntry]


class ReviewSummary(BaseModel):
    id: str
    filename: str
    created_at: str
    updated_at: str
    risk_count: int
    total_count: int


class ReviewList(BaseModel):
    items: list[ReviewSummary]


class UpdateResultRequest(BaseModel):
    status: Status
    expected_status: Status
    actor: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("actor", "reason", mode="before")
    @classmethod
    def clean_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class ReportRequest(BaseModel):
    review_id: str = Field(min_length=1, max_length=100)
    format: Literal["json", "markdown"]
