"""VALIDATE_OUTPUT: independent checks on each final result before it is written.

A result that fails any check is replaced by a safe, escalated fallback; the errors are
kept for the debug report. One bad ticket never stops the run.
"""
from __future__ import annotations

from pydantic import ValidationError

from .classify import is_advice_request
from .generate import REFUSAL_MARKER
from .guard import UNSUPPORTED_CLAIM_PATTERNS, find_unsupported_claims
from .models import Intent, Result, SafetyFlag

SAFE_FALLBACK_TEXT = (
    "Summary: The automated draft failed validation and was withheld.\n"
    "Next step: escalate to a human agent (validation failed)."
)


def validate_result(
    data: dict,
    *,
    known_article_ids: set[str],
    ticket_text: str,
    source_text: str,
    security_reject: bool = False,
) -> list[str]:
    errors: list[str] = []
    try:
        result = Result.model_validate(data)
    except ValidationError as exc:
        return [f"schema: {e['loc']}: {e['msg']}" for e in exc.errors()]

    ids = result.retrieved_articles
    if unknown := [a for a in ids if a not in known_article_ids]:
        errors.append(f"unknown article ids: {unknown}")
    if len(set(ids)) != len(ids):
        errors.append("duplicate article ids")
    if not ids and result.grounded:
        errors.append("grounded=true without any retrieved article")
    if not result.grounded and not (result.safety_flags or result.needs_human_escalation):
        errors.append("ungrounded result carries neither a safety flag nor escalation")

    if is_advice_request(ticket_text) and not security_reject and result.intent is not Intent.TRADING_ADVICE_REQUEST:
        errors.append("advice request was not classified as trading_advice_request")
    if result.intent is Intent.TRADING_ADVICE_REQUEST:
        if REFUSAL_MARKER not in result.reply_draft.lower():
            errors.append("trading advice request is not refused")
        if SafetyFlag.ADVICE_REQUEST not in result.safety_flags:
            errors.append("trading advice request lacks the advice_request flag")
        if UNSUPPORTED_CLAIM_PATTERNS["market_prediction"].search(result.reply_draft):
            errors.append("refusal contains a market prediction")

    if claims := find_unsupported_claims(result.reply_draft, source_text):
        if SafetyFlag.UNSUPPORTED_CLAIM not in result.safety_flags or not result.needs_human_escalation:
            errors.append(f"unsupported claims without flag + escalation: {claims}")
    return errors


def safe_fallback(ticket_id: str) -> Result:
    return Result(
        ticket_id=ticket_id,
        intent=Intent.OTHER,
        retrieved_articles=[],
        reply_draft=SAFE_FALLBACK_TEXT,
        grounded=False,
        confidence=0.0,
        needs_human_escalation=True,
        safety_flags=[SafetyFlag.VALIDATION_FAILED, SafetyFlag.INSUFFICIENT_GROUNDING],
    )
