from enum import Enum
from typing import Optional

from coaudit.contracts import ExtractedMetric, MetricName

SCALE_FACTOR = 1000.0


class FaultKind(str, Enum):
    SCALE = "scale_x1000"
    SIGN = "sign_flip"
    WRONG_YEAR = "wrong_year"
    WRONG_ROW = "wrong_row"


WRONG_ROW_PARTNERS = {
    MetricName.REVENUE: MetricName.NET_INCOME,
    MetricName.NET_INCOME: MetricName.REVENUE,
    MetricName.TOTAL_ASSETS: MetricName.TOTAL_LIABILITIES,
    MetricName.TOTAL_LIABILITIES: MetricName.TOTAL_ASSETS,
    MetricName.OPERATING_CASH_FLOW: MetricName.OPERATING_INCOME,
    MetricName.OPERATING_INCOME: MetricName.OPERATING_CASH_FLOW,
    MetricName.GROSS_PROFIT: MetricName.SHAREHOLDERS_EQUITY,
    MetricName.SHAREHOLDERS_EQUITY: MetricName.GROSS_PROFIT,
}


def faulty_value(
    target: ExtractedMetric, kind: FaultKind, others: dict[MetricName, ExtractedMetric]
) -> Optional[float]:
    if kind == FaultKind.SCALE:
        return target.value * SCALE_FACTOR
    if kind == FaultKind.SIGN:
        return -target.value if target.value != 0 else None
    if kind == FaultKind.WRONG_YEAR:
        if target.prior_value is None or target.prior_value == target.value:
            return None
        return target.prior_value
    partner = others.get(WRONG_ROW_PARTNERS.get(target.metric_name))
    if partner is None or partner.value == target.value:
        return None
    return partner.value


def corrupt(
    metrics: list[ExtractedMetric], target_name: MetricName, kind: FaultKind
) -> Optional[list[ExtractedMetric]]:
    by_name = {metric.metric_name: metric for metric in metrics}
    target = by_name.get(target_name)
    if target is None:
        return None
    value = faulty_value(target, kind, by_name)
    if value is None:
        return None
    return [
        metric.model_copy(update={"value": value, "simulated_fault": True})
        if metric.metric_name == target_name
        else metric
        for metric in metrics
    ]
