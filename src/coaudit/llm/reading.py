from dataclasses import dataclass, field
from typing import Optional

from coaudit.contracts import (
    CheckName,
    CheckStatus,
    ExtractedMetric,
    Extractor,
    FilingBundle,
    MetricName,
    TableKind,
    ValidationResult,
)
from coaudit.extraction.rules import build_metric, normalize_label
from coaudit.llm.client import LlmClient, LlmError
from coaudit.parsing.tables import ParsedTable, select_statement_tables

MAX_ROWS = 90

METRIC_TABLES = {
    MetricName.REVENUE: TableKind.INCOME_STATEMENT,
    MetricName.NET_INCOME: TableKind.INCOME_STATEMENT,
    MetricName.GROSS_PROFIT: TableKind.INCOME_STATEMENT,
    MetricName.OPERATING_INCOME: TableKind.INCOME_STATEMENT,
    MetricName.TOTAL_ASSETS: TableKind.BALANCE_SHEET,
    MetricName.TOTAL_LIABILITIES: TableKind.BALANCE_SHEET,
    MetricName.SHAREHOLDERS_EQUITY: TableKind.BALANCE_SHEET,
    MetricName.OPERATING_CASH_FLOW: TableKind.CASH_FLOW,
}

METRIC_HELP = {
    MetricName.REVENUE: "total revenue or total net sales for the year (income statement)",
    MetricName.NET_INCOME: "net income attributable to the company (income statement)",
    MetricName.GROSS_PROFIT: "gross profit or gross margin (income statement); leave out if not reported",
    MetricName.OPERATING_INCOME: "operating income or income from operations (income statement)",
    MetricName.TOTAL_ASSETS: "total assets (balance sheet)",
    MetricName.TOTAL_LIABILITIES: "total liabilities (balance sheet); leave out if there is no single total line",
    MetricName.SHAREHOLDERS_EQUITY: "total equity attributable to the company's shareholders (balance sheet)",
    MetricName.OPERATING_CASH_FLOW: "net cash provided by operating activities (cash flow statement)",
}


@dataclass(frozen=True)
class LlmReading:
    metrics: list[ExtractedMetric] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)


