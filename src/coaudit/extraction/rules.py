import re
from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional

from coaudit.contracts import (
    ExtractedMetric,
    Extractor,
    FilingBundle,
    MetricName,
    SourceLocation,
    TableKind,
)
from coaudit.parsing.tables import ParsedRow, ParsedTable, load_tables, select_statement_tables

LabelTest = Callable[[str], bool]


def normalize_label(label: str) -> str:
    lowered = label.lower().replace("’", "'").replace("‘", "'")
    without_notes = re.sub(r"\(\d+\)", "", lowered)
    collapsed = re.sub(r"\s+", " ", without_notes)
    return collapsed.strip(" :")


def equals(text: str) -> LabelTest:
    return lambda label: label == text


def starts(text: str) -> LabelTest:
    return lambda label: label.startswith(text)


def is_total_equity(label: str) -> bool:
    if "noncontrolling" in label or "non-controlling" in label or "liabilities" in label:
        return False
    if label.startswith("equity attributable to") or label.startswith(
        "total equity attributable to"
    ):
        return True
    if not label.startswith("total"):
        return False
    return (
        "shareholders' equity" in label
        or "stockholders' equity" in label
        or "shareowners' equity" in label
        or label == "total equity"
    )


def is_operating_cash_flow(label: str) -> bool:
    if "operating activities" not in label:
        return False
    return label.startswith("cash") or label.startswith("net cash")


def is_parent_net_income(label: str) -> bool:
    if "noncontrolling" in label or "non-controlling" in label:
        return False
    return (
        label.startswith("net income attributable")
        or label.startswith("consolidated net income attributable")
        or label.startswith("net earnings attributable")
    )


@dataclass(frozen=True)
class MetricRule:
    metric: MetricName
    table_kind: TableKind
    label_tests: tuple[LabelTest, ...]


RULES = (
    MetricRule(
        MetricName.REVENUE,
        TableKind.INCOME_STATEMENT,
        (
            equals("total net sales"),
            equals("total revenues"),
            equals("total revenue"),
            equals("revenues"),
            equals("revenue"),
            equals("net sales"),
            equals("net revenues"),
            equals("net operating revenues"),
            starts("net operating revenue"),
            starts("total revenue"),
            starts("total net sales"),
            starts("revenues"),
        ),
    ),
    MetricRule(
        MetricName.NET_INCOME,
        TableKind.INCOME_STATEMENT,
        (
            is_parent_net_income,
            equals("net income"),
            equals("net earnings"),
            equals("consolidated net income"),
            starts("net income"),
            starts("net earnings"),
        ),
    ),
    MetricRule(
        MetricName.TOTAL_ASSETS,
        TableKind.BALANCE_SHEET,
        (equals("total assets"),),
    ),
    MetricRule(
        MetricName.TOTAL_LIABILITIES,
        TableKind.BALANCE_SHEET,
        (equals("total liabilities"),),
    ),
    MetricRule(
        MetricName.SHAREHOLDERS_EQUITY,
        TableKind.BALANCE_SHEET,
        (is_total_equity,),
    ),
    MetricRule(
        MetricName.OPERATING_CASH_FLOW,
        TableKind.CASH_FLOW,
        (is_operating_cash_flow, starts("net cash from operations")),
    ),
    MetricRule(
        MetricName.GROSS_PROFIT,
        TableKind.INCOME_STATEMENT,
        (
            equals("gross margin"),
            equals("gross profit"),
            starts("gross margin"),
            starts("gross profit"),
            starts("total gross profit"),
        ),
    ),
    MetricRule(
        MetricName.OPERATING_INCOME,
        TableKind.INCOME_STATEMENT,
        (
            equals("operating income"),
            equals("income from operations"),
            equals("operating profit"),
            equals("operating income (loss)"),
            starts("operating income"),
            starts("income from operations"),
            starts("operating profit"),
        ),
    ),
)

TOTAL_EQUITY_RULE = MetricRule(
    MetricName.SHAREHOLDERS_EQUITY,
    TableKind.BALANCE_SHEET,
    (
        equals("total equity"),
        equals("total shareholders' equity"),
        equals("total stockholders' equity"),
    ),
)

REDEEMABLE_INTEREST_RULE = MetricRule(
    MetricName.SHAREHOLDERS_EQUITY,
    TableKind.BALANCE_SHEET,
    (starts("redeemable noncontrolling interest"),),
)

COST_OF_SALES_RULE = MetricRule(
    MetricName.GROSS_PROFIT,
    TableKind.INCOME_STATEMENT,
    (
        equals("total cost of revenue"),
        equals("total cost of revenues"),
        equals("total cost of sales"),
        equals("cost of revenue"),
        equals("cost of revenues"),
        equals("cost of sales"),
    ),
)


def find_row(table: ParsedTable, rule: MetricRule) -> Optional[ParsedRow]:
    for label_test in rule.label_tests:
        for row in table.rows:
            if row.values and label_test(normalize_label(row.label)):
                return row
    return None


