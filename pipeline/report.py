"""WRITE_RESULTS: results.json plus the explainability artifacts."""
from __future__ import annotations

import html
import json
import os
from pathlib import Path

from .runner import RunOutput
from .text import redact


def _write_atomic(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def results_payload(run: RunOutput) -> list[dict]:
    return [r.model_dump(mode="json") for r in run.results]


def debug_payload(run: RunOutput) -> dict:
    tickets = []
    for c in run.contexts:
        cls, d = c.classification, c.decision
        tickets.append({
            "ticket_id": c.ticket.id,
            "message_preview": redact(c.ticket.message)[:200],
            "language": c.ticket.language,
            "screening_notes": list(c.screening.reasons),
            "classification": {
                "intent": cls.intent.value,
                "source": cls.source,
                "certainty": cls.certainty,
                "scores": cls.scores,
                "conflicting": cls.conflicting,
                "reasons": list(cls.reasons),
            },
            "retrieval": [{"article_id": h.article_id, "title": h.title, "score": h.score} for h in c.hits],
            "route": c.route.route.value,
            "route_reason": c.route.reason,
            "generator": c.draft.generator,
            "generator_notes": c.draft.notes,
            "grounding": {
                "coverage": c.grounding.coverage,
                "unsupported_lines": c.grounding.unsupported_lines,
                "claim_hits": c.grounding.claim_hits,
            },
            "confidence": c.result.confidence,
            "confidence_breakdown": d.breakdown,
            "needs_human_escalation": c.result.needs_human_escalation,
            "escalation_reasons": d.reasons,
            "safety_flags": [f.value for f in c.result.safety_flags],
            "validation_errors": c.validation_errors,
        })
    return {
        "meta": {**run.meta, "ticket_count": len(run.contexts), "article_count": len(run.kb),
                 "article_intents": {k: v.value for k, v in run.kb_intents.items()}},
        "tickets": tickets,
    }


def _md(value: object) -> str:
    """Escape untrusted text for a Markdown table / list (no raw HTML, no cell breaks)."""
    return html.escape(str(value)).replace("|", "\\|").replace("\n", " ")


def debug_markdown(run: RunOutput) -> str:
    meta = run.meta
    lines = [
        "# Debug report",
        "",
        f"Mode: `{meta.get('mode')}` | classifier: `{meta.get('classifier')}` | retriever: `{meta.get('retriever')}` | generator: `{meta.get('generator')}`",
        "",
        "| Ticket | Intent | Retrieved articles | Route | Confidence | Escalate | Why |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in run.contexts:
        r = c.result
        titles = "; ".join(f"{a} – {_md(run.kb[a].title)}" for a in r.retrieved_articles) or "—"
        why = "; ".join(c.decision.reasons + [f"validation: {e}" for e in c.validation_errors])
        lines.append(
            f"| {_md(r.ticket_id)} | {r.intent.value} | {titles} | {c.route.route.value} | {r.confidence:.2f} "
            f"| {'yes' if r.needs_human_escalation else 'no'} | {_md(why)} |"
        )
    lines += ["", "## Per-ticket detail", ""]
    for c in run.contexts:
        cls = c.classification
        lines += [
            f"### {_md(c.ticket.id)}",
            "",
            f"- Message: {_md(repr(redact(c.ticket.message)[:200]))}",
            f"- Classifier ({cls.source}): {cls.intent.value}, certainty {cls.certainty}; " + "; ".join(cls.reasons),
            "- Retrieval scores: " + (", ".join(f"{h.article_id} ({h.title}) = {h.score}" for h in c.hits) or "nothing above threshold"),
            f"- Generator: {c.draft.generator}" + (f" — {'; '.join(c.draft.notes)}" if c.draft.notes else ""),
            f"- Grounding coverage: {c.grounding.coverage}" + (f"; claim hits: {c.grounding.claim_hits}" if c.grounding.claim_hits else ""),
            f"- Confidence breakdown: {c.decision.breakdown or 'fixed for this route'}",
            f"- Flags: {[f.value for f in c.result.safety_flags]}",
            "",
        ]
    return "\n".join(lines)


def write_outputs(run: RunOutput, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        out_dir / "results.json": json.dumps(results_payload(run), indent=2, ensure_ascii=False) + "\n",
        out_dir / "debug_report.json": json.dumps(debug_payload(run), indent=2, ensure_ascii=False) + "\n",
        out_dir / "debug_report.md": debug_markdown(run) + "\n",
    }
    for path, content in files.items():
        _write_atomic(path, content)
    return list(files)
