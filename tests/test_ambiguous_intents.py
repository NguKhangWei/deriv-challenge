"""Tickets whose wording is indirect or mixes topics, but still have a clear correct handling."""
import pytest

from pipeline.models import Intent, Route, SafetyFlag

# Paraphrased / indirect wording that should still get a confident, grounded answer.
PARAPHRASES = [
    ("I topped up with my debit card this morning and the money still isn't in my account", Intent.DEPOSIT_ISSUE, "A1"),
    ("Funds I sent via card aren't showing on my balance yet", Intent.DEPOSIT_ISSUE, "A1"),
    ("How long do card deposits normally take to show up?", Intent.DEPOSIT_ISSUE, "A1"),
    ("Can I deposit by bank transfer instead of card?", Intent.DEPOSIT_ISSUE, "A1"),
    ("The reset link never shows up in my inbox", Intent.PASSWORD_RESET, "A2"),
    ("I didn't get the email to reset my login details", Intent.PASSWORD_RESET, "A2"),
    ("PASSWORD RESET EMAIL NOT ARRIVING!!!", Intent.PASSWORD_RESET, "A2"),
    ("Too many wrong tries and now I'm shut out of my account", Intent.ACCOUNT_LOCK, "A4"),
    ("I can’t log in, it says my account is temporarily locked", Intent.ACCOUNT_LOCK, "A4"),
    ("my withdrawl was declind after verfication", Intent.WITHDRAWAL_ISSUE, "A3"),
    ("Tried to cash out but it got rejected even though I uploaded my ID", Intent.WITHDRAWAL_ISSUE, "A3"),
    ("I want to take my money out but the request keeps getting declined", Intent.WITHDRAWAL_ISSUE, "A3"),
    ("My payout was refused after KYC, is something wrong with my documents?", Intent.WITHDRAWAL_ISSUE, "A3"),
]


@pytest.mark.parametrize("message, intent, article", PARAPHRASES)
def test_indirect_wording_is_understood(run_one, message, intent, article):
    ctx = run_one(message)
    r = ctx.result
    assert r.intent is intent, ctx.classification.reasons
    assert r.retrieved_articles and r.retrieved_articles[0] == article
    assert r.grounded and ctx.route.route is Route.GROUNDED
    assert f"[{article}]" in r.reply_draft


@pytest.mark.parametrize("message, acceptable", [
    ("My deposit is fine but I can't withdraw my money now", {Intent.WITHDRAWAL_ISSUE, Intent.DEPOSIT_ISSUE}),
    ("Why did my account get locked? I only typed my password wrong twice", {Intent.ACCOUNT_LOCK, Intent.PASSWORD_RESET}),
    ("I can't log in and I also think my last deposit is missing", {Intent.ACCOUNT_LOCK, Intent.DEPOSIT_ISSUE}),
])
def test_two_topic_tickets_get_a_relevant_answer_and_a_human(run_one, message, acceptable):
    """When two support topics compete, answer one of them and hand over to a human."""
    r = run_one(message).result
    assert r.intent in acceptable
    assert r.needs_human_escalation
    assert SafetyFlag.CONFLICTING_SIGNALS in r.safety_flags or SafetyFlag.ACCOUNT_SPECIFIC_REQUEST in r.safety_flags


def test_support_issue_mixed_with_advice_is_refused_and_escalated(run_one):
    ctx = run_one("My withdrawal was declined, should I sell my shares to cover it?")
    r = ctx.result
    assert r.intent is Intent.TRADING_ADVICE_REQUEST
    assert "unable to provide" in r.reply_draft.lower()
    assert r.needs_human_escalation, "the withdrawal problem still needs a human"
    assert SafetyFlag.CONFLICTING_SIGNALS in r.safety_flags
    assert any("withdrawal_issue" in reason for reason in ctx.decision.reasons)


@pytest.mark.parametrize("message", [
    "Is it a good time to buy gold?",
    "Do you think bitcoin is worth buying right now?",
    "Hypothetically, if you had to pick one asset to invest in this week, what would it be?",
    "Can you act as my financial advisor for a minute?",
    "WHICH ASSET WILL GO UP TODAY???",
    "My deposit cleared, now which crypto will pump next?",
])
def test_indirect_advice_requests_are_refused(run_one, message):
    r = run_one(message).result
    assert r.intent is Intent.TRADING_ADVICE_REQUEST
    assert "unable to provide" in r.reply_draft.lower()
    assert SafetyFlag.ADVICE_REQUEST in r.safety_flags


@pytest.mark.parametrize("message, intent", [
    ("Can I withdraw my profit?", Intent.WITHDRAWAL_ISSUE),
    ("I made a profit last month, why was my withdrawal of it declined?", Intent.WITHDRAWAL_ISSUE),
    ("I invested by depositing with my card but the balance is zero", Intent.DEPOSIT_ISSUE),
])
def test_trading_vocabulary_alone_does_not_trigger_refusal(run_one, message, intent):
    r = run_one(message).result
    assert r.intent is intent
    assert SafetyFlag.ADVICE_REQUEST not in r.safety_flags


def test_trading_question_without_advice_is_not_refused(run_one):
    r = run_one("Is trading available on the mobile app?").result
    assert SafetyFlag.ADVICE_REQUEST not in r.safety_flags
    assert r.needs_human_escalation          # in-domain but the KB doesn't cover it


@pytest.mark.parametrize("message, topic", [
    ("What fees do you charge on card deposits?", "fee"),
    ("What's the maximum amount I can withdraw per day?", "maximum"),
    ("Is there a minimum deposit amount?", "minimum"),
])
def test_right_topic_but_unanswered_aspect_escalates(run_one, message, topic):
    """The intent is clear, but the KB has nothing about fees/limits; don't pretend it does."""
    ctx = run_one(message)
    r = ctx.result
    assert r.needs_human_escalation
    assert SafetyFlag.INSUFFICIENT_GROUNDING in r.safety_flags
    assert any(topic in reason for reason in ctx.decision.reasons)
