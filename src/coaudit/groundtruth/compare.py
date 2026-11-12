from typing import Iterable, Optional

from coaudit.contracts import (
    AccuracyReport,
    ExtractedMetric,
    FilingBundle,
    MetricAccuracy,
    MetricName,
    ValueOutcome,
)
from coaudit.groundtruth.xbrl import GroundTruthValue

ROUNDING_FRACTION = 0.5


def matches(metric: ExtractedMetric, reference: GroundTruthValue) -> bool:
    allowed_difference = ROUNDING_FRACTION * metric.unit_scale
    return abs(metric.value - reference.value) <= allowed_difference


def build_outcome(
    metric: ExtractedMetric,
    reference: Optional[GroundTruthValue],
    flagged: bool,
) -> ValueOutcome:
    if reference is None:
        return ValueOutcome(
            metric_name=metric.metric_name,
            period_end=metric.period_end,
            extracted_value=metric.value,
            was_flagged=flagged,
            simulated=metric.simulated_fault,
        )
    correct = matches(metric, reference)
    return ValueOutcome(
        metric_name=metric.metric_name,
        period_end=metric.period_end,
        extracted_value=metric.value,
        truth_value=reference.value,
        is_correct=correct,
        was_flagged=flagged,
        is_real_error=not correct,
        simulated=metric.simulated_fault,
    )


def compare_to_ground_truth(
    bundle: FilingBundle,
    metrics: list[ExtractedMetric],
    truth: dict[MetricName, GroundTruthValue],
    flagged: Iterable[MetricName] = (),
) -> AccuracyReport:
    flagged_names = set(flagged)
    outcomes = [
        build_outcome(metric, truth.get(metric.metric_name), metric.metric_name in flagged_names)
        for metric in metrics
    ]
    comparable = [outcome for outcome in outcomes if outcome.is_correct is not None]
    correct_count = sum(1 for outcome in comparable if outcome.is_correct)
    per_metric = [
        MetricAccuracy(
            metric_name=outcome.metric_name,
            correct=1 if outcome.is_correct else 0,
            total=1,
        )
        for outcome in comparable
    ]
    flagged_outcomes = [outcome for outcome in outcomes if outcome.was_flagged]
    flagged_comparable = [o for o in flagged_outcomes if o.is_correct is not None]
    precision = None
    if flagged_comparable:
        real_errors = sum(1 for outcome in flagged_comparable if outcome.is_real_error)
        precision = real_errors / len(flagged_comparable)
    return AccuracyReport(
        ticker=bundle.ticker,
        overall_accuracy=correct_count / len(comparable) if comparable else None,
        per_metric=per_metric,
        auto_approved_count=len(outcomes) - len(flagged_outcomes),
        escalated_count=len(flagged_outcomes),
        escalation_precision=precision,
        outcomes=outcomes,
    )
