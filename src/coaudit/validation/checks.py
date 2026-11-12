from dataclasses import dataclass
from datetime import date
from typing import Optional

from coaudit.contracts import (
    CheckName,
    CheckStatus,
    Config,
    ExtractedMetric,
    Extractor,
    MetricName,
    TableKind,
    ValidationResult,
)
from coaudit.extraction.rules import (
    COST_OF_SALES_RULE,
    MetricRule,
    equals,
    find_row,
    normalize_label,
    starts,
)
from coaudit.parsing.tables import ParsedRow, ParsedTable, select_statement_tables

YEAR_RANGE = range(1990, 2101)
RECONCILING_KEYWORDS = ("corporate", "elimination", "unallocated", "reconcil")


@dataclass(frozen=True)
class CheckContext:
    ticker: str
    period_end: date
    metrics: dict[MetricName, ExtractedMetric]
    tables: dict[TableKind, ParsedTable]
    config: Config


def row_current_value(table: ParsedTable, row: ParsedRow) -> Optional[float]:
    if not row.values:
        return None
    index = len(row.values) - 1 if table.columns_ascending else 0
    return row.values[index] * table.unit_scale


def is_header_row(row: ParsedRow) -> bool:
    if len(row.values) < 2:
        return False
    return all(float(value).is_integer() and int(value) in YEAR_RANGE for value in row.values)


def make_result(
    context: CheckContext,
    name: CheckName,
    status: CheckStatus,
    reason: str,
    metrics: list[MetricName],
) -> ValidationResult:
    return ValidationResult(
        ticker=context.ticker,
        period_end=context.period_end,
        check_name=name,
        status=status,
        reason=reason,
        metrics_involved=metrics,
    )


def format_dollars(value: float) -> str:
    return f"{value:,.0f}"


def find_balance_total_row(table: ParsedTable) -> Optional[ParsedRow]:
    for row in table.rows:
        label = normalize_label(row.label)
        if row.values and label.startswith("total liabilities") and "equity" in label:
            return row
    return None


def minority_interest_total(table: ParsedTable) -> float:
    total = 0.0
    for row in table.rows:
        label = normalize_label(row.label)
        if "noncontrolling" in label and "total" not in label:
            value = row_current_value(table, row)
            if value is not None:
                total += value
    return total


def check_accounting_equation(context: CheckContext) -> list[ValidationResult]:
    assets = context.metrics.get(MetricName.TOTAL_ASSETS)
    table = context.tables.get(TableKind.BALANCE_SHEET)
    name = CheckName.ACCOUNTING_EQUATION
    if assets is None or table is None:
        return [
            make_result(
                context,
                name,
                CheckStatus.NOT_APPLICABLE,
                "Total assets or the balance sheet was not found",
                [MetricName.TOTAL_ASSETS],
            )
        ]
    tolerance = context.config.equation_tolerance_units * assets.unit_scale
    results: list[ValidationResult] = []
    total_row = find_balance_total_row(table)
    if total_row is not None:
        bottom_line = row_current_value(table, total_row)
        difference = abs(assets.value - bottom_line)
        passed = difference <= tolerance
        reason = (
            f"Total assets {format_dollars(assets.value)} "
            f"{'equals' if passed else 'differs from'} total liabilities and equity "
            f"{format_dollars(bottom_line)}"
        )
        results.append(
            make_result(
                context,
                name,
                CheckStatus.PASSED if passed else CheckStatus.FAILED,
                reason,
                [MetricName.TOTAL_ASSETS],
            )
        )
    liabilities = context.metrics.get(MetricName.TOTAL_LIABILITIES)
    equity = context.metrics.get(MetricName.SHAREHOLDERS_EQUITY)
    if liabilities is not None and equity is not None and liabilities.extractor == Extractor.PARSER:
        residual = assets.value - liabilities.value - equity.value
        minority = minority_interest_total(table)
        passed = abs(residual) <= tolerance or abs(residual - minority) <= tolerance
        reason = (
            f"Assets {format_dollars(assets.value)} minus liabilities "
            f"{format_dollars(liabilities.value)} minus equity {format_dollars(equity.value)} "
            f"leaves {format_dollars(residual)}"
        )
        if passed and abs(residual) > tolerance:
            reason += f", explained by noncontrolling interests {format_dollars(minority)}"
        results.append(
            make_result(
                context,
                name,
                CheckStatus.PASSED if passed else CheckStatus.FAILED,
                reason,
                [
                    MetricName.TOTAL_ASSETS,
                    MetricName.TOTAL_LIABILITIES,
                    MetricName.SHAREHOLDERS_EQUITY,
                ],
            )
        )
    if not results:
        results.append(
            make_result(
                context,
                name,
                CheckStatus.NOT_APPLICABLE,
                "No balance sheet total or direct liabilities line to compare",
                [MetricName.TOTAL_ASSETS],
            )
        )
    return results


