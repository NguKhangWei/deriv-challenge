"""Data contracts shared by every pipeline stage."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Intent(str, Enum):
    DEPOSIT_ISSUE = "deposit_issue"
    PASSWORD_RESET = "password_reset"
    WITHDRAWAL_ISSUE = "withdrawal_issue"
    ACCOUNT_LOCK = "account_lock"
    TRADING_ADVICE_REQUEST = "trading_advice_request"
    OTHER = "other"


class SafetyFlag(str, Enum):
    ADVICE_REQUEST = "advice_request"
    INSUFFICIENT_GROUNDING = "insufficient_grounding"
    POLICY_SENSITIVE = "policy_sensitive"
    LOW_CONFIDENCE = "low_confidence"
    ACCOUNT_SPECIFIC_REQUEST = "account_specific_request"
    CONFLICTING_SIGNALS = "conflicting_signals"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    OUT_OF_SCOPE = "out_of_scope"
    PROMPT_INJECTION_SUSPECTED = "prompt_injection_suspected"
    INVALID_INPUT = "invalid_input"
    INPUT_TRUNCATED = "input_truncated"
    VALIDATION_FAILED = "validation_failed"
    THIRD_PARTY_REQUEST = "third_party_request"
    SENSITIVE_DATA_SHARED = "sensitive_data_shared"


# --- Input records -----------------------------------------------------------

class Ticket(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    message: str
    language: str | None = "en"


class Article(BaseModel):
    model_config = ConfigDict(extra="ignore")

    article_id: str = Field(min_length=1)
    title: str
    body: str


# --- Output record (the required schema) -------------------------------------

class Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str = Field(min_length=1)
    intent: Intent
    retrieved_articles: list[str] = Field(max_length=3)
    reply_draft: str = Field(min_length=1)
    grounded: bool
    confidence: float = Field(ge=0.0, le=1.0)
    needs_human_escalation: bool
    safety_flags: list[SafetyFlag]


# --- Internal stage outputs --------------------------------------------------

class Route(str, Enum):
    """How the reply is produced. Only GROUNDED may use a model-written draft."""

    GROUNDED = "grounded"        # KB-backed agent notes
    REFUSAL = "refusal"          # trading-advice refusal (fixed, policy-backed)
    FALLBACK = "fallback"        # in-domain, but nothing relevant in the KB
    REJECT = "reject"            # off-topic, injection, or empty input


@dataclass(frozen=True)
class Screening:
    """Signals derived from the raw ticket before classification."""

    text: str                      # possibly truncated message used downstream
    empty: bool
    truncated: bool
    unsupported_language: bool
    injection: bool
    account_specific: bool
    in_domain: bool
    third_party: bool = False
    sensitive_data: bool = False
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Classification:
    intent: Intent
    certainty: float               # 0..1, how sure the classifier is
    scores: dict[str, float]
    conflicting: bool
    source: str                    # "rules" | "rules:advice" | "llm"
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Hit:
    article_id: str
    title: str
    score: float


@dataclass(frozen=True)
class DraftLine:
    text: str
    sources: tuple[str, ...]       # article IDs; empty for vetted template text
    kind: str                      # "guidance" | "caution" | "policy" | "framing"


@dataclass
class Draft:
    route: Route
    summary: str
    lines: list[DraftLine]
    suggested_reply: str | None = None
    generator: str = "extractive"
    articles: list[str] = field(default_factory=list)   # articles the draft is built on
    notes: list[str] = field(default_factory=list)      # debug notes (e.g. LLM fallback)

    @property
    def claim_lines(self) -> list[DraftLine]:
        return [line for line in self.lines if line.kind != "framing"]
