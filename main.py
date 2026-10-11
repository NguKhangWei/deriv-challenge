"""Run the support-ticket pipeline: python main.py [--mode offline|llm]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline.llm import GeminiClient
from pipeline.loader import InputError
from pipeline.report import write_outputs
from pipeline.runner import build_pipeline

ROOT = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["offline", "llm"], default="offline",
                        help="offline (default, deterministic) or llm (Gemini fallback layer; needs GEMINI_API_KEY)")
    parser.add_argument("--tickets", type=Path, default=ROOT / "tickets.json")
    parser.add_argument("--kb", type=Path, default=ROOT / "kb_articles.json")
    parser.add_argument("--out-dir", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    client = None
    if args.mode == "llm":
        client = GeminiClient.from_env(cache_dir=ROOT / ".cache" / "llm")
        if client is None:
            print("[main] GEMINI_API_KEY not set; running the offline pipeline instead.", file=sys.stderr)

    pipeline = build_pipeline(args.mode, client)
    try:
        run = pipeline.run(args.tickets, args.kb)
    except InputError as exc:
        print(f"[LOAD_DATA] input error: {exc}", file=sys.stderr)
        return 2

    written = write_outputs(run, args.out_dir)
    print(f"[WRITE_RESULTS] {', '.join(p.name for p in written)}", file=sys.stderr)
    for r in run.results:
        print(f"{r.ticket_id}: {r.intent.value:<24} articles={r.retrieved_articles} "
              f"confidence={r.confidence:.2f} escalate={r.needs_human_escalation} flags={[f.value for f in r.safety_flags]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