def build_metric(
    bundle: FilingBundle,
    metric: MetricName,
    table: ParsedTable,
    row: ParsedRow,
    period_end: date,
) -> ExtractedMetric:
    scale = table.unit_scale
    values = row.values
    current_index = len(values) - 1 if table.columns_ascending else 0
    prior_index = current_index - 1 if table.columns_ascending else 1
    prior = values[prior_index] * scale if 0 <= prior_index < len(values) else None
    return ExtractedMetric(
        company=bundle.company_name,
        ticker=bundle.ticker,
        metric_name=metric,
        value=values[current_index] * scale,
        period_end=period_end,
        fiscal_label=f"FY{period_end.year}",
        unit_scale=scale,
        raw_text=row.raw_text,
        source_snippet=row.raw_text,
        source_location=SourceLocation(table_id=table.table_id, row_index=row.index),
        extractor=Extractor.PARSER,
        prior_value=prior,
    )


def subtract_prior(first: ExtractedMetric, second: ExtractedMetric) -> Optional[float]:
    if first.prior_value is None or second.prior_value is None:
        return None
    return first.prior_value - second.prior_value


def derive_difference(
    bundle: FilingBundle,
    metric: MetricName,
    minuend: ExtractedMetric,
    subtrahend: ExtractedMetric,
    derived_from: list[MetricName],
) -> ExtractedMetric:
    minuend_text = minuend.source_snippet.removeprefix("derived: ")
    snippet = f"derived: {minuend_text} ; {subtrahend.source_snippet}"
    return ExtractedMetric(
        company=bundle.company_name,
        ticker=bundle.ticker,
        metric_name=metric,
        value=minuend.value - subtrahend.value,
        period_end=minuend.period_end,
        fiscal_label=minuend.fiscal_label,
        unit_scale=minuend.unit_scale,
        raw_text=snippet,
        source_snippet=snippet,
        extractor=Extractor.DERIVED,
        derived_from=derived_from,
        prior_value=subtract_prior(minuend, subtrahend),
    )


def extract_metrics_by_rules(
    bundle: FilingBundle, tables: list[ParsedTable]
) -> list[ExtractedMetric]:
    period_end = bundle.fiscal_year_end
    if period_end is None:
        return []
    chosen = select_statement_tables(tables)
    metrics: list[ExtractedMetric] = []
    for rule in RULES:
        table = chosen.get(rule.table_kind)
        if table is None:
            continue
        row = find_row(table, rule)
        if row is None:
            continue
        metrics.append(build_metric(bundle, rule.metric, table, row, period_end))
    found = {metric.metric_name: metric for metric in metrics}
    balance_table = chosen.get(TableKind.BALANCE_SHEET)
    income_table = chosen.get(TableKind.INCOME_STATEMENT)
    if MetricName.TOTAL_LIABILITIES not in found and MetricName.TOTAL_ASSETS in found:
        equity = found.get(MetricName.SHAREHOLDERS_EQUITY)
        total_equity_row = (
            find_row(balance_table, TOTAL_EQUITY_RULE) if balance_table else None
        )
        if total_equity_row is not None:
            equity = build_metric(
                bundle,
                MetricName.SHAREHOLDERS_EQUITY,
                balance_table,
                total_equity_row,
                period_end,
            )
        if equity is not None:
            liabilities = derive_difference(
                bundle,
                MetricName.TOTAL_LIABILITIES,
                found[MetricName.TOTAL_ASSETS],
                equity,
                [MetricName.TOTAL_ASSETS, MetricName.SHAREHOLDERS_EQUITY],
            )
            redeemable_row = (
                find_row(balance_table, REDEEMABLE_INTEREST_RULE)
                if balance_table
                else None
            )
            if redeemable_row is not None:
                redeemable = build_metric(
                    bundle,
                    MetricName.SHAREHOLDERS_EQUITY,
                    balance_table,
                    redeemable_row,
                    period_end,
                )
                liabilities = derive_difference(
                    bundle,
                    MetricName.TOTAL_LIABILITIES,
                    liabilities,
                    redeemable,
                    [MetricName.TOTAL_ASSETS, MetricName.SHAREHOLDERS_EQUITY],
                )
            metrics.append(liabilities)
    if MetricName.GROSS_PROFIT not in found and MetricName.REVENUE in found and income_table:
        cost_row = find_row(income_table, COST_OF_SALES_RULE)
        if cost_row is not None:
            cost = build_metric(
                bundle, MetricName.GROSS_PROFIT, income_table, cost_row, period_end
            )
            metrics.append(
                derive_difference(
                    bundle,
                    MetricName.GROSS_PROFIT,
                    found[MetricName.REVENUE],
                    cost,
                    [MetricName.REVENUE],
                )
            )
    return metrics


def extract_filing_metrics(bundle: FilingBundle) -> list[ExtractedMetric]:
    return extract_metrics_by_rules(bundle, load_tables(bundle))
