from datetime import date, datetime

from coaudit.contracts import Config, Extractor, FilingBundle, MetricName
from coaudit.evaluation.faults import SCALE_FACTOR, FaultKind, corrupt
from coaudit.evaluation.harness import (
    FilingInput,
    calibration_table,
    detection_by_kind,
    evaluate_all,
    sweep_year_on_year,
)
from coaudit.extraction.rules import extract_metrics_by_rules
from coaudit.parsing.tables import extract_tables
from test_validation import BALANCED_HTML

PERIOD_END = date(2026, 9, 26)


def build_bundle():
    return FilingBundle(
        ticker="EXM",
        cik="0000320193",
        company_name="Example Corp",
        accession_number="0000320193-26-000026",
        url="https://www.sec.gov/Archives/edgar/data/320193/x/doc.htm",
        downloaded_at=datetime(2026, 11, 12, 16, 0, 0),
        document_hash="a" * 64,
        html_path="unused.html",
        fiscal_year_end=PERIOD_END,
    )


def build_inputs():
    tables = extract_tables(BALANCED_HTML)
    return FilingInput(bundle=build_bundle(), tables=tables), extract_metrics_by_rules(
        build_bundle(), tables
    )


def value_of(metrics, name):
    return next(metric for metric in metrics if metric.metric_name == name)


def test_the_scale_fault_multiplies_one_value_and_marks_it_simulated():
    _, metrics = build_inputs()
    corrupted = corrupt(metrics, MetricName.REVENUE, FaultKind.SCALE)
    revenue = value_of(corrupted, MetricName.REVENUE)
    original = value_of(metrics, MetricName.REVENUE)
    assert revenue.value == original.value * SCALE_FACTOR
    assert revenue.simulated_fault is True
    assert value_of(corrupted, MetricName.NET_INCOME) == value_of(metrics, MetricName.NET_INCOME)


def test_the_sign_fault_flips_the_value():
    _, metrics = build_inputs()
    corrupted = corrupt(metrics, MetricName.NET_INCOME, FaultKind.SIGN)
    assert value_of(corrupted, MetricName.NET_INCOME).value == -200_000_000


def test_the_wrong_year_fault_uses_last_years_value():
    _, metrics = build_inputs()
    corrupted = corrupt(metrics, MetricName.REVENUE, FaultKind.WRONG_YEAR)
    assert value_of(corrupted, MetricName.REVENUE).value == 900_000_000


def test_the_wrong_row_fault_takes_the_partner_metric_value():
    _, metrics = build_inputs()
    corrupted = corrupt(metrics, MetricName.REVENUE, FaultKind.WRONG_ROW)
    assert value_of(corrupted, MetricName.REVENUE).value == 200_000_000


def test_faults_that_do_not_apply_return_nothing():
    _, metrics = build_inputs()
    assert corrupt(metrics, MetricName.GROSS_PROFIT, FaultKind.SCALE) is not None
    assert corrupt([], MetricName.REVENUE, FaultKind.SCALE) is None
    without_prior = [metric.model_copy(update={"prior_value": None}) for metric in metrics]
    assert corrupt(without_prior, MetricName.REVENUE, FaultKind.WRONG_YEAR) is None


def test_the_original_metrics_are_not_modified():
    _, metrics = build_inputs()
    corrupt(metrics, MetricName.REVENUE, FaultKind.SCALE)
    assert value_of(metrics, MetricName.REVENUE).simulated_fault is False


def test_scale_and_sign_errors_are_always_caught_by_the_year_on_year_check():
    filing, _ = build_inputs()
    totals = detection_by_kind(evaluate_all([filing], Config()))
    caught, count = totals[FaultKind.SCALE]
    assert count == 8 and caught == 8
    caught, count = totals[FaultKind.SIGN]
    assert count == 8 and caught == 8


def test_a_wrong_year_value_is_caught_for_assets_but_missed_for_revenue():
    filing, _ = build_inputs()
    outcome = evaluate_all([filing], Config())[0]
    trials = {
        (trial.metric, trial.kind): trial.flagged
        for trial in outcome.trials
        if trial.kind == FaultKind.WRONG_YEAR
    }
    assert trials[(MetricName.TOTAL_ASSETS, FaultKind.WRONG_YEAR)] is True
    assert trials[(MetricName.REVENUE, FaultKind.WRONG_YEAR)] is False


def test_derived_and_already_flagged_values_are_not_tested():
    filing, _ = build_inputs()
    strict = evaluate_all([filing], Config(year_on_year_threshold=0.05))[0]
    assert all(trial.metric != MetricName.REVENUE for trial in strict.trials)
    derived_html = BALANCED_HTML.replace(
        "<tr><td>Total liabilities</td><td>600</td><td>580</td></tr>", ""
    )
    tables = extract_tables(derived_html)
    derived_filing = FilingInput(bundle=build_bundle(), tables=tables)
    outcome = evaluate_all([derived_filing], Config())[0]
    assert all(trial.metric != MetricName.TOTAL_LIABILITIES for trial in outcome.trials)
    metrics = extract_metrics_by_rules(build_bundle(), tables)
    assert value_of(metrics, MetricName.TOTAL_LIABILITIES).extractor == Extractor.DERIVED


def test_calibration_puts_injected_errors_in_the_low_bands():
    filing, _ = build_inputs()
    rows = calibration_table(evaluate_all([filing], Config()))
    assert [row[:2] for row in rows] == [(0.0, 0.6), (0.6, 0.85), (0.85, 1.0)]
    low_count, low_correct = rows[0][2], rows[0][3]
    high_count, high_correct = rows[2][2], rows[2][3]
    assert low_count > 0 and low_correct == 0
    assert high_count > 0 and 0 < high_correct < high_count


def test_a_looser_year_on_year_limit_catches_no_more_errors_than_a_stricter_one():
    filing, _ = build_inputs()
    points = sweep_year_on_year([filing], Config(), [0.10, 0.25, 1.0, 100.0])
    rates = [point.caught_rate for point in points if point.caught_rate is not None]
    assert rates == sorted(rates, reverse=True)
    assert points[0].clean_flag_rate >= points[-1].clean_flag_rate


def test_the_sweep_reports_the_number_of_trials():
    filing, _ = build_inputs()
    point = sweep_year_on_year([filing], Config(), [0.25])[0]
    assert point.trials > 0
