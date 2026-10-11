"""Guard, validation and loader units."""
import json

import pytest

from pipeline.guard import find_unsupported_claims
from pipeline.loader import InputError, load_tickets
from pipeline.validation import validate_result

GOOD = {
    "ticket_id": "T1",
    "intent": "deposit_issue",
    "retrieved_articles": ["A1"],
    "reply_draft": "Summary: Client has a deposit-related issue.",
    "grounded": True,
    "confidence": 0.9,
    "needs_human_escalation": False,
    "safety_flags": [],
}
KNOWN = {"A1", "A2", "A3", "A4", "A5"}


def check(**overrides):
    return validate_result({**GOOD, **overrides}, known_article_ids=KNOWN,
                           ticket_text=overrides.pop("_ticket", "deposit missing"), source_text="")


@pytest.mark.parametrize("text", [
    "Your deposit will be credited within 24 hours.",
    "We guarantee that the withdrawal is approved.",
    "I can see your account shows a pending payment.",
    "This asset is going to rise, you should buy now.",
])
def test_risky_claims_are_detected(text):
    assert find_unsupported_claims(text)


def test_timeframe_present_in_source_is_allowed():
    assert not find_unsupported_claims("Allow 24 hours.", source_text="Processing can take 24 hours.")


def test_valid_result_passes():
    assert check() == []


@pytest.mark.parametrize("overrides, fragment", [
    ({"intent": "refund"}, "schema"),
    ({"confidence": 1.5}, "schema"),
    ({"retrieved_articles": ["A9"]}, "unknown article"),
    ({"retrieved_articles": ["A1", "A1"]}, "duplicate"),
    ({"retrieved_articles": [], "grounded": True}, "grounded=true"),
    ({"grounded": False}, "neither a safety flag"),
    ({"extra": 1}, "schema"),
    ({"reply_draft": "Funds will be credited within 2 days."}, "unsupported claims"),
])
def test_invalid_results_are_caught(overrides, fragment):
    assert any(fragment in e for e in check(**overrides))


def test_unrefused_advice_is_caught():
    errors = validate_result(
        {**GOOD, "intent": "trading_advice_request", "safety_flags": ["advice_request"],
         "reply_draft": "Gold is a good pick."},
        known_article_ids=KNOWN, ticket_text="Which asset will go up today?", source_text="",
    )
    assert any("not refused" in e for e in errors)


def test_misclassified_advice_is_caught():
    errors = validate_result(GOOD, known_article_ids=KNOWN, ticket_text="Which stock should I buy to profit?", source_text="")
    assert any("advice request" in e for e in errors)


def test_loader_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps([{"id": "T1", "message": "a"}, {"id": "T1", "message": "b"}]), encoding="utf-8")
    with pytest.raises(InputError, match="duplicate"):
        load_tickets(path)


def test_loader_rejects_malformed_ticket(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps([{"id": "T1"}]), encoding="utf-8")
    with pytest.raises(InputError):
        load_tickets(path)
