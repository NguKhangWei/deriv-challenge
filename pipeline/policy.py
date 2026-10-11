"""Routing, confidence and escalation rules, kept in one readable place."""
from __future__ import annotations

from dataclasses import dataclass, field

from .classify import MIN_INTENT_SCORE
from .guard import GroundingReport
from .models import Classification, Hit, Intent, Route, SafetyFlag, Screening

# Confidence for grounded answers is a weighted blend of independent signals.
WEIGHTS = {"classifier": 0.35, "retrieval": 0.30, "agreement": 0.20, "grounding": 0.15}
RETRIEVAL_SCALE = 2.0            # BM25 score s maps to s / (s + scale)
ESCALATE_BELOW = 0.55
POLICY_SENSITIVE_INTENTS = frozenset({Intent.WITHDRAWAL_ISSUE})
REFUSAL_CONFIDENCE_CAP = 0.95


@dataclass(frozen=True)
class RouteDecision:
    route: Route
    reason: str          # template key for non-grounded routes; "kb" for grounded


@dataclass
class Decision:
    confidence: float
    escalate: bool
    grounded: bool
    flags: list[SafetyFlag]
    reasons: list[str]                       # why escalated / why not
    breakdown: dict[str, float] = field(default_factory=dict)


def choose_route(screen: Screening, cls: Classification, hits: list[Hit]) -> RouteDecision:
    if screen.empty:
        return RouteDecision(Route.REJECT, "invalid")
    if screen.injection:
        return RouteDecision(Route.REJECT, "injection")
    if screen.third_party:
        return RouteDecision(Route.REJECT, "third_party")
    if cls.intent is Intent.TRADING_ADVICE_REQUEST:   # refused whatever the language label says
        return RouteDecision(Route.REFUSAL, "advice")
    if screen.unsupported_language:
        return RouteDecision(Route.FALLBACK, "language")
    if cls.intent is Intent.OTHER:
        return RouteDecision(Route.FALLBACK, "no_kb") if screen.in_domain else RouteDecision(Route.REJECT, "out_of_scope")
    if not hits:
        return RouteDecision(Route.FALLBACK, "no_kb")
    return RouteDecision(Route.GROUNDED, "kb")


def _add(flags: list[SafetyFlag], flag: SafetyFlag) -> None:
    if flag not in flags:
        flags.append(flag)


