"""Required checks on the sample data, plus sample-level behaviour."""
import json

from pipeline.models import Intent, Result, SafetyFlag
from pipeline.report import results_payload, write_outputs
from pipeline.runner import build_pipeline

from .conftest import FIXTURES, quiet


def test_t4_is_refused_as_trading_advice(results_by_id):
    t4 = results_by_id["T4"]
    assert t4.intent is Intent.TRADING_ADVICE_REQUEST
    assert "unable to provide" in t4.reply_draft.lower()
    assert SafetyFlag.ADVICE_REQUEST in t4.safety_flags
    assert t4.retrieved_articles == ["A5"]
    assert t4.grounded and not t4.needs_human_escalation


def test_t1_retrieves_the_deposit_article(results_by_id):
    t1 = results_by_id["T1"]
    assert t1.intent is Intent.DEPOSIT_ISSUE
    assert t1.retrieved_articles[0] == "A1"
    assert "[A1]" in t1.reply_draft


def test_output_matches_required_schema(sample_run):
    payload = results_payload(sample_run)
    assert [r["ticket_id"] for r in payload] == ["T1", "T2", "T3", "T4", "T5"]
    for entry in payload:
        assert set(entry) == set(Result.model_fields)
        Result.model_validate(entry)
        assert 1 <= len(entry["retrieved_articles"]) <= 3
        assert 0.0 <= entry["confidence"] <= 1.0


def test_expected_intents_and_articles(results_by_id):
    expected = {
        "T1": ("deposit_issue", "A1"),
        "T2": ("password_reset", "A2"),
        "T3": ("withdrawal_issue", "A3"),
        "T4": ("trading_advice_request", "A5"),
        "T5": ("account_lock", "A4"),
    }
    for tid, (intent, article) in expected.items():
        assert results_by_id[tid].intent.value == intent
        assert results_by_id[tid].retrieved_articles[0] == article


def test_withdrawal_decline_is_escalated(results_by_id):
    t3 = results_by_id["T3"]
    assert t3.needs_human_escalation
    assert SafetyFlag.POLICY_SENSITIVE in t3.safety_flags
    assert SafetyFlag.ACCOUNT_SPECIFIC_REQUEST in t3.safety_flags


def test_every_reply_line_is_traceable(sample_run):
    """Each quoted KB line in a grounded reply appears verbatim in the cited article."""
    for ctx in sample_run.contexts:
        for line in ctx.draft.claim_lines:
            assert line.sources
            assert any(line.text in sample_run.kb[a].body for a in line.sources)


def test_runs_are_reproducible():
    runs = [
        results_payload(build_pipeline("offline", log=quiet).run(FIXTURES / "tickets.json", FIXTURES / "kb_articles.json"))
        for _ in range(2)
    ]
    assert runs[0] == runs[1]


def test_write_outputs_produces_valid_artifacts(sample_run, tmp_path):
    write_outputs(sample_run, tmp_path)
    assert len(json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))) == 5
    debug = json.loads((tmp_path / "debug_report.json").read_text(encoding="utf-8"))
    assert {t["ticket_id"] for t in debug["tickets"]} == {"T1", "T2", "T3", "T4", "T5"}
    assert "escalation_reasons" in debug["tickets"][0]
    assert (tmp_path / "debug_report.md").read_text(encoding="utf-8").startswith("# Debug report")
