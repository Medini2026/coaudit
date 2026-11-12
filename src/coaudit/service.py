import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from coaudit.contracts import (
    AuditEntry,
    Config,
    Decision,
    FilingBundle,
    MetricName,
    ReviewItem,
    ReviewStatus,
)
from coaudit.groundtruth.xbrl import GroundTruthValue
from coaudit.store.database import ReviewNotFoundError, Store
from coaudit.workflow.graph import Dependencies, build_graph

CONFIG_SETTING = "config"
ACTION_ALIASES = {
    "APPROVE": Decision.APPROVED,
    "APPROVED": Decision.APPROVED,
    "EDIT": Decision.CORRECTED,
    "CORRECT": Decision.CORRECTED,
    "CORRECTED": Decision.CORRECTED,
    "REJECT": Decision.REJECTED,
    "REJECTED": Decision.REJECTED,
}


class RunNotFoundError(Exception):
    pass


def data_source_label(bundle: dict[str, Any]) -> Optional[str]:
    if not bundle:
        return None
    return "CACHE" if bundle.get("from_cache") else "LIVE_SEC"


def parse_decision(action: str) -> Decision:
    decision = ACTION_ALIASES.get(action.strip().upper())
    if decision is None:
        raise ValueError(f"Unknown action {action!r}: use APPROVE, EDIT or REJECT")
    return decision


