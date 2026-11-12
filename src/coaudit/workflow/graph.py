from dataclasses import asdict, dataclass
from typing import Any, Callable, Optional

from typing_extensions import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from coaudit.contracts import (
    Config,
    Decision,
    ExtractedMetric,
    FilingBundle,
    MetricName,
    ValidationResult,
)
from coaudit.extraction.fault import apply_fault
from coaudit.extraction.rules import extract_metrics_by_rules
from coaudit.groundtruth.compare import compare_to_ground_truth
from coaudit.groundtruth.xbrl import GroundTruthValue
from coaudit.parsing.tables import load_tables
from coaudit.scoring.confidence import route, score_confidence
from coaudit.store.database import Store
from coaudit.validation.checks import run_validations

FILING_STEPS = ("fetch", "extract", "analyze", "prepare_review", "finalize", "verify")


class AuditState(TypedDict, total=False):
    thread_id: str
    ticker: str
    config: dict[str, Any]
    fault_switch: bool
    bundle: dict[str, Any]
    metrics: list[dict[str, Any]]
    validations: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    pending: list[str]
    final_metrics: list[dict[str, Any]]
    report_before_review: dict[str, Any]
    report: dict[str, Any]


@dataclass(frozen=True)
class Dependencies:
    load_filing: Callable[[str], FilingBundle]
    load_truth: Callable[[FilingBundle], dict[MetricName, GroundTruthValue]]
    store: Store


def dump_all(models: list[Any]) -> list[dict[str, Any]]:
    return [model.model_dump(mode="json") for model in models]


def apply_decisions(
    metrics: list[ExtractedMetric], latest: dict[str, Any]
) -> list[ExtractedMetric]:
    final: list[ExtractedMetric] = []
    for metric in metrics:
        entry = latest.get(metric.metric_name.value)
        if entry is None or entry.decision == Decision.APPROVED:
            final.append(metric)
        elif entry.decision == Decision.CORRECTED:
            final.append(metric.model_copy(update={"value": entry.final_value}))
    return final


def build_graph(dependencies: Dependencies, checkpointer):
    store = dependencies.store

    def fetch(state: AuditState) -> AuditState:
        store.record_step(state["thread_id"], "fetch")
        bundle = dependencies.load_filing(state["ticker"])
        return {"bundle": bundle.model_dump(mode="json")}

    def extract(state: AuditState) -> AuditState:
        store.record_step(state["thread_id"], "extract")
        bundle = FilingBundle.model_validate(state["bundle"])
        metrics = extract_metrics_by_rules(bundle, load_tables(bundle))
        if state.get("fault_switch"):
            metrics = apply_fault(metrics)
        return {"metrics": dump_all(metrics)}

    def analyze(state: AuditState) -> AuditState:
        store.record_step(state["thread_id"], "analyze")
        bundle = FilingBundle.model_validate(state["bundle"])
        config = Config.model_validate(state["config"])
        metrics = [ExtractedMetric.model_validate(item) for item in state["metrics"]]
        validations = run_validations(metrics, load_tables(bundle), config)
        scored = score_confidence(metrics, validations, config)
        decisions = route(scored, validations, config)
        return {
            "metrics": dump_all(scored),
            "validations": dump_all(validations),
            "decisions": [
                {**asdict(decision), "reasons": list(decision.reasons)}
                for decision in decisions
            ],
        }

    def prepare_review(state: AuditState) -> AuditState:
        thread_id = state["thread_id"]
        store.record_step(thread_id, "prepare_review")
        flagged = [item for item in state["decisions"] if item["flagged"]]
        store.create_review_items(
            thread_id,
            state["ticker"],
            [(item["metric_name"], "; ".join(item["reasons"])) for item in flagged],
        )
        store.update_run(thread_id, "PAUSED" if flagged else "RUNNING")
        return {"pending": [item["metric_name"] for item in flagged]}

    def human_review(state: AuditState) -> AuditState:
        interrupt({"thread_id": state["thread_id"], "pending": state["pending"]})
        return {}

    def after_prepare(state: AuditState) -> str:
        return "human_review" if state["pending"] else "finalize"

    def finalize(state: AuditState) -> AuditState:
        thread_id = state["thread_id"]
        store.record_step(thread_id, "finalize")
        latest = {entry.metric_name.value: entry for entry in store.list_audit(thread_id)}
        metrics = [ExtractedMetric.model_validate(item) for item in state["metrics"]]
        return {"final_metrics": dump_all(apply_decisions(metrics, latest))}

    def verify(state: AuditState) -> AuditState:
        thread_id = state["thread_id"]
        store.record_step(thread_id, "verify")
        bundle = FilingBundle.model_validate(state["bundle"])
        truth = dependencies.load_truth(bundle)
        flagged = [MetricName(name) for name in state["pending"]]
        before = [ExtractedMetric.model_validate(item) for item in state["metrics"]]
        final = [ExtractedMetric.model_validate(item) for item in state["final_metrics"]]
        report_before = compare_to_ground_truth(bundle, before, truth, flagged)
        report_after = compare_to_ground_truth(bundle, final, truth, flagged)
        store.update_run(thread_id, "COMPLETED")
        return {
            "report_before_review": report_before.model_dump(mode="json"),
            "report": report_after.model_dump(mode="json"),
        }

    graph = StateGraph(AuditState)
    graph.add_node("fetch", fetch)
    graph.add_node("extract", extract)
    graph.add_node("analyze", analyze)
    graph.add_node("prepare_review", prepare_review)
    graph.add_node("human_review", human_review)
    graph.add_node("finalize", finalize)
    graph.add_node("verify", verify)
    graph.add_edge(START, "fetch")
    graph.add_edge("fetch", "extract")
    graph.add_edge("extract", "analyze")
    graph.add_edge("analyze", "prepare_review")
    graph.add_conditional_edges(
        "prepare_review", after_prepare, {"human_review": "human_review", "finalize": "finalize"}
    )
    graph.add_edge("human_review", "finalize")
    graph.add_edge("finalize", "verify")
    graph.add_edge("verify", END)
    return graph.compile(checkpointer=checkpointer)
