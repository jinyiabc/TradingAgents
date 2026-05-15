"""Pydantic request/response models for the web API."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

AnalystKind = Literal["market", "social", "news", "fundamentals"]


class CreateAnalysisRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    analysis_date: date
    analysts: list[AnalystKind] = Field(
        default=["market", "social", "news", "fundamentals"], min_length=1
    )
    llm_provider: str = "openai"
    deep_thinking_model: str | None = None
    quick_thinking_model: str | None = None
    max_debate_rounds: int = Field(default=1, ge=1, le=5)
    max_risk_discuss_rounds: int = Field(default=1, ge=1, le=5)
    output_language: str = "English"


class CreateAnalysisResponse(BaseModel):
    job_id: str


class JobSummary(BaseModel):
    job_id: str
    ticker: str
    analysis_date: str
    status: str
    created_at: str
    finished_at: str | None = None


class JobDetail(JobSummary):
    current_step: str | None = None
    progress_pct: int | None = None
    error: str | None = None
    report_url: str | None = None
    started_at: str | None = None
    config: dict[str, Any]


class OptionsResponse(BaseModel):
    """Frontend-facing dropdown data sourced from MODEL_OPTIONS."""

    providers: list[str]
    models: dict[str, dict[str, list[dict[str, str]]]]  # provider -> mode -> [{label, value}]
    analysts: list[str]
