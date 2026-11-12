import sys
from typing import Any, Callable, Optional

from coaudit.contracts import ReviewStatus
from coaudit.service import AuditService

FAULT_METRIC = "operating_cash_flow"
FAULT_FACTOR = 1000.0


def percent(report: Optional[dict[str, Any]]) -> str:
    if not report or report.get("overall_accuracy") is None:
        return "n/a"
    return f"{100 * report['overall_accuracy']:.0f}%"


def run_demo(service: AuditService, ticker: str, say: Callable[[str], None]) -> dict[str, Any]:
    say(f"STEP 1  Start an audit of {ticker} with the fault switch ON (a simulation).")
    thread_id = service.start_run(ticker, fault_switch=True)
    run = service.get_run(thread_id)
    say(f"        Status: {run['status']}   (from saved copy: {run['from_cache']})")
    reviews = service.list_reviews(ReviewStatus.PENDING, thread_id)
    for review in reviews:
        say(f"        Waiting for a human: {review.metric_name.value}")
        say(f"        Reason: {review.reason}")
    metric = next(item for item in run["metrics"] if item["metric_name"] == FAULT_METRIC)
    say(f"        The corrupted value is {metric['value']:,.0f} (it should be 1,000 times smaller).")
    say("")
    say("STEP 2  The reviewer checks each flagged value against its source line.")
    resumed = False
    for review in reviews:
        name = review.metric_name.value
        if name == FAULT_METRIC:
            corrected = metric["value"] / FAULT_FACTOR
            outcome = service.decide(review.id, "EDIT", corrected, "unit fixed", "reviewer-1")
            say(f"        {name}: corrected to {corrected:,.0f}")
        else:
            outcome = service.decide(
                review.id, "APPROVE", None, "real change, confirmed in the filing", "reviewer-1"
            )
            say(f"        {name}: approved (a real change, not an error)")
        resumed = outcome["resumed"]
    say(f"        All reviews decided. The run resumed by itself: {resumed}")
    say("")
    run = service.get_run(thread_id)
    say(f"STEP 3  Final status: {run['status']}")
    say(f"        Accuracy against the SEC before the fix: {percent(run['report_before_review'])}")
    say(f"        Accuracy against the SEC after the fix:  {percent(run['report'])}")
    say("")
    say("STEP 4  The audit log entry (it can only be added to, never edited or deleted):")
    for entry in service.list_audit(thread_id):
        say(
            f"        #{entry.audit_id} {entry.metric_name.value} {entry.decision.value} "
            f"{entry.original_value:,.0f} -> {entry.final_value:,.0f} by {entry.reviewer_id}, "
            f"simulated={entry.simulated}"
        )
    say("")
    ledger = ", ".join(f"{step} x{count}" for step, count in run["step_ledger"].items())
    say(f"STEP 5  Every step ran exactly once, even after the pause: {ledger}")
    return run


def main(arguments: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from coaudit.bootstrap import build_default_service

    ticker = next((item for item in arguments if not item.startswith("--")), "AAPL")
    offline = True if "--offline" in arguments else None
    service = build_default_service(offline=offline)
    try:
        run_demo(service, ticker.upper(), print)
    finally:
        service.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
