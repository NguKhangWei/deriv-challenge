"""Try tickets by hand and see the full result. Writes no files.

    python manual_tests/try_ticket.py "I forgot my password"          # one or more messages
    python manual_tests/try_ticket.py --language fr "Mon dépôt..."    # set the language label
    python manual_tests/try_ticket.py --file manual_tests/my_tickets.json
    python manual_tests/try_ticket.py                                 # interactive: type messages, blank line to quit
    python manual_tests/try_ticket.py --mode llm "..."                # use Gemini if GEMINI_API_KEY is set
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.llm import GeminiClient  # noqa: E402
from pipeline.loader import InputError  # noqa: E402
from pipeline.runner import build_pipeline  # noqa: E402


def show(run) -> None:
    for c in run.contexts:
        r, d = c.result, c.decision
        print("=" * 78)
        print(f"{r.ticket_id}  [{c.ticket.language}]  {c.ticket.message[:200]!r}")
        print("-" * 78)
        print(f"intent      : {r.intent.value}  (classifier: {c.classification.source}, certainty {c.classification.certainty})")
        arts = ", ".join(f"{a} ({run.kb[a].title})" for a in r.retrieved_articles) or "none"
        print(f"articles    : {arts}")
        scores = ", ".join(f"{h.article_id}={h.score}" for h in c.hits) or "nothing above threshold"
        print(f"bm25 scores : {scores}")
        print(f"route       : {c.route.route.value} ({c.route.reason}), generator: {c.draft.generator}")
        print(f"confidence  : {r.confidence}  {d.breakdown or ''}")
        print(f"grounded    : {r.grounded}")
        print(f"escalate    : {r.needs_human_escalation}")
        print(f"flags       : {[f.value for f in r.safety_flags] or 'none'}")
        print("why         : " + "\n              ".join(d.reasons))
        for note in c.draft.notes + [f"VALIDATION: {e}" for e in c.validation_errors]:
            print(f"note        : {note}")
        print("-" * 78)
        print(r.reply_draft)
    print("=" * 78)


def run_tickets(tickets: list[dict], mode: str) -> None:
    client = GeminiClient.from_env(cache_dir=ROOT / ".cache" / "llm") if mode == "llm" else None
    if mode == "llm" and client is None:
        print("(GEMINI_API_KEY not set; running offline)")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "tickets.json"
        path.write_text(json.dumps(tickets, ensure_ascii=False), encoding="utf-8")
        try:
            run = build_pipeline(mode, client, log=lambda _m: None).run(path, ROOT / "kb_articles.json")
        except InputError as exc:
            print(f"input error: {exc}")
            return
    show(run)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("messages", nargs="*", help="ticket message(s) to try")
    parser.add_argument("--language", default="en")
    parser.add_argument("--file", type=Path, help="a tickets JSON file (same format as tickets.json)")
    parser.add_argument("--mode", choices=["offline", "llm"], default="offline")
    args = parser.parse_args()

    if args.file:
        run_tickets(json.loads(args.file.read_text(encoding="utf-8")), args.mode)
    elif args.messages:
        run_tickets([{"id": f"M{i + 1}", "message": m, "language": args.language}
                     for i, m in enumerate(args.messages)], args.mode)
    else:
        print("Type a ticket message and press Enter (blank line to quit).")
        n = 0
        while True:
            try:
                message = input("> ").strip()
            except EOFError:
                break
            if not message:
                break
            n += 1
            run_tickets([{"id": f"M{n}", "message": message, "language": args.language}], args.mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
