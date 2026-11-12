import pytest

from coaudit.contracts import Config
from coaudit.demo.__main__ import run_demo
from test_backend import make_service


@pytest.fixture
def service(tmp_path):
    instance = make_service(tmp_path)
    yield instance
    instance.close()


def test_the_demo_walks_through_pause_fix_and_resume(service):
    lines = []
    run = run_demo(service, "EXM", lines.append)
    text = "\n".join(lines)
    assert run["status"] == "COMPLETED"
    assert "Status: PAUSED" in text
    assert "Waiting for a human: operating_cash_flow" in text
    assert "Final status: COMPLETED" in text
    assert "after the fix:  100%" in text
    assert "simulated=True" in text
    assert "fetch x1" in text


def test_the_demo_reviews_every_pending_value_and_still_completes(service):
    service.set_config(Config(year_on_year_threshold=0.05))
    lines = []
    run = run_demo(service, "EXM", lines.append)
    text = "\n".join(lines)
    assert run["status"] == "COMPLETED"
    assert run["pending_reviews"] == 0
    assert "operating_cash_flow: corrected to" in text
    assert "approved (a real change, not an error)" in text
    assert "The run resumed by itself: True" in text
    assert len(service.list_audit(run["thread_id"])) == run["completed_reviews"]


def test_the_demo_shows_lower_accuracy_before_the_fix(service):
    lines = []
    run_demo(service, "EXM", lines.append)
    before = next(line for line in lines if "before the fix" in line)
    assert "100%" not in before