def find_segment_candidates(table: ParsedTable) -> list[tuple[list[ParsedRow], ParsedRow]]:
    candidates: list[tuple[list[ParsedRow], ParsedRow]] = []
    data_rows: list[ParsedRow] = []
    for row in table.rows:
        if not row.label or not row.values or is_header_row(row):
            continue
        if normalize_label(row.label).startswith("total"):
            if len(data_rows) >= 2:
                candidates.append((list(data_rows), row))
            data_rows = []
            continue
        data_rows.append(row)
    return candidates


def segment_tolerance(context: CheckContext, table: ParsedTable, count: int) -> float:
    units = max(context.config.equation_tolerance_units, 0.5 * count)
    return units * table.unit_scale


def column_segment_match(
    context: CheckContext, table: ParsedTable, revenue: ExtractedMetric
) -> Optional[tuple[int, float]]:
    for row in table.rows:
        if len(row.values) < 3 or is_header_row(row):
            continue
        scaled = [value * table.unit_scale for value in row.values]
        for total, segments in ((scaled[-1], scaled[:-1]), (scaled[0], scaled[1:])):
            if abs(total - revenue.value) > segment_tolerance(context, table, 1):
                continue
            if abs(sum(segments) - total) <= segment_tolerance(context, table, len(segments)):
                return len(segments), total
    return None


def find_reconciling_row(
    context: CheckContext, table: ParsedTable, gap: float
) -> Optional[ParsedRow]:
    header = ""
    for row in table.rows:
        if not row.values:
            header = normalize_label(row.label)
            continue
        if not any(keyword in header for keyword in RECONCILING_KEYWORDS):
            continue
        value = row_current_value(table, row)
        if abs(value - gap) <= segment_tolerance(context, table, 1):
            return row
    return None


def stacked_segment_match(
    context: CheckContext, table: ParsedTable, revenue: ExtractedMetric
) -> Optional[tuple[bool, str]]:
    rows_by_label: dict[str, list[ParsedRow]] = {}
    for row in table.rows:
        if not row.label or not row.values or is_header_row(row):
            continue
        rows_by_label.setdefault(normalize_label(row.label), []).append(row)
    for rows in rows_by_label.values():
        if len(rows) < 3:
            continue
        total = row_current_value(table, rows[-1])
        if abs(total - revenue.value) > segment_tolerance(context, table, 1):
            continue
        values = [row_current_value(table, row) for row in rows[:-1]]
        for count in range(2, len(values) + 1):
            if abs(sum(values[-count:]) - total) <= segment_tolerance(context, table, count):
                return True, (
                    f"{count} stacked segment revenue rows add up to {format_dollars(total)}, "
                    f"matching the reported total"
                )
        for count in range(len(values), 1, -1):
            block_sum = sum(values[-count:])
            reconciling = find_reconciling_row(context, table, total - block_sum)
            if reconciling is not None:
                return True, (
                    f"{count} stacked segment revenue rows add up to {format_dollars(block_sum)}; "
                    f"the remaining {format_dollars(total - block_sum)} is "
                    f"{reconciling.label} in the corporate block, giving the reported total "
                    f"{format_dollars(total)}"
                )
        return False, (
            f"{len(values)} stacked segment revenue rows add up to "
            f"{format_dollars(sum(values))}, but the reported total is {format_dollars(total)}"
        )
    return None


