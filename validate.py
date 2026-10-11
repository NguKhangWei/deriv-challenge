"""Validate the pipeline artifacts: python validate.py

Checks: files exist, JSON is valid, one result per ticket, schema respected, article IDs
valid, advice requests safely refused, per-result safety rules, and reproducibility.
Exits non-zero if any check fails. Run `python main.py` first.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from pipeline.classify import is_advice_request
from pipeline.generate import REFUSAL_MARKER
from pipeline.models import Result, Ticket
from pipeline.report import results_payload
from pipeline.runner import build_pipeline
from pipeline.screen import screen_ticket
from pipeline.validation import validate_result

ROOT = Path(__file__).resolve().parent
REQUIRED_FILES = ["tickets.json", "kb_articles.json", "results.json", "debug_report.json", "debug_report.md", "README.md"]
SCHEMA_KEYS = set(Result.model_fields)


class Checker:
    def __init__(self) -> None:
        self.failures = 0

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail and not ok else ""))
        self.failures += not ok
        return ok


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as exc:  # noqa: BLE001 - reported as a check failure
        return None, str(exc)


def main() -> int:
    c = Checker()

    missing = [f for f in REQUIRED_FILES if not (ROOT / f).exists()]
    if not c.check("required files exist", not missing, f"missing: {missing}"):
        return 1

    data = {}
    for name in ("tickets.json", "kb_articles.json", "results.json", "debug_report.json"):
        data[name], err = load_json(ROOT / name)
        c.check(f"{name} is valid JSON", err is None, err or "")
    if c.failures:
        return 1

    tickets, kb, results = data["tickets.json"], data["kb_articles.json"], data["results.json"]
    kb_ids = {a["article_id"] for a in kb}
    kb_bodies = {a["article_id"]: a["body"] for a in kb}
    ticket_ids = [t["id"] for t in tickets]
    result_ids = [r.get("ticket_id") for r in results] if isinstance(results, list) else []

    c.check("results.json is a list with one result per ticket",
            isinstance(results, list) and result_ids == ticket_ids,
            f"tickets={ticket_ids} results={result_ids}")

    schema_errors = []
    for r in results:
        if set(r) != SCHEMA_KEYS:
            schema_errors.append(f"{r.get('ticket_id')}: keys {sorted(set(r) ^ SCHEMA_KEYS)} differ from schema")
            continue
        try:
            Result.model_validate(r)
        except Exception as exc:  # noqa: BLE001
            schema_errors.append(f"{r.get('ticket_id')}: {exc}")
    c.check("every result matches the required schema", not schema_errors, "; ".join(schema_errors))

    bad_ids = {r["ticket_id"]: [a for a in r.get("retrieved_articles", []) if a not in kb_ids] for r in results}
    bad_ids = {k: v for k, v in bad_ids.items() if v}
    c.check("retrieved article IDs are all known KB articles", not bad_ids, str(bad_ids))

    by_id = {r["ticket_id"]: r for r in results}
    refusal_problems = []
    screens = {t["id"]: screen_ticket(Ticket.model_validate(t)) for t in tickets}
    security_rejects = {tid for tid, sc in screens.items() if sc.injection or sc.third_party}
    advice_tickets = [t for t in tickets if is_advice_request(t["message"]) and t["id"] not in security_rejects]
    for t in advice_tickets:
        r = by_id.get(t["id"], {})
        if r.get("intent") != "trading_advice_request":
            refusal_problems.append(f"{t['id']}: intent {r.get('intent')}")
        elif REFUSAL_MARKER not in r.get("reply_draft", "").lower() or "advice_request" not in r.get("safety_flags", []):
            refusal_problems.append(f"{t['id']}: not refused / not flagged")
    if advice_tickets:
        c.check(f"trading-advice requests are safely refused ({len(advice_tickets)} found)",
                not refusal_problems, "; ".join(refusal_problems))
    else:
        print("[SKIP] no trading-advice request in tickets.json")

    rule_errors = []
    messages = {t["id"]: t["message"] for t in tickets}
    for r in results:
        source = " ".join(kb_bodies.get(a, "") for a in r.get("retrieved_articles", []))
        errs = validate_result(r, known_article_ids=kb_ids, ticket_text=messages.get(r["ticket_id"], ""),
                               source_text=source, security_reject=r["ticket_id"] in security_rejects)
        rule_errors += [f"{r['ticket_id']}: {e}" for e in errs]
    c.check("per-result safety rules hold (grounding, claims, refusals)", not rule_errors, "; ".join(rule_errors))

    quiet = lambda _msg: None  # noqa: E731
    run_a = results_payload(build_pipeline("offline", log=quiet).run(ROOT / "tickets.json", ROOT / "kb_articles.json"))
    run_b = results_payload(build_pipeline("offline", log=quiet).run(ROOT / "tickets.json", ROOT / "kb_articles.json"))
    c.check("offline pipeline is reproducible across runs", run_a == run_b)
    if data["debug_report.json"].get("meta", {}).get("mode") == "offline":
        c.check("results.json matches a fresh offline run", run_a == results,
                "re-run `python main.py`; outputs are stale")
    else:
        print("[SKIP] results.json was produced in llm mode; compared offline runs only")

    print(f"\n{'ALL CHECKS PASSED' if not c.failures else f'{c.failures} CHECK(S) FAILED'}")
    return 1 if c.failures else 0


if __name__ == "__main__":
    sys.exit(main())
