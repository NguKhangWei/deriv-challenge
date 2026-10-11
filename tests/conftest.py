import json
from pathlib import Path

import pytest

from pipeline.runner import build_pipeline

FIXTURES = Path(__file__).parent / "fixtures"


def quiet(_msg: str) -> None:
    pass


@pytest.fixture(scope="session")
def sample_run():
    return build_pipeline("offline", log=quiet).run(FIXTURES / "tickets.json", FIXTURES / "kb_articles.json")


@pytest.fixture(scope="session")
def results_by_id(sample_run):
    return {r.ticket_id: r for r in sample_run.results}


@pytest.fixture
def run_tickets(tmp_path):
    """Run a pipeline on raw ticket dicts against the sample KB; returns the full RunOutput."""

    def _run(tickets: list[dict], *, mode: str = "offline", client=None, kb_path=FIXTURES / "kb_articles.json"):
        path = tmp_path / "tickets.json"
        path.write_text(json.dumps(tickets, ensure_ascii=False), encoding="utf-8")
        return build_pipeline(mode, client, log=quiet).run(path, kb_path)

    return _run


@pytest.fixture
def run_messages(run_tickets):
    """Run the offline pipeline on ad-hoc messages; returns the results."""

    def _run(*messages: str, language: str = "en"):
        tickets = [{"id": f"X{i}", "message": m, "language": language} for i, m in enumerate(messages)]
        return run_tickets(tickets).results

    return _run


@pytest.fixture
def run_one(run_tickets):
    """Run one message and return its TicketContext (route, draft, decision, result)."""

    def _run(message: str, language: str = "en", **kwargs):
        return run_tickets([{"id": "X", "message": message, "language": language}], **kwargs).contexts[0]

    return _run
