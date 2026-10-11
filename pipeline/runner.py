"""Pipeline orchestration:

LOAD_DATA -> INDEX_KB -> CLASSIFY_INTENT -> RETRIEVE_CONTEXT -> GENERATE_REPLY
          -> VALIDATE_OUTPUT -> WRITE_RESULTS

Each stage runs over every ticket before the next starts, and each component
(classifier, retriever, generator) is injected so it can be swapped independently.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .classify import Classifier, FallbackLLMClassifier, RuleClassifier
from .generate import (
    SENSITIVE_DATA_CAUTION,
    ExtractiveGenerator,
    GenerationInput,
    Generator,
    LLMGenerator,
    fallback_draft,
    refusal_draft,
    reject_draft,
    render,
)
from .guard import GroundingReport, check_draft, uncovered_topics
from .loader import load_articles, load_tickets
from .models import Article, Classification, Draft, DraftLine, Hit, Intent, Result, Route, Screening, Ticket
from .policy import Decision, RouteDecision, choose_route, decide
from .retrieve import BM25Retriever, Retriever
from .screen import screen_ticket
from .validation import safe_fallback, validate_result


@dataclass
class TicketContext:
    ticket: Ticket
    screening: Screening | None = None
    classification: Classification | None = None
    hits: list[Hit] = field(default_factory=list)
    route: RouteDecision | None = None
    draft: Draft | None = None
    grounding: GroundingReport | None = None
    decision: Decision | None = None
    result: Result | None = None
    validation_errors: list[str] = field(default_factory=list)


@dataclass
class RunOutput:
    contexts: list[TicketContext]
    kb: dict[str, Article]
    kb_intents: dict[str, Intent]
    meta: dict

    @property
    def results(self) -> list[Result]:
        return [c.result for c in self.contexts if c.result is not None]


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


class Pipeline:
    def __init__(
        self,
        classifier: Classifier,
        retriever: Retriever,
        generator: Generator,
        *,
        meta: dict | None = None,
        log: Callable[[str], None] = _stderr,
    ) -> None:
        self.classifier, self.retriever, self.generator = classifier, retriever, generator
        self.meta = meta or {}
        self.log = log
        self._rules = RuleClassifier()   # KB mapping and blocked tickets never use a model

    # -- stages -------------------------------------------------------------------

    def load_data(self, tickets_path: Path, kb_path: Path) -> tuple[list[Ticket], list[Article]]:
        tickets, articles = load_tickets(tickets_path), load_articles(kb_path)
        self.log(f"[LOAD_DATA] {len(tickets)} tickets, {len(articles)} articles")
        return tickets, articles

    def index_kb(self, articles: list[Article]) -> dict[str, Intent]:
        self.retriever.index(articles)
        kb_intents = {a.article_id: self._rules.classify(f"{a.title}. {a.body}").intent for a in articles}
        self.log(f"[INDEX_KB] indexed; article intents: {', '.join(f'{k}={v.value}' for k, v in kb_intents.items())}")
        return kb_intents

    def classify_intent(self, ctx: TicketContext) -> None:
        ctx.screening = screen_ticket(ctx.ticket)
        screen = ctx.screening
        # Tickets that screening will reject are never sent to an external model.
        blocked = screen.empty or screen.injection or screen.third_party
        ctx.classification = (self._rules if blocked else self.classifier).classify(screen.text)

    def retrieve_context(self, ctx: TicketContext) -> None:
        ctx.hits = self.retriever.retrieve(ctx.screening.text) if not ctx.screening.empty else []

    def generate_reply(self, ctx: TicketContext, kb: dict[str, Article], kb_intents: dict[str, Intent]) -> None:
        screen, cls = ctx.screening, ctx.classification
        ctx.route = choose_route(screen, cls, ctx.hits)
        route = ctx.route.route
        if route is Route.GROUNDED:
            ctx.draft = self.generator.generate(GenerationInput(screen.text, cls.intent, ctx.hits, kb, kb_intents))
        elif route is Route.REFUSAL:
            hit_order = [h.article_id for h in ctx.hits]
            policy_ids = sorted(
                (a for a, i in kb_intents.items() if i is Intent.TRADING_ADVICE_REQUEST),
                key=lambda a: (a not in hit_order, hit_order.index(a) if a in hit_order else 0, a),
            )[:3]
            ctx.draft = refusal_draft([kb[a] for a in policy_ids])
        elif route is Route.FALLBACK:
            ctx.draft = fallback_draft(ctx.route.reason)
        else:
            ctx.draft = reject_draft(ctx.route.reason)

        if screen.sensitive_data:
            ctx.draft.lines.append(DraftLine(SENSITIVE_DATA_CAUTION, (), "framing"))

        ctx.grounding = check_draft(ctx.draft, kb)
        uncovered = uncovered_topics(screen.text, [kb[h.article_id] for h in ctx.hits]) if route is Route.GROUNDED else []
        ctx.decision = decide(ctx.route, screen, cls, ctx.hits, ctx.grounding, kb_intents, uncovered)
        retrieved = [h.article_id for h in ctx.hits] if route is Route.GROUNDED else list(ctx.draft.articles)
        intent = cls.intent if route in (Route.GROUNDED, Route.REFUSAL) else Intent.OTHER
        ctx.result = Result(
            ticket_id=ctx.ticket.id,
            intent=intent,
            retrieved_articles=retrieved[:3],
            reply_draft=render(ctx.draft, ctx.decision.escalate, ctx.decision.reasons),
            grounded=ctx.decision.grounded,
            confidence=ctx.decision.confidence,
            needs_human_escalation=ctx.decision.escalate,
            safety_flags=ctx.decision.flags,
        )

    def validate_output(self, ctx: TicketContext, kb: dict[str, Article]) -> None:
        source_text = " ".join(kb[a].body for a in ctx.result.retrieved_articles if a in kb)
        ctx.validation_errors = validate_result(
            ctx.result.model_dump(mode="json"),
            known_article_ids=set(kb),
            ticket_text=ctx.screening.text,
            source_text=source_text,
            security_reject=ctx.screening.injection or ctx.screening.third_party,
        )
        if ctx.validation_errors:
            self.log(f"[VALIDATE_OUTPUT] {ctx.ticket.id}: replaced with safe fallback: {ctx.validation_errors}")
            ctx.result = safe_fallback(ctx.ticket.id)

    # -- orchestration ----------------------------------------------------------

    def run(self, tickets_path: Path, kb_path: Path) -> RunOutput:
        tickets, articles = self.load_data(tickets_path, kb_path)
        kb = {a.article_id: a for a in articles}
        kb_intents = self.index_kb(articles)
        contexts = [TicketContext(t) for t in tickets]

        for ctx in contexts:
            self.classify_intent(ctx)
        self.log("[CLASSIFY_INTENT] " + ", ".join(f"{c.ticket.id}={c.classification.intent.value}" for c in contexts))

        for ctx in contexts:
            self.retrieve_context(ctx)
        self.log("[RETRIEVE_CONTEXT] " + ", ".join(f"{c.ticket.id}={[h.article_id for h in c.hits]}" for c in contexts))

        for ctx in contexts:
            self.generate_reply(ctx, kb, kb_intents)
        self.log("[GENERATE_REPLY] " + ", ".join(f"{c.ticket.id}={c.route.route.value}" for c in contexts))

        for ctx in contexts:
            self.validate_output(ctx, kb)
        failed = sum(1 for c in contexts if c.validation_errors)
        self.log(f"[VALIDATE_OUTPUT] {len(contexts) - failed}/{len(contexts)} passed")

        return RunOutput(contexts, kb, kb_intents, dict(self.meta))


def build_pipeline(mode: str = "offline", llm_client=None, log: Callable[[str], None] = _stderr) -> Pipeline:
    """offline: rules + BM25 + extractive. llm: same, with Gemini as a guarded fallback layer."""
    rules = RuleClassifier()
    extractive = ExtractiveGenerator()
    retriever = BM25Retriever()
    if mode == "llm" and llm_client is not None:
        classifier: Classifier = FallbackLLMClassifier(rules, llm_client)
        generator: Generator = LLMGenerator(llm_client, fallback=extractive)
        meta = {"mode": "llm", "llm_model": llm_client.model}
    else:
        classifier, generator = rules, extractive
        meta = {"mode": "offline", "llm_model": None}
    meta.update(classifier=getattr(classifier, "name", type(classifier).__name__),
                retriever="bm25", generator=generator.name)
    return Pipeline(classifier, retriever, generator, meta=meta, log=log)
