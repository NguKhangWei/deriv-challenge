"""CLASSIFY_INTENT: map ticket text to exactly one allowed intent label."""
from __future__ import annotations

import re
from typing import Protocol

from .models import Classification, Intent
from .text import fold, normalize


class Classifier(Protocol):
    def classify(self, text: str) -> Classification: ...


def _compile(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


# Requests for predictions / recommendations. Deliberately phrased around *asking for
# advice* so that "can I withdraw my profit?" is not mistaken for an advice request.
ADVICE_PATTERNS = _compile(
    r"\b(?:which|what)\s+(?:\w+\s+)?(?:assets?|stocks?|coins?|crypto\w*|currenc(?:y|ies)|pairs?|shares?|markets?|commodit(?:y|ies)|indices|symbols?|tokens?)\b.{0,60}\b(?:buy|sell|go(?:ing)? (?:up|down)|rise|fall|pump|profit|invest|trade|moon)",
    r"\b(?:prices?|markets?|assets?|stocks?|coins?|bitcoin|btc|eth|gold|oil|crypto\w*|ind(?:ex|ices)|pairs?|shares?|forex)\b.{0,40}\b(?:go(?:ing)? (?:up|down)|rise|fall|increase|drop|moon|pump|crash)\b",
    r"\bshould i (?:buy|sell|invest|trade|hold|short|go long|go short)\b",
    r"\b(?:guarantee\w*|sure|safe|easy|quick|fast)\s+(?:profits?|returns?|money|wins?)\b",
    r"\b(?:predict\w*|forecast\w*|signals?|tips?)\b.{0,40}\b(?:market|price|asset|trade|trading|stock|crypto|coin)",
    r"\b(?:best|good|top)\s+(?:assets?|stocks?|coins?|trades?|investments?|strateg(?:y|ies))\b",
    r"\b(?:recommend|suggest)\b.{0,30}\b(?:assets?|stocks?|coins?|trades?|investments?|to buy)\b",
    r"\b(?:good|right|best)\s+time\s+to\s+(?:buy|sell|invest|trade|enter|exit|get in|get out)\b",
    r"\bworth\s+(?:buying|selling|investing|trading|holding)\b",
    r"\b(?:pick|choose|tell me)\b.{0,30}\b(?:assets?|stocks?|coins?|shares?|crypto\w*)\b.{0,30}\b(?:for me|to (?:buy|invest|trade))\b",
    r"\b(?:financial|investment|trading)\s+advis[eo]r\b",
)

# Weighted lexicon per intent. Each pattern counts at most once per ticket.
# Patterns run on normalize()d text, so "log in"/"login"/"sign-in" -> "signin" and
# "withdrawal(s)" -> "withdraw".
LEXICON: dict[Intent, tuple[tuple[re.Pattern[str], float], ...]] = {
    intent: tuple((re.compile(p), w) for p, w in rules)
    for intent, rules in {
        Intent.DEPOSIT_ISSUE: (
            (r"\bdeposit\w*", 3.0),
            (r"\bfund(?:ed|ing|s)?\b", 1.5),
            (r"\bbalance\b", 1.0),
            (r"\b(?:card|visa|mastercard|debit|credit)\b", 1.0),
            (r"\b(?:payments?|paid)\b", 1.0),
            (r"\b(?:missing|(?:not|never)\s+(?:arrived|credited|reflected|showing|shown|there|in)|(?:isn'?t|hasn'?t|haven'?t)\s+(?:in|there|arrived|showing|shown up|appeared|credited))\b", 1.0),
        ),
        Intent.PASSWORD_RESET: (
            (r"\bpassword\b", 3.0),
            (r"\breset\b", 2.0),
            (r"\bforg[oe]t\w*", 2.0),
            (r"\b(?:reset|recovery)\s+(?:email|link|code)\b|\bemail\s+(?:to|for)\s+reset", 2.0),
        ),
        Intent.WITHDRAWAL_ISSUE: (
            (r"\bwithdraw\b", 3.0),
            (r"\bcash(?:ing)?[\s-]?out\b|\bpayouts?\b", 2.0),
            (r"\bdeclin\w*|\breject\w*|\bdenied\b", 1.0),
            (r"\bverif\w*|\bkyc\b", 0.5),
        ),
        Intent.ACCOUNT_LOCK: (
            (r"\block(?:ed|out|s)?\b", 3.0),
            (r"\b(?:blocked|suspended|disabled|frozen)\b", 2.0),
            (r"\b(?:too many|failed|multiple|repeated|wrong|incorrect)\s+(?:\w+\s+){0,2}attempts?\b", 2.0),
            (r"\bsignin\b", 1.0),
            (r"\b(?:can'?t|cannot|unable to)\s+(?:access|signin|get in(?:to)?)\b", 1.5),
        ),
        Intent.TRADING_ADVICE_REQUEST: (
            (r"\btrad(?:e|es|ing)\b", 1.5),
            (r"\binvest\w*", 1.5),
            (r"\badvice\b", 1.0),
            (r"\bpredict\w*|\bforecast\w*", 1.5),
            (r"\b(?:assets?|stocks?|crypto\w*|coins?|forex|shares)\b", 1.0),
            (r"\bprofits?\b", 1.0),
            (r"\brecommend\w*", 1.0),
        ),
    }.items()
}

MIN_INTENT_SCORE = 2.0      # below this the ticket is "other"
STRONG_INTENT_SCORE = 5.0   # at or above this, lexical strength is saturated
CONFLICT_RATIO = 0.6        # runner-up within 60% of the winner => conflicting


def is_advice_request(text: str) -> bool:
    folded = fold(text)
    return any(p.search(folded) for p in ADVICE_PATTERNS)


class RuleClassifier:
    """Advice detector first, then weighted lexicon scoring with a margin-based certainty."""

    name = "rules"

    def score(self, text: str) -> dict[str, float]:
        norm = normalize(text)
        return {
            intent.value: round(sum(w for p, w in rules if p.search(norm)), 2)
            for intent, rules in LEXICON.items()
        }

    def classify(self, text: str) -> Classification:
        scores = self.score(text)
        if is_advice_request(text):
            return Classification(
                intent=Intent.TRADING_ADVICE_REQUEST,
                certainty=1.0,
                scores=scores,
                conflicting=False,
                source="rules:advice",
                reasons=("matched a trading-advice / prediction request pattern",),
            )

        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        (top_label, top), (second_label, second) = ranked[0], ranked[1]
        if top < MIN_INTENT_SCORE:
            return Classification(
                intent=Intent.OTHER,
                certainty=round(1.0 - top / MIN_INTENT_SCORE, 4),
                scores=scores,
                conflicting=False,
                source=self.name,
                reasons=(f"no intent reached the minimum score ({top} < {MIN_INTENT_SCORE})",),
            )

        strength = min(top / STRONG_INTENT_SCORE, 1.0)
        margin = (top - second) / top
        conflicting = second >= MIN_INTENT_SCORE and second >= CONFLICT_RATIO * top
        reasons = [f"top intent {top_label}={top}, runner-up {second_label}={second}"]
        if conflicting:
            reasons.append(f"runner-up intent '{second_label}' is close to the winner")
        return Classification(
            intent=Intent(top_label),
            certainty=round(0.5 * strength + 0.5 * margin, 4),
            scores=scores,
            conflicting=conflicting,
            source=self.name,
            reasons=tuple(reasons),
        )


class FallbackLLMClassifier:
    """Rules first; asks the LLM only when the rules are unsure.

    The LLM output is constrained to the allowed labels by a JSON enum schema and is
    re-validated here. The advice detector is never overridden by the model.
    """

    LLM_CERTAINTY = 0.6
    UNSURE_BELOW = 0.5

    def __init__(self, rules: RuleClassifier, client) -> None:
        self.rules, self.client = rules, client
        self.name = "rules+llm"

    def classify(self, text: str) -> Classification:
        base = self.rules.classify(text)
        if base.source == "rules:advice":
            return base
        if base.intent is not Intent.OTHER and base.certainty >= self.UNSURE_BELOW:
            return base
        try:
            label = self.client.classify_intent(text, [i.value for i in Intent])
            intent = Intent(label)
        except Exception as exc:  # network, quota, malformed output: keep the rules answer
            return Classification(**{**base.__dict__, "reasons": base.reasons + (f"llm classifier unavailable: {type(exc).__name__}",)})

        conflicting = base.intent is not Intent.OTHER and intent is not base.intent
        reasons = base.reasons + (f"rules unsure; llm label '{intent.value}'",)
        if conflicting:
            reasons += (f"llm disagrees with rules ('{base.intent.value}')",)
        return Classification(
            intent=intent,
            certainty=self.LLM_CERTAINTY if not conflicting else round(self.LLM_CERTAINTY / 2, 4),
            scores=base.scores,
            conflicting=conflicting or base.conflicting,
            source="llm",
            reasons=reasons,
        )
