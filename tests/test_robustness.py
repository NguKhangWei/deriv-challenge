"""Input-handling and determinism edge cases."""
import json
import random

import pytest

from pipeline.loader import InputError
from pipeline.models import Intent, Result, SafetyFlag
from pipeline.report import results_payload

from .conftest import FIXTURES


# --- Language handling ---------------------------------------------------------------

@pytest.mark.parametrize("language", ["en", "EN", "en-US", "en_GB", " en "])
def test_english_language_variants_are_supported(run_one, language):
    r = run_one("I forgot my password", language=language).result
    assert r.intent is Intent.PASSWORD_RESET
    assert SafetyFlag.UNSUPPORTED_LANGUAGE not in r.safety_flags


@pytest.mark.parametrize("language", ["fr", "es", "zh", "", None])
def test_other_or_missing_languages_escalate(run_one, language):
    r = run_one("Je ne peux pas retirer mon argent", language=language).result
    assert r.needs_human_escalation
    assert SafetyFlag.UNSUPPORTED_LANGUAGE in r.safety_flags


@pytest.mark.parametrize("message", [
    "Mi depósito no aparece en mi cuenta, por favor ayuda",
    "Je ne peux pas retirer mon argent de mon compte",
    "Saya tidak boleh log masuk ke akaun saya",
])
def test_non_english_text_labelled_en_escalates_instead_of_being_rejected(run_one, message):
    r = run_one(message).result
    assert SafetyFlag.UNSUPPORTED_LANGUAGE in r.safety_flags
    assert SafetyFlag.OUT_OF_SCOPE not in r.safety_flags
    assert r.needs_human_escalation


def test_advice_request_is_refused_even_with_wrong_language_label(run_one):
    r = run_one("Which asset will go up today?", language="de").result
    assert r.intent is Intent.TRADING_ADVICE_REQUEST
    assert "unable to provide" in r.reply_draft.lower()


# --- Text normalisation --------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "ｍｙ ｐａｓｓｗｏｒｄ ｒｅｓｅｔ ｅｍａｉｌ is missing",  # full-width
    "my pass​word reset email is missing",                                     # zero-width
    "my\tpassword\n\nreset   email is missing",                                     # whitespace
])
def test_unusual_characters_do_not_hide_intent(run_one, message):
    assert run_one(message).result.intent is Intent.PASSWORD_RESET


def test_keyword_stuffing_keeps_confidence_in_range(run_one):
    r = run_one("deposit " * 200 + "password " * 200).result
    assert 0.0 <= r.confidence <= 1.0
    assert r.needs_human_escalation        # two strong intents conflict


def test_overlong_message_is_truncated_but_still_answered(run_one):
    r = run_one("My card deposit is not showing in my balance. " + "Please help. " * 600).result
    assert SafetyFlag.INPUT_TRUNCATED in r.safety_flags
    assert r.intent is Intent.DEPOSIT_ISSUE


# --- Input files ------------------------------------------------------------------------

def test_empty_ticket_list_produces_empty_results(run_tickets):
    assert run_tickets([]).results == []


def test_unicode_ticket_ids_are_preserved(run_tickets):
    run = run_tickets([{"id": "Ticket-é中-1", "message": "I forgot my password", "language": "en"}])
    assert run.results[0].ticket_id == "Ticket-é中-1"


def test_extra_ticket_fields_are_ignored(run_tickets):
    run = run_tickets([{"id": "T1", "message": "I forgot my password", "language": "en", "priority": "high"}])
    assert set(results_payload(run)[0]) == set(Result.model_fields)


@pytest.mark.parametrize("bad, fragment", [
    ('[{"id": "T1"}]', "message"),
    ('[{"id": "", "message": "hi"}]', "id"),
    ('{"id": "T1", "message": "hi"}', "JSON array"),
    ("[{]", "invalid JSON"),
])
def test_malformed_ticket_files_fail_with_a_clear_error(tmp_path, bad, fragment):
    from pipeline.loader import load_tickets

    path = tmp_path / "tickets.json"
    path.write_text(bad, encoding="utf-8")
    with pytest.raises(InputError, match=fragment):
        load_tickets(path)


def test_empty_knowledge_base_is_rejected(run_tickets, tmp_path):
    kb = tmp_path / "kb.json"
    kb.write_text("[]", encoding="utf-8")
    with pytest.raises(InputError, match="empty"):
        run_tickets([{"id": "T1", "message": "hi", "language": "en"}], kb_path=kb)


# --- Determinism ------------------------------------------------------------------------

def test_kb_order_does_not_change_results(run_tickets, tmp_path):
    tickets = json.loads((FIXTURES / "tickets.json").read_text(encoding="utf-8"))
    articles = json.loads((FIXTURES / "kb_articles.json").read_text(encoding="utf-8"))
    baseline = results_payload(run_tickets(tickets))
    for seed in range(3):
        shuffled = articles[:]
        random.Random(seed).shuffle(shuffled)
        kb = tmp_path / f"kb_{seed}.json"
        kb.write_text(json.dumps(shuffled), encoding="utf-8")
        assert results_payload(run_tickets(tickets, kb_path=kb)) == baseline


def test_ticket_order_is_preserved_and_results_are_independent(run_tickets):
    tickets = json.loads((FIXTURES / "tickets.json").read_text(encoding="utf-8"))
    forward = {r["ticket_id"]: r for r in results_payload(run_tickets(tickets))}
    reversed_run = results_payload(run_tickets(tickets[::-1]))
    assert [r["ticket_id"] for r in reversed_run] == [t["id"] for t in tickets[::-1]]
    assert all(r == forward[r["ticket_id"]] for r in reversed_run)


def test_duplicate_messages_get_identical_results(run_tickets):
    msg = "Why was my withdrawal declined after verification?"
    a, b = results_payload(run_tickets([
        {"id": "A", "message": msg, "language": "en"},
        {"id": "B", "message": msg, "language": "en"},
    ]))
    assert {**a, "ticket_id": None} == {**b, "ticket_id": None}
