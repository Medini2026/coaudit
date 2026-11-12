from coaudit.contracts import ExtractedMetric, MetricName

DEFAULT_FAULT_METRIC = MetricName.OPERATING_CASH_FLOW
DEFAULT_FAULT_FACTOR = 1000.0


def apply_fault(
    metrics: list[ExtractedMetric],
    metric_name: MetricName = DEFAULT_FAULT_METRIC,
    factor: float = DEFAULT_FAULT_FACTOR,
) -> list[ExtractedMetric]:
    corrupted = []
    for metric in metrics:
        if metric.metric_name == metric_name:
            corrupted.append(
                metric.model_copy(
                    update={"value": metric.value * factor, "simulated_fault": True}
                )
            )
        else:
            corrupted.append(metric)
    return corrupted
