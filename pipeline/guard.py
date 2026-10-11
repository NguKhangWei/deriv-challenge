"""Grounding and unsupported-claim checks.

Used twice: by GENERATE_REPLY to vet a draft (and reject bad LLM output), and again by
VALIDATE_OUTPUT on the final rendered text as an independent second line of defence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import Article, Draft
from .text import content_terms, split_sentences, support_ratio

SUPPORT_THRESHOLD = 0.6   # share of a claim's content terms that must appear in one source sentence

_DURATION = re.compile(
    r"\b(?:within\s+)?\d+(?:\s*(?:-|to)\s*\d+)?\s*(?:minutes?|mins?|hours?|hrs?|days?|business days|working days|weeks?|months?)\b",
    re.IGNORECASE,
)
# Absolute wording that can flip a KB sentence's meaning ("usually instant" -> "always instant").
_ABSOLUTE = re.compile(r"\b(?:always|never|guaranteed|definitely|certainly|immediately|instantly)\b|100\s*%", re.IGNORECASE)

# Question topics the KB does not cover; if asked about and absent from the retrieved
# articles, a grounded draft cannot answer that part of the question.
UNCOVERED_TOPIC_TERMS = frozenset(content_terms(
    "fee fees charge charges cost costs commission refund bonus tax statement invoice "
    "minimum maximum limit limits interest leverage spread cashback promotion"
))

UNSUPPORTED_CLAIM_PATTERNS: dict[str, re.Pattern[str]] = {
    "outcome_promise": re.compile(
        r"\b(?:will|shall)\s+(?:definitely\s+|certainly\s+|surely\s+)?(?:be\s+)?(?:approved|credited|processed|released|refunded|unlocked|reflected|resolved|reversed|arrive)\b",
        re.IGNORECASE,
    ),
    "guarantee": re.compile(r"\bguarantee[sd]?\s+(?:that|to|you|your)\b|\b(?:i|we)\s+promise\b", re.IGNORECASE),
    "account_fact": re.compile(
        r"\b(?:i|we)\s+(?:can\s+)?(?:see|checked|have checked|confirmed|verified)\b.{0,40}\b(?:your|the client'?s|their)\b"
        r"|\b(?:your|the client'?s)\s+(?:account|balance|deposit|withdrawal|payment|transaction)\s+(?:shows|has been|is|was)\b",
        re.IGNORECASE,
    ),
    "market_prediction": re.compile(
        r"\b(?:will|is going to|is likely to|expected to)\s+(?:go up|go down|rise|fall|increase|drop)\b"
        r"|\byou should (?:buy|sell|invest|trade)\b|\b(?:buy|sell) (?:it |this )?(?:now|today)\b",
        re.IGNORECASE,
    ),
}


def find_unsupported_claims(text: str, source_text: str = "") -> list[str]:
    """Return 'kind: snippet' for each risky assertion not backed by the sources."""
    found = []
    for kind, pattern in UNSUPPORTED_CLAIM_PATTERNS.items():
        found += [f"{kind}: {m.group(0)}" for m in pattern.finditer(text)]
    source_lower = source_text.lower()
    for kind, pattern in (("unsourced_timeframe", _DURATION), ("unsourced_absolute", _ABSOLUTE)):
        found += [f"{kind}: {m.group(0)}" for m in pattern.finditer(text) if m.group(0).lower() not in source_lower]
    return found


def uncovered_topics(query: str, articles: list[Article]) -> list[str]:
    """Topic terms the client asks about that none of the given articles mention."""
    covered = set(content_terms(" ".join(f"{a.title} {a.body}" for a in articles)))
    return sorted((set(content_terms(query)) & UNCOVERED_TOPIC_TERMS) - covered)


@dataclass
class GroundingReport:
    coverage: float                                   # supported claim lines / claim lines
    unsupported_lines: list[str] = field(default_factory=list)
    claim_hits: list[str] = field(default_factory=list)

    @property
    def grounded(self) -> bool:
        return self.coverage > 0 and not self.unsupported_lines and not self.claim_hits


def best_support(text: str, articles: list[Article]) -> float:
    sentences = [s for a in articles for s in split_sentences(a.body)]
    return max((support_ratio(text, s) for s in sentences), default=0.0)


def check_draft(draft: Draft, kb: dict[str, Article]) -> GroundingReport:
    """Every non-template line must cite a draft article and be lexically supported by it."""
    claims = draft.claim_lines
    unsupported = []
    for line in claims:
        cited = [kb[a] for a in line.sources if a in kb and a in draft.articles]
        if not cited or best_support(line.text, cited) < SUPPORT_THRESHOLD:
            unsupported.append(line.text)
    coverage = (len(claims) - len(unsupported)) / len(claims) if claims else 0.0

    source_text = " ".join(kb[a].body for a in draft.articles if a in kb)
    draft_text = " ".join([draft.summary, *(l.text for l in draft.lines), draft.suggested_reply or ""])
    return GroundingReport(
        coverage=round(coverage, 4),
        unsupported_lines=unsupported,
        claim_hits=find_unsupported_claims(draft_text, source_text),
    )
