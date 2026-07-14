"""Pydantic models for blog search agent output validation."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

EVENT_TYPES = [
    "INJURY",
    "CANCELLATION",
    "VENUE_CHANGE",
    "SCHEDULE_CHANGE",
    "ROSTER",
]

RETRIEVAL_METHODS = [
    "rss",
    "web_search",
    "web_fetch",
    "web_search + web_fetch",
    "rss + web_fetch",
]


class EventResult(BaseModel):
    sport: str
    team: str
    event_type: str
    player_name: Optional[str] = None
    excerpt: str
    summary: str
    source_url: str
    blog_post_date: Optional[str] = None
    detected_at: str
    retrieval_method: str

    @field_validator("event_type")
    @classmethod
    def event_type_must_be_valid(cls, v: str) -> str:
        if v.upper() not in EVENT_TYPES:
            raise ValueError(
                f"event_type must be one of {EVENT_TYPES}, got '{v}'"
            )
        return v.upper()

    @field_validator("retrieval_method")
    @classmethod
    def retrieval_method_must_be_valid(cls, v: str) -> str:
        if v not in RETRIEVAL_METHODS:
            raise ValueError(
                f"retrieval_method must be one of {RETRIEVAL_METHODS}, got '{v}'"
            )
        return v


class RetrievalDiagnostics(BaseModel):
    model_config = {"extra": "allow"}

    urls_total: int = Field(ge=0)
    urls_with_rss: int = Field(ge=0)
    urls_without_rss: int = Field(ge=0)
    web_searches_performed: int = Field(ge=0)
    rss_feeds_fetched: int = Field(ge=0)
    web_fetches_performed: int = Field(ge=0)
    web_search_queries: Optional[List[str]] = None
    events_found_via_rss: Optional[bool] = None
    events_found_via_web_search: Optional[bool] = None


class BlogSearchResponse(BaseModel):
    results: List[EventResult] = Field(default_factory=list)
    retrieval_diagnostics: RetrievalDiagnostics


def validate_response(parsed: dict):
    """Validate parsed JSON against the schema.

    Returns the validated BlogSearchResponse on success,
    or a list of human-readable error strings on failure.
    """
    try:
        return BlogSearchResponse.model_validate(parsed)
    except Exception as e:
        errors = []
        if hasattr(e, "errors"):
            for err in e.errors():
                loc = " -> ".join(str(x) for x in err["loc"])
                errors.append(f"{loc}: {err['msg']}")
        else:
            errors.append(str(e))
        return errors
