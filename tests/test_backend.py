import sqlite3
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from coaudit.api.app import create_app
from coaudit.contracts import Config, FilingBundle, MetricName, ReviewStatus
from coaudit.extraction.rules import extract_metrics_by_rules
from coaudit.groundtruth.xbrl import GroundTruthValue
from coaudit.parsing.tables import extract_tables
from coaudit.service import AuditService
from coaudit.store.database import ReviewAlreadyDecidedError, ReviewNotFoundError
from test_crosschecks import FULL_HTML, PERIOD_END


def make_bundle(tmp_path):
    html_path = tmp_path / "filing.html"
    html_path.write_text(FULL_HTML, encoding="utf-8")
    return FilingBundle(
        ticker="EXM",
        cik="0000320193",
        company_name="Example Corp",
        accession_number="0000320193-26-000026",
        url="https://www.sec.gov/Archives/edgar/data/320193/x/doc.htm",
        downloaded_at=datetime(2026, 11, 12, 16, 0, 0),
        document_hash="a" * 64,
        html_path=str(html_path),
        fiscal_year_end=PERIOD_END,
        from_cache=True,
    )


def make_service(tmp_path):
    bundle = make_bundle(tmp_path)
    metrics = extract_metrics_by_rules(bundle, extract_tables(FULL_HTML))
    truth = {
        metric.metric_name: GroundTruthValue(
            metric=metric.metric_name,
            tag="Test",
            value=metric.value,
            period_end=PERIOD_END,
            accession_number=bundle.accession_number,
            form="10-K",
            filed="2026-10-30",
        )
        for metric in metrics
    }
    return AuditService(
        tmp_path / "coaudit.db",
        tmp_path / "checkpoints.db",
        lambda ticker: bundle,
        lambda found: truth,
    )


@pytest.fixture
def service(tmp_path):
    instance = make_service(tmp_path)
    yield instance
    instance.close()


def pending_ids(service, thread_id):
    return [r.id for r in service.list_reviews(ReviewStatus.PENDING, thread_id)]


def test_a_clean_run_completes_without_pausing(service):
    thread_id = service.start_run("exm")
    run = service.get_run(thread_id)
    assert run["status"] == "COMPLETED"
    assert run["pending_reviews"] == 0
    assert run["report"]["overall_accuracy"] == 1.0
    assert run["from_cache"] is True
    assert set(run["step_ledger"]) == {
        "fetch", "extract", "analyze", "prepare_review", "finalize", "verify"
    }
    assert all(count == 1 for count in run["step_ledger"].values())


def test_a_flagged_run_pauses_and_lists_pending_reviews(service):
    service.set_config(Config(year_on_year_threshold=0.05))
    thread_id = service.start_run("EXM")
    run = service.get_run(thread_id)
    assert run["status"] == "PAUSED"
    assert run["pending_reviews"] > 0
    assert run["report"] is None
    reviews = service.list_reviews(ReviewStatus.PENDING, thread_id)
    assert len(reviews) == run["pending_reviews"]
    assert all("year_on_year" in review.reason for review in reviews)


def test_resuming_never_repeats_the_earlier_steps(service):
    service.set_config(Config(year_on_year_threshold=0.05))
    thread_id = service.start_run("EXM")
    ids = pending_ids(service, thread_id)
    outcomes = [service.decide(i, "APPROVE", None, "ok", "reviewer-1") for i in ids]
    assert [o["resumed"] for o in outcomes] == [False] * (len(ids) - 1) + [True]
    run = service.get_run(thread_id)
    assert run["status"] == "COMPLETED"
    assert run["completed_reviews"] == len(ids)
    assert run["step_ledger"]["fetch"] == 1
    assert run["step_ledger"]["extract"] == 1
    assert run["step_ledger"]["analyze"] == 1
    assert len(service.list_audit(thread_id)) == len(ids)


