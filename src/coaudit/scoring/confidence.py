from dataclasses import dataclass
from typing import Optional

from coaudit.contracts import (
    CheckStatus,
    Config,
    ExtractedMetric,
    Extractor,
    ValidationResult,
)

DIRECT_READ_WEIGHT = 0.35
UNIT_CLEAR_WEIGHT = 0.15
CHECKS_WEIGHT = 0.50
DIRECT_READ_SCORES = {
    Extractor.PARSER: 1.0,
    Extractor.LLM: 0.8,
    Extractor.DERIVED: 0.4,
}
UNCHECKED_SIGNAL = 0.6


@dataclass(frozen=True)
class RoutingDecision:
    metric_name: str
    flagged: bool
    reasons: tuple[str, ...]


def checks_signal(results: list[ValidationResult]) -> float:
    if any(result.status == CheckStatus.FAILED for result in results):
        return 0.0
    if any(result.status == CheckStatus.PASSED for result in results):
        return 1.0
    return UNCHECKED_SIGNAL


def score_metric(metric: ExtractedMetric, results: list[ValidationResult]) -> ExtractedMetric:
    involved = [result for result in results if metric.metric_name in result.metrics_involved]
    signals = {
        "direct_read": DIRECT_READ_SCORES[metric.extractor],
        "unit_clear": 1.0 if metric.unit_scale > 1 else 0.5,
        "checks": checks_signal(involved),
    }
    confidence = (
        DIRECT_READ_WEIGHT * signals["direct_read"]
        + UNIT_CLEAR_WEIGHT * signals["unit_clear"]
        + CHECKS_WEIGHT * signals["checks"]
    )
    return metric.model_copy(
        update={"confidence": round(confidence, 4), "confidence_signals": signals}
    )


def score_confidence(
    metrics: list[ExtractedMetric],
    results: list[ValidationResult],
    config: Optional[Config] = None,
) -> list[ExtractedMetric]:
    return [score_metric(metric, results) for metric in metrics]


def route(
    metrics: list[ExtractedMetric],
    results: list[ValidationResult],
    config: Optional[Config] = None,
) -> list[RoutingDecision]:
    active = config or Config()
    decisions: list[RoutingDecision] = []
    for metric in metrics:
        reasons = [
            f"{result.check_name.value}: {result.reason}"
            for result in results
            if result.status == CheckStatus.FAILED and metric.metric_name in result.metrics_involved
        ]
        confidence = metric.confidence
        if confidence is None or confidence < active.confidence_threshold:
            shown = "missing" if confidence is None else f"{confidence:.2f}"
            reasons.append(
                f"confidence {shown} is below the threshold {active.confidence_threshold:.2f}"
            )
        decisions.append(
            RoutingDecision(
                metric_name=metric.metric_name.value,
                flagged=bool(reasons),
                reasons=tuple(reasons),
            )
        )
    return decisions