def check_segment_sum(context: CheckContext) -> ValidationResult:
    name = CheckName.SEGMENT_SUM
    table = context.tables.get(TableKind.SEGMENT_NOTE)
    involved = [MetricName.REVENUE]
    revenue = context.metrics.get(MetricName.REVENUE)
    if table is None:
        return make_result(
            context, name, CheckStatus.NOT_APPLICABLE, "No segment table was found", involved
        )
    if revenue is None:
        return make_result(
            context, name, CheckStatus.NOT_APPLICABLE, "Revenue was not found", involved
        )
    column_match = column_segment_match(context, table, revenue)
    if column_match is not None:
        count, total = column_match
        reason = (
            f"{count} segment columns add up to {format_dollars(total)}, "
            f"matching the reported total"
        )
        return make_result(context, name, CheckStatus.PASSED, reason, involved)
    stacked = stacked_segment_match(context, table, revenue)
    if stacked is not None:
        passed, reason = stacked
        status = CheckStatus.PASSED if passed else CheckStatus.FAILED
        return make_result(context, name, status, reason, involved)
    candidates = find_segment_candidates(table)
    matching = [
        (rows, total_row)
        for rows, total_row in candidates
        if abs(row_current_value(table, total_row) - revenue.value)
        <= segment_tolerance(context, table, 1)
    ]
    if not matching:
        return make_result(
            context,
            name,
            CheckStatus.NOT_APPLICABLE,
            "No total row in the segment table equals the reported revenue, "
            "so it is not a revenue-by-segment table",
            involved,
        )
    rows, total_row = matching[0]
    values = [row_current_value(table, row) for row in rows]
    total = row_current_value(table, total_row)
    for count in range(2, len(values) + 1):
        block_sum = sum(values[-count:])
        if abs(block_sum - total) <= segment_tolerance(context, table, count):
            reason = (
                f"{count} segments add up to {format_dollars(block_sum)}, "
                f"matching the reported total {format_dollars(total)}"
            )
            return make_result(context, name, CheckStatus.PASSED, reason, involved)
    reason = (
        f"{len(values)} segments add up to {format_dollars(sum(values))}, "
        f"but the reported total is {format_dollars(total)}"
    )
    return make_result(context, name, CheckStatus.FAILED, reason, involved)


def check_year_on_year(context: CheckContext) -> list[ValidationResult]:
    results: list[ValidationResult] = []
    threshold = context.config.year_on_year_threshold
    for metric in context.metrics.values():
        if metric.prior_value is None:
            continue
        involved = [metric.metric_name]
        if metric.prior_value == 0:
            results.append(
                make_result(
                    context,
                    CheckName.YEAR_ON_YEAR,
                    CheckStatus.NOT_APPLICABLE,
                    "Last year's value is zero, so a percentage change cannot be computed",
                    involved,
                )
            )
            continue
        change = (metric.value - metric.prior_value) / abs(metric.prior_value)
        passed = abs(change) <= threshold
        reason = (
            f"Changed {change:+.1%} from last year "
            f"({format_dollars(metric.prior_value)} to {format_dollars(metric.value)}); "
            f"limit is {threshold:.0%}"
        )
        results.append(
            make_result(
                context,
                CheckName.YEAR_ON_YEAR,
                CheckStatus.PASSED if passed else CheckStatus.FAILED,
                reason,
                involved,
            )
        )
    return results


NET_INCOME_ROW_RULE = MetricRule(
    MetricName.NET_INCOME,
    TableKind.CASH_FLOW,
    (
        equals("net income"),
        equals("net earnings"),
        equals("consolidated net income"),
        equals("net income (loss)"),
        starts("net income"),
        starts("net earnings"),
    ),
)

OPERATING_EXPENSE_RULE = MetricRule(
    MetricName.OPERATING_INCOME,
    TableKind.INCOME_STATEMENT,
    (
        equals("total operating expenses"),
        equals("total costs and expenses"),
        equals("total operating costs and expenses"),
        starts("operating, selling, general and administrative"),
    ),
)

TOTAL_COST_LABELS = ("total costs and expenses", "total operating costs and expenses")


def not_applicable(
    context: CheckContext, name: CheckName, reason: str, involved: list[MetricName]
) -> ValidationResult:
    return make_result(context, name, CheckStatus.NOT_APPLICABLE, reason, involved)


def identity_tolerance(context: CheckContext, table: ParsedTable, units: float) -> float:
    return max(context.config.equation_tolerance_units, units) * table.unit_scale


def check_net_income_tie_out(context: CheckContext) -> ValidationResult:
    name = CheckName.NET_INCOME_TIE_OUT
    involved = [MetricName.NET_INCOME]
    net_income = context.metrics.get(MetricName.NET_INCOME)
    cash_table = context.tables.get(TableKind.CASH_FLOW)
    income_table = context.tables.get(TableKind.INCOME_STATEMENT)
    if net_income is None or cash_table is None:
        return not_applicable(
            context, name, "Net income or the cash flow statement was not found", involved
        )
    row = find_row(cash_table, NET_INCOME_ROW_RULE)
    if row is None:
        return not_applicable(
            context, name, "The cash flow statement has no net income line", involved
        )
    cash_value = row_current_value(cash_table, row)
    gap = cash_value - net_income.value
    tolerance = identity_tolerance(context, cash_table, 1)
    if abs(gap) <= tolerance:
        reason = (
            f"Net income {format_dollars(net_income.value)} matches the cash flow "
            f"statement {format_dollars(cash_value)}"
        )
        return make_result(context, name, CheckStatus.PASSED, reason, involved)
    if income_table is not None:
        for candidate in income_table.rows:
            if "noncontrolling" not in normalize_label(candidate.label) or not candidate.values:
                continue
            minority = row_current_value(income_table, candidate)
            if abs(abs(minority) - abs(gap)) <= tolerance:
                reason = (
                    f"Net income {format_dollars(net_income.value)} differs from the cash flow "
                    f"statement {format_dollars(cash_value)} by the noncontrolling interest "
                    f"{format_dollars(abs(minority))}"
                )
                return make_result(context, name, CheckStatus.PASSED, reason, involved)
    reason = (
        f"Net income {format_dollars(net_income.value)} does not match the cash flow "
        f"statement {format_dollars(cash_value)}"
    )
    return make_result(context, name, CheckStatus.FAILED, reason, involved)


