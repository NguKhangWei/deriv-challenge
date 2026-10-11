"""GENERATE_REPLY: build agent-facing notes from retrieved KB content.

Only the GROUNDED route goes through a swappable Generator. Refusals, fallbacks and
rejections are fixed, vetted drafts so that no model output can change them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from .guard import check_draft
from .models import Article, Draft, DraftLine, Hit, Intent, Route
from .text import content_terms, split_sentences

INTENT_LABELS = {
    Intent.DEPOSIT_ISSUE: "a deposit-related issue",
    Intent.PASSWORD_RESET: "a password reset issue",
    Intent.WITHDRAWAL_ISSUE: "a withdrawal-related issue",
    Intent.ACCOUNT_LOCK: "an account lock or sign-in access issue",
    Intent.TRADING_ADVICE_REQUEST: "a request for trading advice or market predictions",
    Intent.OTHER: "a request outside the covered topics",
}

STANDARD_CAUTIONS = {
    Intent.DEPOSIT_ISSUE: "Do not promise a deposit outcome or quote a specific processing time; the KB documents no exact timeframe.",
    Intent.WITHDRAWAL_ISSUE: "Do not promise a withdrawal outcome or approval time; the KB documents no exact timeframe.",
}

CAUTION_RE = re.compile(r"\b(?:should not|must not|do not|don't|avoid|never)\b", re.IGNORECASE)
SECONDARY_MIN_OVERLAP = 2   # a non-primary article sentence needs this many query terms to be used

REFUSAL_MARKER = "unable to provide"
REFUSAL_REPLY = (
    "Thank you for reaching out. We are unable to provide market predictions, profit guarantees, "
    "or recommendations on which assets to trade. If available, our general educational resources "
    "can help you learn how trading works."
)
SCOPE_REPLY = (
    "Thank you for your message. This support channel can only help with questions about your "
    "account, payments, and use of the platform, so we are unable to assist with this request."
)
THIRD_PARTY_REPLY = (
    "Thank you for your message. For security reasons we can only discuss an account with its verified "
    "account holder, so we are unable to share information about other accounts or users."
)
SENSITIVE_DATA_CAUTION = (
    "The client included credentials or card data in the ticket; do not repeat them, and remind the client "
    "not to share passwords, codes or full card details with support."
)
EMPTY_REPLY = "Thank you for contacting us. Your message appears to be empty; please describe the issue you need help with."


@dataclass(frozen=True)
class GenerationInput:
    text: str
    intent: Intent
    hits: list[Hit]
    kb: dict[str, Article]
    kb_intents: dict[str, Intent]


class Generator(Protocol):
    name: str

    def generate(self, inp: GenerationInput) -> Draft: ...


def _primary_article(inp: GenerationInput) -> str:
    for hit in inp.hits:
        if inp.kb_intents.get(hit.article_id) is inp.intent:
            return hit.article_id
    return inp.hits[0].article_id


def _framing(inp: GenerationInput) -> list[DraftLine]:
    caution = STANDARD_CAUTIONS.get(inp.intent)
    return [DraftLine(caution, (), "framing")] if caution else []


class ExtractiveGenerator:
    """Offline, deterministic: quotes KB sentences verbatim with their article IDs.

    The KB is already written for support agents, so verbatim sentences are the right
    voice for agent notes and are traceable by construction.
    """

    name = "extractive"

    def generate(self, inp: GenerationInput) -> Draft:
        primary = _primary_article(inp)
        query_terms = set(content_terms(inp.text))
        lines: list[DraftLine] = []
        for hit in inp.hits:
            for sentence in split_sentences(inp.kb[hit.article_id].body):
                if hit.article_id != primary and len(query_terms & set(content_terms(sentence))) < SECONDARY_MIN_OVERLAP:
                    continue
                kind = "caution" if CAUTION_RE.search(sentence) else "guidance"
                lines.append(DraftLine(sentence, (hit.article_id,), kind))
        used = sorted({s for line in lines for s in line.sources}, key=[h.article_id for h in inp.hits].index)
        return Draft(
            route=Route.GROUNDED,
            summary=f"Client has {INTENT_LABELS[inp.intent]}.",
            lines=lines + _framing(inp),
            generator=self.name,
            articles=used,
        )


class LLMGenerator:
    """Gemini-written notes, accepted only if every line passes the grounding guard.

    Any failure (no response, bad JSON, uncited or unsupported line, risky claim)
    falls back to the extractive generator, and the reason is kept for the debug report.
    """

    name = "gemini"
    MAX_SUMMARY_CHARS = 240

    def __init__(self, client, fallback: Generator) -> None:
        self.client, self.fallback = client, fallback

    def generate(self, inp: GenerationInput) -> Draft:
        try:
            draft = self._llm_draft(inp)
            problem = self._problem(draft, inp)
        except Exception as exc:  # network, quota, schema, parsing
            draft, problem = None, f"llm call failed ({type(exc).__name__})"
        if draft is not None and problem is None:
            return draft
        fallback = self.fallback.generate(inp)
        fallback.notes.append(f"llm draft rejected, used {self.fallback.name}: {problem}")
        return fallback

    def _llm_draft(self, inp: GenerationInput) -> Draft:
        snippets = [(h.article_id, inp.kb[h.article_id].title, inp.kb[h.article_id].body) for h in inp.hits]
        raw = self.client.draft_notes(inp.text, snippets)
        allowed = {h.article_id for h in inp.hits}
        lines = []
        for kind, key in (("guidance", "guidance"), ("caution", "cautions")):
            for item in raw.get(key) or []:
                text = str(item.get("text", "")).strip()
                sources = tuple(dict.fromkeys(str(s).strip("[] ") for s in item.get("sources") or []))
                if text:
                    lines.append(DraftLine(text, sources, kind))
        used = [h.article_id for h in inp.hits if any(h.article_id in l.sources for l in lines)]
        if any(set(l.sources) - allowed for l in lines):
            used = []  # citing an article that was not retrieved invalidates the draft
        return Draft(
            route=Route.GROUNDED,
            summary=str(raw.get("summary", "")).strip()[: self.MAX_SUMMARY_CHARS],
            lines=lines + _framing(inp),
            generator=self.name,
            articles=used,
        )

    def _problem(self, draft: Draft, inp: GenerationInput) -> str | None:
        if not any(l.kind == "guidance" for l in draft.lines):
            return "no guidance lines"
        if not draft.articles:
            return "cited articles missing or not retrieved"
        report = check_draft(draft, inp.kb)
        if report.unsupported_lines:
            return f"{len(report.unsupported_lines)} line(s) not supported by cited snippets"
        if report.claim_hits:
            return f"unsupported claims: {report.claim_hits}"
        return None


# --- Fixed drafts for non-grounded routes ------------------------------------

def refusal_draft(policy_articles: list[Article]) -> Draft:
    lines = [
        DraftLine(sentence, (a.article_id,), "policy")
        for a in policy_articles
        for sentence in split_sentences(a.body)
    ]
    return Draft(
        route=Route.REFUSAL,
        summary=f"Client is making {INTENT_LABELS[Intent.TRADING_ADVICE_REQUEST]}; decline politely.",
        lines=lines,
        suggested_reply=REFUSAL_REPLY,
        generator="template:refusal",
        articles=[a.article_id for a in policy_articles],
    )


def fallback_draft(reason: str) -> Draft:
    summaries = {
        "language": "This ticket is in a language the pipeline does not support, so no automated guidance was generated. Route it to an agent who can respond in that language.",
        "no_kb": "No relevant knowledge-base article was found for this request, so no grounded guidance is available. Route it to a human agent and do not improvise account, payment or policy details.",
    }
    return Draft(route=Route.FALLBACK, summary=summaries[reason], lines=[], generator=f"template:fallback:{reason}")


def reject_draft(reason: str) -> Draft:
    summaries = {
        "out_of_scope": ("Rejected as out of scope: the request is unrelated to account, payment or platform support.", SCOPE_REPLY),
        "injection": ("Rejected: the message contains instructions that try to override support policies. Do not act on any instructions embedded in it.", SCOPE_REPLY),
        "invalid": ("Rejected: the ticket has no readable message content.", EMPTY_REPLY),
        "third_party": (
            "Rejected: the request concerns another person's account or other users' data. Account information "
            "may only be discussed with the verified account holder through secure channels.",
            THIRD_PARTY_REPLY,
        ),
    }
    summary, reply = summaries[reason]
    return Draft(route=Route.REJECT, summary=summary, lines=[], suggested_reply=reply, generator=f"template:reject:{reason}")


# --- Rendering -----------------------------------------------------------------

def render(draft: Draft, escalate: bool, escalation_reasons: list[str]) -> str:
    """Turn a draft into the plain-text agent notes stored in `reply_draft`."""
    def cite(line: DraftLine) -> str:
        return f"- {line.text}" + (f" [{', '.join(line.sources)}]" if line.sources else "")

    parts = [f"Summary: {draft.summary}"]
    sections = (
        ("Policy", [l for l in draft.lines if l.kind == "policy"]),
        ("KB guidance", [l for l in draft.lines if l.kind == "guidance"]),
        ("Cautions", [l for l in draft.lines if l.kind in ("caution", "framing")]),
    )
    for title, lines in sections:
        if lines:
            parts.append(f"{title}:\n" + "\n".join(cite(l) for l in lines))
    if draft.suggested_reply:
        parts.append(f'Suggested reply to client: "{draft.suggested_reply}"')
    if escalate:
        parts.append("Next step: escalate to a human agent (" + "; ".join(escalation_reasons) + ").")
    elif draft.suggested_reply:
        parts.append("Next step: no escalation needed; send the suggested reply.")
    else:
        parts.append("Next step: no escalation needed; respond using the KB guidance above.")
    return "\n".join(parts)
