"""Public API contracts. Keep these separate from database models and graph state."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ShopRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=500)
    num_items: int = Field(default=5, ge=1, le=10)
    category: str | None = None
    max_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=128)
    retrieval_mode: Literal["bm25", "vector", "hybrid"] | None = None

    @field_validator("user_id", "query", "category", "conversation_id")
    @classmethod
    def _strip_nonempty_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must contain non-whitespace text")
        return stripped


class EvidenceRef(BaseModel):
    source_id: str
    source_type: str
    excerpt: str


class ConditionJudgment(BaseModel):
    """One explicit hard condition checked against current catalog evidence."""

    field: str
    operator: Literal["=", "!=", ">", ">=", "<", "<=", "present", "absent"]
    expected: str | float
    unit: str | None = None
    status: Literal["supported", "refuted", "unknown"]
    evidence: list[EvidenceRef] = Field(default_factory=list)
    conflict: bool = False
    explanation: str = ""


class Recommendation(BaseModel):
    product_id: str
    name: str
    category: str
    price: float
    stock: int
    reason: str
    evidence: list[EvidenceRef] = Field(default_factory=list)
    condition_judgments: list[ConditionJudgment] = Field(default_factory=list)


class ShopResponse(BaseModel):
    request_id: str
    user_id: str
    answer: str
    recommendations: list[Recommendation] = Field(default_factory=list)
    retrieval_used: bool = False
    llm_used: bool = False
    warnings: list[str] = Field(default_factory=list)
    total_latency_ms: float = 0.0
    route: Literal["product", "faq", "mixed"] = "product"
    tool_trace: list[str] = Field(default_factory=list)
    retrieval_mode: Literal["bm25", "vector", "hybrid"] | None = None
    requested_retrieval_mode: Literal["bm25", "vector", "hybrid"] = "bm25"
    effective_retrieval_modes: dict[str, str] = Field(default_factory=dict)
    conversation_id: str | None = None
    experiment_id: str | None = None
    variant: str | None = None
    routing_reason: str = ""
    knowledge_evidence: list[EvidenceRef] = Field(default_factory=list)


class EventRequest(BaseModel):
    user_id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    event_type: Literal["view", "click", "purchase"]
    request_id: str | None = None

    @field_validator("user_id", "product_id")
    @classmethod
    def _strip_nonempty_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must contain non-whitespace text")
        return stripped

    @field_validator("request_id")
    @classmethod
    def _strip_request_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("request_id must contain non-whitespace text")
        return value.strip()