class AuditService:
    def __init__(
        self,
        database_path,
        checkpoint_path,
        load_filing: Callable[[str], FilingBundle],
        load_truth: Callable[[FilingBundle], dict[MetricName, GroundTruthValue]],
    ) -> None:
        self.store = Store(database_path)
        Path(checkpoint_path).parent.mkdir(parents=True, exist_ok=True)
        self._checkpoint_connection = sqlite3.connect(
            str(checkpoint_path), check_same_thread=False
        )
        self.graph = build_graph(
            Dependencies(load_filing=load_filing, load_truth=load_truth, store=self.store),
            SqliteSaver(self._checkpoint_connection),
        )
        self._graph_lock = threading.RLock()

    def close(self) -> None:
        self.store.close()
        self._checkpoint_connection.close()

    def get_config(self) -> Config:
        stored = self.store.get_setting(CONFIG_SETTING)
        return Config.model_validate_json(stored) if stored else Config()

    def set_config(self, config: Config) -> Config:
        self.store.set_setting(CONFIG_SETTING, config.model_dump_json())
        return config

    def start_run(self, ticker: str, fault_switch: bool = False) -> str:
        symbol = ticker.strip().upper()
        thread_id = f"{symbol}-{uuid4().hex[:8]}"
        self.store.create_run(thread_id, symbol, fault_switch)
        payload = {
            "thread_id": thread_id,
            "ticker": symbol,
            "config": self.get_config().model_dump(),
            "fault_switch": fault_switch,
        }
        self._invoke(thread_id, payload)
        return thread_id

    def _graph_config(self, thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    def _invoke(self, thread_id: str, payload: Any) -> None:
        with self._graph_lock:
            try:
                self.graph.invoke(payload, self._graph_config(thread_id))
            except Exception as error:
                self.store.update_run(thread_id, "FAILED", str(error))
                raise

    def _state(self, thread_id: str) -> dict[str, Any]:
        with self._graph_lock:
            return dict(self.graph.get_state(self._graph_config(thread_id)).values or {})

    def get_run(self, thread_id: str) -> dict[str, Any]:
        run = self.store.get_run(thread_id)
        if run is None:
            raise RunNotFoundError(thread_id)
        values = self._state(thread_id)
        bundle = values.get("bundle", {})
        return {
            "thread_id": thread_id,
            "ticker": run["ticker"],
            "status": run["status"],
            "error": run["error"],
            "fault_injected": bool(run["fault_switch"]),
            "created_at": run["created_at"],
            "updated_at": run["updated_at"],
            "company": bundle.get("company_name"),
            "period_end": bundle.get("fiscal_year_end"),
            "from_cache": bundle.get("from_cache"),
            "data_source": data_source_label(bundle),
            "metrics": values.get("metrics", []),
            "validations": values.get("validations", []),
            "decisions": values.get("decisions", []),
            "final_metrics": values.get("final_metrics", []),
            "report_before_review": values.get("report_before_review"),
            "report": values.get("report"),
            "pending_reviews": self.store.count_reviews(thread_id, ReviewStatus.PENDING),
            "completed_reviews": self.store.count_reviews(thread_id, ReviewStatus.DONE),
            "step_ledger": self.store.get_ledger(thread_id),
        }

    def list_runs(self) -> list[dict[str, Any]]:
        return self.store.list_runs()

    def list_reviews(
        self, status: Optional[ReviewStatus] = None, thread_id: Optional[str] = None
    ) -> list[ReviewItem]:
        return self.store.list_reviews(status, thread_id)

    def list_reviews_detailed(
        self, status: Optional[ReviewStatus] = None, thread_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        states: dict[str, dict[str, Any]] = {}
        detailed: list[dict[str, Any]] = []
        for review in self.store.list_reviews(status, thread_id):
            if review.thread_id not in states:
                states[review.thread_id] = self._state(review.thread_id)
            values = states[review.thread_id]
            name = review.metric_name.value
            metric = next(
                (item for item in values.get("metrics", []) if item["metric_name"] == name), None
            )
            decision = next(
                (item for item in values.get("decisions", []) if item["metric_name"] == name), None
            )
            bundle = values.get("bundle", {})
            payload = review.model_dump(mode="json")
            payload["metric"] = metric
            payload["reasons"] = decision["reasons"] if decision else []
            payload["company"] = bundle.get("company_name")
            payload["data_source"] = data_source_label(bundle)
            detailed.append(payload)
        return detailed

    def list_audit(self, thread_id: Optional[str] = None) -> list[AuditEntry]:
        return self.store.list_audit(thread_id)

    def get_report(self, thread_id: str) -> dict[str, Any]:
        run = self.get_run(thread_id)
        return {
            "thread_id": thread_id,
            "status": run["status"],
            "fault_injected": run["fault_injected"],
            "report_before_review": run["report_before_review"],
            "report": run["report"],
        }

    def decide(
        self,
        review_id: str,
        action: str,
        corrected_value: Optional[float],
        notes: str,
        reviewer_id: str,
    ) -> dict[str, Any]:
        decision = parse_decision(action)
        if not reviewer_id.strip():
            raise ValueError("A reviewer id is required")
        if decision == Decision.CORRECTED and corrected_value is None:
            raise ValueError("A corrected value is required when editing")
        review = self.store.get_review(review_id)
        if review is None:
            raise ReviewNotFoundError(review_id)
        values = self._state(review.thread_id)
        metric = next(
            item for item in values["metrics"] if item["metric_name"] == review.metric_name.value
        )
        original = float(metric["value"])
        if decision == Decision.CORRECTED:
            final_value: Optional[float] = float(corrected_value)
        elif decision == Decision.APPROVED:
            final_value = original
        else:
            final_value = None
        entry = AuditEntry(
            thread_id=review.thread_id,
            company=values["bundle"]["company_name"],
            metric_name=review.metric_name,
            original_value=original,
            final_value=final_value,
            decision=decision,
            reviewer_notes=notes,
            reviewer_id=reviewer_id.strip(),
            simulated=bool(metric.get("simulated_fault", False)),
            timestamp=datetime.now(timezone.utc),
        )
        saved = self.store.record_decision(review_id, entry)
        resumed = False
        if self.store.count_reviews(review.thread_id, ReviewStatus.PENDING) == 0:
            self.store.update_run(review.thread_id, "RUNNING")
            self._invoke(review.thread_id, Command(resume={"decided": True}))
            resumed = True
        run = self.store.get_run(review.thread_id)
        return {
            "audit_entry": saved.model_dump(mode="json"),
            "resumed": resumed,
            "run_status": run["status"],
        }
