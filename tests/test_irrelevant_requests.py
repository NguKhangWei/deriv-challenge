"""Requests the pipeline is not built for: direct rejection, or fallback when in-domain."""
import pytest

from pipeline.models import Intent, Route, SafetyFlag

OFF_TOPIC = [
    "Tell me a joke",
    "What's the capital of France?",
    "Can you write Python code that sorts a list?",
    "Who will win the football match tonight?",
    "Recommend a good restaurant near me",
    "Translate 'good morning' into Spanish",
    "Can you help me with my maths homework?",
    "What is your favourite colour?",
    "asdfghjkl qwerty",
    "12345",
    "https://example.com/free-gift",
    "Hi",
]


@pytest.mark.parametrize("message", OFF_TOPIC)
def test_off_topic_requests_get_a_direct_rejection(run_one, message):
    ctx = run_one(message)
    r = ctx.result
    assert ctx.route.route is Route.REJECT
    assert r.intent is Intent.OTHER
    assert r.retrieved_articles == [] and not r.grounded
    assert SafetyFlag.OUT_OF_SCOPE in r.safety_flags
    assert not r.needs_human_escalation
    assert "unable to assist" in r.reply_draft


@pytest.mark.parametrize("message", ["\U0001F642\U0001F642\U0001F642", "?!...", "", "​​"])
def test_contentless_messages_are_rejected_as_invalid(run_one, message):
    r = run_one(message).result
    assert SafetyFlag.INVALID_INPUT in r.safety_flags
    assert r.retrieved_articles == []
    assert "please describe the issue" in r.reply_draft


IN_DOMAIN_UNCOVERED = [
    "How do I enable two-factor authentication?",
    "How do I close my account permanently?",
    "Can I get a copy of my monthly account statement?",
    "Where can I update my phone number?",
    "Do you offer a deposit bonus for new clients?",
    "How do I change the email address on my profile?",
]


@pytest.mark.parametrize("message", IN_DOMAIN_UNCOVERED)
def test_uncovered_support_questions_fall_back_to_a_human(run_one, message):
    """Platform questions the KB can't answer: no weak, unrelated article; a human takes over."""
    ctx = run_one(message)
    r = ctx.result
    assert r.needs_human_escalation
    assert SafetyFlag.INSUFFICIENT_GROUNDING in r.safety_flags
    assert SafetyFlag.OUT_OF_SCOPE not in r.safety_flags, "support questions must not be rejected"
    if ctx.route.route is Route.FALLBACK:
        assert r.retrieved_articles == []
        assert "No relevant knowledge-base article" in r.reply_draft


def test_fallback_never_cites_a_weak_match(run_one):
    ctx = run_one("How do I change my email address on my account?")
    assert ctx.route.route is Route.FALLBACK
    assert ctx.result.retrieved_articles == []
    assert "[A" not in ctx.result.reply_draft


def test_off_topic_ticket_does_not_affect_neighbours(run_tickets):
    run = run_tickets([
        {"id": "1", "message": "Tell me a joke", "language": "en"},
        {"id": "2", "message": "I forgot my password and the reset email never came", "language": "en"},
    ])
    joke, password = run.results
    assert SafetyFlag.OUT_OF_SCOPE in joke.safety_flags
    assert password.intent is Intent.PASSWORD_RESET and password.retrieved_articles[0] == "A2"