def test_the_fault_switch_pauses_and_a_correction_restores_accuracy(service):
    thread_id = service.start_run("EXM", fault_switch=True)
    run = service.get_run(thread_id)
    assert run["status"] == "PAUSED"
    assert run["fault_injected"] is True
    reviews = service.list_reviews(ReviewStatus.PENDING, thread_id)
    assert [review.metric_name for review in reviews] == [MetricName.OPERATING_CASH_FLOW]
    corrupted = next(m for m in run["metrics"] if m["metric_name"] == "operating_cash_flow")
    assert corrupted["simulated_fault"] is True
    outcome = service.decide(
        reviews[0].id, "EDIT", corrupted["value"] / 1000, "unit fixed", "reviewer-1"
    )
    assert outcome["audit_entry"]["decision"] == "CORRECTED"
    assert outcome["audit_entry"]["simulated"] is True
    assert outcome["run_status"] == "COMPLETED"
    run = service.get_run(thread_id)
    before = {o["metric_name"]: o for o in run["report_before_review"]["outcomes"]}
    after = {o["metric_name"]: o for o in run["report"]["outcomes"]}
    assert before["operating_cash_flow"]["is_correct"] is False
    assert after["operating_cash_flow"]["is_correct"] is True
    assert run["report"]["overall_accuracy"] == 1.0
    assert run["report_before_review"]["overall_accuracy"] < 1.0


def test_a_rejected_value_is_left_out_of_the_final_metrics(service):
    thread_id = service.start_run("EXM", fault_switch=True)
    review = service.list_reviews(ReviewStatus.PENDING, thread_id)[0]
    service.decide(review.id, "REJECT", None, "cannot trust it", "reviewer-1")
    run = service.get_run(thread_id)
    names = [m["metric_name"] for m in run["final_metrics"]]
    assert "operating_cash_flow" not in names
    assert len(names) == len(run["metrics"]) - 1
    entry = service.list_audit(thread_id)[0]
    assert entry.final_value is None


def test_a_paused_run_survives_a_restart(tmp_path):
    first = make_service(tmp_path)
    thread_id = first.start_run("EXM", fault_switch=True)
    review = first.list_reviews(ReviewStatus.PENDING, thread_id)[0]
    first.close()
    second = make_service(tmp_path)
    try:
        assert second.get_run(thread_id)["status"] == "PAUSED"
        value = second.get_run(thread_id)["metrics"][5]["value"]
        outcome = second.decide(review.id, "APPROVE", None, "", "reviewer-2")
        assert outcome["run_status"] == "COMPLETED"
        assert second.get_run(thread_id)["step_ledger"]["fetch"] == 1
        assert value > 0
    finally:
        second.close()


def test_bad_decisions_are_refused(service):
    thread_id = service.start_run("EXM", fault_switch=True)
    review_id = pending_ids(service, thread_id)[0]
    with pytest.raises(ReviewNotFoundError):
        service.decide("missing", "APPROVE", None, "", "reviewer-1")
    with pytest.raises(ValueError):
        service.decide(review_id, "EDIT", None, "", "reviewer-1")
    with pytest.raises(ValueError):
        service.decide(review_id, "MAYBE", None, "", "reviewer-1")
    with pytest.raises(ValueError):
        service.decide(review_id, "APPROVE", None, "", "  ")
    service.decide(review_id, "APPROVE", None, "", "reviewer-1")
    with pytest.raises(ReviewAlreadyDecidedError):
        service.decide(review_id, "APPROVE", None, "", "reviewer-1")


def test_the_audit_log_rejects_updates_and_deletes_from_the_app(service):
    thread_id = service.start_run("EXM", fault_switch=True)
    service.decide(pending_ids(service, thread_id)[0], "APPROVE", None, "", "reviewer-1")
    connection = service.store._connection
    for statement in (
        "UPDATE audit_log SET decision = 'REJECTED'",
        "DELETE FROM audit_log",
        "DROP TABLE audit_log",
        "ALTER TABLE audit_log ADD COLUMN extra TEXT",
        "DROP TRIGGER audit_log_no_update",
    ):
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(statement)
    assert len(service.list_audit()) == 1