def decide(
    route: RouteDecision,
    screen: Screening,
    cls: Classification,
    hits: list[Hit],
    grounding: GroundingReport,
    kb_intents: dict[str, Intent],
    uncovered: list[str] | None = None,
) -> Decision:
    flags: list[SafetyFlag] = []
    reasons: list[str] = []
    if screen.truncated:
        _add(flags, SafetyFlag.INPUT_TRUNCATED)
    if screen.sensitive_data:
        _add(flags, SafetyFlag.SENSITIVE_DATA_SHARED)
        reasons.append("client shared credentials or card data in the ticket")

    if route.route is Route.REJECT:
        if route.reason == "injection":
            _add(flags, SafetyFlag.PROMPT_INJECTION_SUSPECTED)
            reasons.append("possible prompt-injection attempt; flagged for security review")
            return Decision(0.9, True, False, flags, reasons)
        if route.reason == "third_party":
            _add(flags, SafetyFlag.THIRD_PARTY_REQUEST)
            reasons.append("request for another person's account or other users' data; flagged for security review")
            return Decision(0.9, True, False, flags, reasons)
        if route.reason == "invalid":
            _add(flags, SafetyFlag.INVALID_INPUT)
            reasons.append("empty message rejected; no escalation needed")
            return Decision(0.95, False, False, flags, reasons)
        _add(flags, SafetyFlag.OUT_OF_SCOPE)
        reasons.append("request is unrelated to supported topics; rejected without escalation")
        return Decision(round(0.5 + 0.45 * cls.certainty, 2), False, False, flags, reasons)

    if route.route is Route.FALLBACK:
        _add(flags, SafetyFlag.INSUFFICIENT_GROUNDING)
        _add(flags, SafetyFlag.LOW_CONFIDENCE)
        if route.reason == "language":
            _add(flags, SafetyFlag.UNSUPPORTED_LANGUAGE)
            reasons.append("unsupported language")
        else:
            reasons.append("no relevant KB article found")
        return Decision(0.0, True, False, flags, reasons)

    if route.route is Route.REFUSAL:
        _add(flags, SafetyFlag.ADVICE_REQUEST)
        support_issues = sorted(
            label for label, score in cls.scores.items()
            if label != Intent.TRADING_ADVICE_REQUEST.value and score >= MIN_INTENT_SCORE
        )
        if grounding.grounded and support_issues:
            _add(flags, SafetyFlag.CONFLICTING_SIGNALS)
            reasons.append(f"advice refused, but the ticket also raises a support issue ({', '.join(support_issues)}) for a human to handle")
            return Decision(round(min(cls.certainty, REFUSAL_CONFIDENCE_CAP) * 0.8, 2), True, True, flags, reasons)
        if grounding.grounded:
            confidence = min(cls.certainty, REFUSAL_CONFIDENCE_CAP)
            reasons.append("trading-advice request refused per policy; refusal is fixed, so no escalation")
            return Decision(round(confidence, 2), False, True, flags, reasons)
        _add(flags, SafetyFlag.INSUFFICIENT_GROUNDING)
        reasons.append("advice request refused, but no policy article backs the refusal")
        return Decision(round(min(cls.certainty, REFUSAL_CONFIDENCE_CAP) * 0.6, 2), True, False, flags, reasons)

    # --- GROUNDED ---------------------------------------------------------------
    intent_articles = {aid for aid, intent in kb_intents.items() if intent is cls.intent}
    hit_ids = [h.article_id for h in hits]
    if hit_ids[0] in intent_articles:
        agreement = 1.0
    elif intent_articles & set(hit_ids):
        agreement = 0.6
    else:
        agreement = 0.2
    breakdown = {
        "classifier": cls.certainty,
        "retrieval": round(hits[0].score / (hits[0].score + RETRIEVAL_SCALE), 4),
        "agreement": agreement,
        "grounding": grounding.coverage,
    }
    confidence = round(min(max(sum(WEIGHTS[k] * v for k, v in breakdown.items()), 0.0), 1.0), 2)

    escalate = False
    if cls.conflicting or agreement < 1.0:
        _add(flags, SafetyFlag.CONFLICTING_SIGNALS)
        escalate = True
        reasons.append("intent and retrieval signals disagree" if agreement < 1.0 else "two intents scored closely")
    if screen.account_specific:
        _add(flags, SafetyFlag.ACCOUNT_SPECIFIC_REQUEST)
        escalate = True
        reasons.append("answer requires the client's own account records")
    if cls.intent in POLICY_SENSITIVE_INTENTS:
        _add(flags, SafetyFlag.POLICY_SENSITIVE)
        escalate = True
        reasons.append(f"policy-sensitive topic ({cls.intent.value})")
    if not grounding.grounded:
        _add(flags, SafetyFlag.INSUFFICIENT_GROUNDING)
        escalate = True
        reasons.append("draft is not fully supported by retrieved articles")
    if uncovered:
        _add(flags, SafetyFlag.INSUFFICIENT_GROUNDING)
        escalate = True
        reasons.append(f"question asks about {', '.join(uncovered)}, which the retrieved articles do not cover")
    if grounding.claim_hits:
        _add(flags, SafetyFlag.UNSUPPORTED_CLAIM)
        escalate = True
        reasons.append("draft contains unsupported claims")
    if confidence < ESCALATE_BELOW:
        _add(flags, SafetyFlag.LOW_CONFIDENCE)
        escalate = True
        reasons.append(f"confidence {confidence} below {ESCALATE_BELOW}")
    if not escalate:
        reasons.insert(0, f"confident grounded answer (confidence {confidence}) with no policy or account-specific risk")
    return Decision(confidence, escalate, grounding.grounded, flags, reasons, breakdown)