def check_gross_profit_identity(context: CheckContext) -> ValidationResult:
    name = CheckName.GROSS_PROFIT_IDENTITY
    involved = [MetricName.REVENUE, MetricName.GROSS_PROFIT]
    revenue = context.metrics.get(MetricName.REVENUE)
    gross = context.metrics.get(MetricName.GROSS_PROFIT)
    table = context.tables.get(TableKind.INCOME_STATEMENT)
    if revenue is None or gross is None or table is None:
        return not_applicable(
            context, name, "Revenue, gross profit or the income statement was not found", involved
        )
    if gross.extractor == Extractor.DERIVED:
        return not_applicable(
            context,
            name,
            "Gross profit was calculated from revenue and cost of sales, so it cannot be "
            "checked against them",
            involved,
        )
    cost_row = find_row(table, COST_OF_SALES_RULE)
    if cost_row is None:
        return not_applicable(context, name, "No cost of sales line was found", involved)
    cost = row_current_value(table, cost_row)
    expected = revenue.value - cost
    passed = abs(expected - gross.value) <= identity_tolerance(context, table, 2)
    reason = (
        f"Revenue {format_dollars(revenue.value)} minus cost of sales {format_dollars(cost)} is "
        f"{format_dollars(expected)}, "
        f"{'matching' if passed else 'but gross profit is'} {format_dollars(gross.value)}"
    )
    return make_result(
        context, name, CheckStatus.PASSED if passed else CheckStatus.FAILED, reason, involved
    )


def check_operating_income_identity(context: CheckContext) -> ValidationResult:
    name = CheckName.OPERATING_INCOME_IDENTITY
    involved = [MetricName.OPERATING_INCOME]
    operating = context.metrics.get(MetricName.OPERATING_INCOME)
    revenue = context.metrics.get(MetricName.REVENUE)
    gross = context.metrics.get(MetricName.GROSS_PROFIT)
    table = context.tables.get(TableKind.INCOME_STATEMENT)
    if operating is None or revenue is None or table is None:
        return not_applicable(
            context, name, "Operating income, revenue or the income statement was not found", involved
        )
    row = find_row(table, OPERATING_EXPENSE_RULE)
    if row is None:
        return not_applicable(
            context, name, "No total operating expenses line was found", involved
        )
    expense = row_current_value(table, row)
    label = normalize_label(row.label)
    if label in TOTAL_COST_LABELS:
        expected = revenue.value - expense
        basis = f"revenue {format_dollars(revenue.value)} minus total costs {format_dollars(expense)}"
    elif gross is not None:
        expected = gross.value - expense
        basis = (
            f"gross profit {format_dollars(gross.value)} minus operating expenses "
            f"{format_dollars(expense)}"
        )
    else:
        return not_applicable(context, name, "Gross profit was not found", involved)
    passed = abs(expected - operating.value) <= identity_tolerance(context, table, 2)
    reason = (
        f"{basis.capitalize()} is {format_dollars(expected)}, "
        f"{'matching' if passed else 'but operating income is'} {format_dollars(operating.value)}"
    )
    return make_result(
        context, name, CheckStatus.PASSED if passed else CheckStatus.FAILED, reason, involved
    )


def run_validations(
    metrics: list[ExtractedMetric],
    tables: list[ParsedTable],
    config: Optional[Config] = None,
) -> list[ValidationResult]:
    if not metrics:
        return []
    context = CheckContext(
        ticker=metrics[0].ticker,
        period_end=metrics[0].period_end,
        metrics={metric.metric_name: metric for metric in metrics},
        tables=select_statement_tables(tables),
        config=config or Config(),
    )
    results = check_accounting_equation(context)
    results.append(check_segment_sum(context))
    results.append(check_net_income_tie_out(context))
    results.append(check_gross_profit_identity(context))
    results.append(check_operating_income_identity(context))
    results.extend(check_year_on_year(context))
    return results