def test_the_database_itself_rejects_edits_from_outside_the_app(service, tmp_path):
    thread_id = service.start_run("EXM", fault_switch=True)
    service.decide(pending_ids(service, thread_id)[0], "APPROVE", None, "", "reviewer-1")
    outside = sqlite3.connect(str(tmp_path / "coaudit.db"))
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            outside.execute("UPDATE audit_log SET decision = 'REJECTED'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            outside.execute("DELETE FROM audit_log")
    finally:
        outside.close()


def test_settings_persist_between_service_instances(tmp_path):
    first = make_service(tmp_path)
    first.set_config(Config(confidence_threshold=0.9))
    first.close()
    second = make_service(tmp_path)
    try:
        assert second.get_config().confidence_threshold == 0.9
    finally:
        second.close()


@pytest.fixture
def client(service):
    return TestClient(create_app(service))


def test_the_api_health_and_config(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/config").json()["confidence_threshold"] == 0.85
    updated = client.put("/config", json={"year_on_year_threshold": 0.05}).json()
    assert updated["year_on_year_threshold"] == 0.05
    assert client.put("/config", json={"confidence_threshold": 3}).status_code == 422


def test_the_api_runs_a_full_review_cycle(client):
    client.put("/config", json={"year_on_year_threshold": 0.05})
    started = client.post("/runs", json={"ticker": "EXM"}).json()
    thread_id = started["thread_id"]
    assert started["status"] == "PAUSED"
    reviews = client.get("/reviews", params={"status": "PENDING"}).json()
    assert reviews and all(review["thread_id"] == thread_id for review in reviews)
    for review in reviews:
        response = client.post(
            f"/reviews/{review['id']}/decision",
            json={"action": "APPROVE", "reviewer_id": "reviewer-1", "notes": "fine"},
        )
        assert response.status_code == 200
    assert client.get(f"/runs/{thread_id}").json()["status"] == "COMPLETED"
    assert client.get("/reviews", params={"status": "PENDING"}).json() == []
    assert len(client.get("/reviews", params={"status": "DONE"}).json()) == len(reviews)
    audit = client.get("/audit", params={"thread_id": thread_id}).json()
    assert len(audit) == len(reviews)
    report = client.get(f"/report/{thread_id}").json()
    assert report["report"]["overall_accuracy"] == 1.0
    assert [run["thread_id"] for run in client.get("/runs").json()] == [thread_id]


def test_reviews_carry_the_metric_details_the_dashboard_needs(service):
    thread_id = service.start_run("EXM", fault_switch=True)
    review = service.list_reviews_detailed(ReviewStatus.PENDING, thread_id)[0]
    assert review["metric"]["metric_name"] == "operating_cash_flow"
    assert review["metric"]["value"] > 0
    assert review["metric"]["confidence"] is not None
    assert "Cash generated by operating activities" in review["metric"]["source_snippet"]
    assert any("year_on_year" in reason for reason in review["reasons"])
    assert review["company"] == "Example Corp"
    assert review["data_source"] == "CACHE"


def test_runs_report_where_the_data_came_from(client):
    run = client.post("/runs", json={"ticker": "EXM"}).json()
    assert run["data_source"] == "CACHE"
    assert run["from_cache"] is True
    assert "thread_id" in run


def test_the_decision_names_used_by_the_dashboard_are_accepted(client):
    client.post("/runs", json={"ticker": "EXM", "fault_switch": True})
    review = client.get("/reviews", params={"status": "PENDING"}).json()[0]
    response = client.post(
        f"/reviews/{review['id']}/decision",
        json={
            "action": "CORRECTED",
            "corrected_value": review["metric"]["value"] / 1000,
            "reviewer_id": "reviewer-1",
            "notes": "unit fixed",
        },
    )
    assert response.status_code == 200
    assert response.json()["audit_entry"]["decision"] == "CORRECTED"


def test_the_api_reports_errors_clearly(client):
    assert client.get("/runs/unknown").status_code == 404
    assert client.get("/report/unknown").status_code == 404
    assert client.post("/runs", json={"ticker": "not a ticker!"}).status_code == 422
    started = client.post("/runs", json={"ticker": "EXM", "fault_switch": True}).json()
    review_id = client.get("/reviews", params={"status": "PENDING"}).json()[0]["id"]
    missing = client.post(
        "/reviews/nothing/decision", json={"action": "APPROVE", "reviewer_id": "r"}
    )
    assert missing.status_code == 404
    no_value = client.post(
        f"/reviews/{review_id}/decision", json={"action": "EDIT", "reviewer_id": "r"}
    )
    assert no_value.status_code == 422
    ok = client.post(
        f"/reviews/{review_id}/decision", json={"action": "REJECT", "reviewer_id": "r"}
    )
    assert ok.status_code == 200
    again = client.post(
        f"/reviews/{review_id}/decision", json={"action": "REJECT", "reviewer_id": "r"}
    )
    assert again.status_code == 409
    assert started["fault_injected"] is True