def format_number(value: float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def table_text(table: ParsedTable) -> str:
    order = "oldest year first" if table.columns_ascending else "newest year first"
    lines = [f"TABLE {table.kind.value} (unit multiplier {table.unit_scale:,}; columns: {order})"]
    for row in table.rows[:MAX_ROWS]:
        numbers = " | ".join(format_number(value) for value in row.values)
        lines.append(f"{row.label} | {numbers}" if numbers else row.label)
    return "\n".join(lines)


def build_prompt(tables: dict[TableKind, ParsedTable]) -> str:
    metric_lines = "\n".join(
        f'- "{name.value}": {help_text}' for name, help_text in METRIC_HELP.items()
    )
    table_blocks = "\n\n".join(table_text(table) for table in tables.values())
    return (
        "You are reading the financial statements of a company's annual report.\n"
        "Below are three statement tables as text. Each line is a row label followed by its "
        "numbers, one per year column.\n\n"
        f"{table_blocks}\n\n"
        "For each metric below, find the matching row and report the number from the MOST "
        "RECENT year column, exactly as printed. Do not calculate, estimate or convert units.\n"
        f"Metrics:\n{metric_lines}\n\n"
        "Return only JSON in this exact shape:\n"
        '{"readings": [{"metric": "<metric key>", "row_label": "<the row label copied exactly>", '
        '"value": <number as printed>}]}\n'
        "Leave a metric out if it is not clearly present. Never invent a number."
    )


def current_value(table: ParsedTable, values: tuple[float, ...]) -> float:
    return values[-1] if table.columns_ascending else values[0]


def read_item(
    item: dict,
    usable: dict[TableKind, ParsedTable],
    bundle: FilingBundle,
) -> tuple[Optional[ExtractedMetric], Optional[tuple[str, str]]]:
    raw_name = str(item.get("metric", ""))
    try:
        name = MetricName(raw_name)
    except ValueError:
        return None, (raw_name, "unknown metric")
    table = usable.get(METRIC_TABLES[name])
    if table is None:
        return None, (raw_name, "the table for this metric was not found")
    label = normalize_label(str(item.get("row_label", "")))
    try:
        value = float(item.get("value"))
    except (TypeError, ValueError):
        return None, (raw_name, "the value is not a number")
    rows = [row for row in table.rows if row.values and normalize_label(row.label) == label]
    if not rows:
        return None, (raw_name, f"row '{item.get('row_label')}' is not in the table")
    for row in rows:
        if abs(current_value(table, row.values) - value) < 1e-6:
            metric = build_metric(bundle, name, table, row, bundle.fiscal_year_end)
            return metric.model_copy(update={"extractor": Extractor.LLM}), None
    return None, (raw_name, "the number is not the most recent year value of that row")


def read_with_llm(
    bundle: FilingBundle, tables: list[ParsedTable], client: LlmClient
) -> LlmReading:
    chosen = select_statement_tables(tables)
    kinds = (TableKind.BALANCE_SHEET, TableKind.INCOME_STATEMENT, TableKind.CASH_FLOW)
    usable = {kind: chosen[kind] for kind in kinds if kind in chosen}
    if not usable or bundle.fiscal_year_end is None:
        return LlmReading()
    payload = client.complete_json(build_prompt(usable))
    items = payload.get("readings")
    if not isinstance(items, list):
        raise LlmError("The model response has no readings list")
    metrics: list[ExtractedMetric] = []
    rejected: list[tuple[str, str]] = []
    seen: set[MetricName] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        metric, reason = read_item(item, usable, bundle)
        if reason is not None:
            rejected.append(reason)
        elif metric.metric_name not in seen:
            seen.add(metric.metric_name)
            metrics.append(metric)
    return LlmReading(metrics=metrics, rejected=rejected)


def second_reading(
    bundle: FilingBundle,
    tables: list[ParsedTable],
    metrics: list[ExtractedMetric],
    client: LlmClient,
) -> tuple[list[ExtractedMetric], list[ValidationResult]]:
    ticker = metrics[0].ticker if metrics else bundle.ticker
    period_end = metrics[0].period_end if metrics else bundle.fiscal_year_end

    def result(status: CheckStatus, reason: str, involved: list[MetricName]) -> ValidationResult:
        return ValidationResult(
            ticker=ticker,
            period_end=period_end,
            check_name=CheckName.SECOND_READING,
            status=status,
            reason=reason,
            metrics_involved=involved,
        )

    try:
        reading = read_with_llm(bundle, tables, client)
    except LlmError as error:
        return [], [result(CheckStatus.NOT_APPLICABLE, f"AI second reading unavailable: {error}", [])]
    by_rules = {metric.metric_name: metric for metric in metrics}
    by_llm = {metric.metric_name: metric for metric in reading.metrics}
    rejected = {name: reason for name, reason in reading.rejected}
    results: list[ValidationResult] = []
    for name, rule_metric in by_rules.items():
        llm_metric = by_llm.get(name)
        if llm_metric is None:
            reason = rejected.get(name.value, "the AI did not return a value for this figure")
            results.append(result(CheckStatus.NOT_APPLICABLE, f"AI second reading: {reason}", [name]))
            continue
        tolerance = 0.5 * rule_metric.unit_scale
        if abs(llm_metric.value - rule_metric.value) <= tolerance:
            reason = (
                f"The AI read the same value {rule_metric.value:,.0f} "
                f"from '{llm_metric.source_snippet.split(' | ')[0]}'"
            )
            results.append(result(CheckStatus.PASSED, reason, [name]))
        else:
            reason = (
                f"The AI read {llm_metric.value:,.0f} from "
                f"'{llm_metric.source_snippet.split(' | ')[0]}' but the rules read "
                f"{rule_metric.value:,.0f}"
            )
            results.append(result(CheckStatus.FAILED, reason, [name]))
    extras = [metric for name, metric in by_llm.items() if name not in by_rules]
    return extras, results
