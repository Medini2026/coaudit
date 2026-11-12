from datetime import date, datetime

import httpx

from coaudit.contracts import Extractor, ExtractedMetric, FilingBundle, MetricName
from coaudit.groundtruth.compare import compare_to_ground_truth, matches
from coaudit.groundtruth.xbrl import (
    COMPANY_FACTS_URL,
    find_fact,
    is_annual_entry,
    load_ground_truth,
)
from coaudit.sec.client import SecClient

PERIOD_END = date(2026, 9, 26)
ACCESSION = "0000320193-26-000026"

COMPANY_FACTS = {
    "facts": {
        "us-gaap": {
            "Assets": {
                "units": {
                    "USD": [
                        {"end": "2026-09-26", "val": 404548000000, "accn": ACCESSION, "form": "10-K", "filed": "2026-10-30"},
                        {"end": "2025-09-27", "val": 359241000000, "accn": "0000320193-25-000079", "form": "10-K", "filed": "2025-10-31"},
                        {"end": "2026-09-26", "val": 1, "accn": "0000320193-26-000020", "form": "10-Q", "filed": "2026-08-01"},
                    ]
                }
            },
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "units": {
                    "USD": [
                        {"start": "2025-09-28", "end": "2026-09-26", "val": 478834000000, "accn": ACCESSION, "form": "10-K", "filed": "2026-10-30"},
                        {"start": "2026-06-28", "end": "2026-09-26", "val": 100000000000, "accn": ACCESSION, "form": "10-K", "filed": "2026-10-30"},
                    ]
                }
            },
            "NetIncomeLoss": {
                "units": {
                    "USD": [
                        {"start": "2025-09-28", "end": "2026-09-26", "val": 132175000000, "accn": "0000320193-27-000001", "form": "10-K", "filed": "2027-10-30"},
                        {"start": "2025-09-28", "end": "2026-09-26", "val": 132100000000, "accn": ACCESSION, "form": "10-K", "filed": "2026-10-30"},
                    ]
                }
            },
        }
    }
}


def build_bundle(period_end=PERIOD_END):
    return FilingBundle(
        ticker="AAPL",
        cik="0000320193",
        company_name="Apple Inc.",
        accession_number=ACCESSION,
        url="https://www.sec.gov/Archives/edgar/data/320193/x/doc.htm",
        downloaded_at=datetime(2026, 11, 12, 16, 0, 0),
        document_hash="a" * 64,
        html_path="unused.html",
        fiscal_year_end=period_end,
    )


def build_metric(name, value, extractor=Extractor.PARSER, unit_scale=1_000_000):
    return ExtractedMetric(
        company="Apple Inc.",
        ticker="AAPL",
        metric_name=name,
        value=value,
        period_end=PERIOD_END,
        fiscal_label="FY2026",
        unit_scale=unit_scale,
        raw_text="row",
        source_snippet="row",
        extractor=extractor,
    )


def test_instant_facts_need_no_start_date():
    assert is_annual_entry({"end": "2026-09-26"}) is True


def test_only_annual_durations_are_accepted():
    assert is_annual_entry({"start": "2025-09-28", "end": "2026-09-26"}) is True
    assert is_annual_entry({"start": "2026-06-28", "end": "2026-09-26"}) is False


def test_balance_sheet_fact_ignores_quarterly_forms():
    fact = find_fact(COMPANY_FACTS, MetricName.TOTAL_ASSETS, PERIOD_END, ACCESSION)
    assert fact.value == 404548000000
    assert fact.form == "10-K"
    assert fact.tag == "Assets"


def test_revenue_uses_the_annual_duration_and_the_fallback_tag():
    fact = find_fact(COMPANY_FACTS, MetricName.REVENUE, PERIOD_END, ACCESSION)
    assert fact.value == 478834000000
    assert fact.tag == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_the_fact_from_the_same_filing_is_preferred_over_a_later_restatement():
    fact = find_fact(COMPANY_FACTS, MetricName.NET_INCOME, PERIOD_END, ACCESSION)
    assert fact.value == 132100000000
    assert fact.accession_number == ACCESSION


def test_missing_facts_return_none():
    assert find_fact(COMPANY_FACTS, MetricName.GROSS_PROFIT, PERIOD_END, ACCESSION) is None
    assert find_fact(COMPANY_FACTS, MetricName.TOTAL_ASSETS, date(2020, 1, 1), ACCESSION) is None


def test_load_ground_truth_reads_company_facts_and_caches_them(tmp_path):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, json=COMPANY_FACTS)

    client = SecClient(
        "Test Team test@example.com",
        tmp_path,
        transport=httpx.MockTransport(handler),
        min_interval_seconds=0.0,
    )
    bundle = build_bundle()
    truth = load_ground_truth(client, bundle)
    assert set(truth) == {MetricName.TOTAL_ASSETS, MetricName.REVENUE, MetricName.NET_INCOME}
    load_ground_truth(client, bundle)
    assert requests == [COMPANY_FACTS_URL.format(cik="0000320193")]


def test_load_ground_truth_without_a_period_end_returns_nothing(tmp_path):
    client = SecClient("Test Team test@example.com", tmp_path, min_interval_seconds=0.0)
    assert load_ground_truth(client, build_bundle(period_end=None)) == {}


def test_matching_allows_rounding_to_half_a_unit():
    truth = find_fact(COMPANY_FACTS, MetricName.TOTAL_ASSETS, PERIOD_END, ACCESSION)
    close = build_metric(MetricName.TOTAL_ASSETS, 404548000000 + 400_000)
    far = build_metric(MetricName.TOTAL_ASSETS, 404548000000 + 600_000)
    assert matches(close, truth) is True
    assert matches(far, truth) is False


def test_report_counts_matches_differences_and_missing_values():
    truth = {
        MetricName.TOTAL_ASSETS: find_fact(COMPANY_FACTS, MetricName.TOTAL_ASSETS, PERIOD_END, ACCESSION),
        MetricName.REVENUE: find_fact(COMPANY_FACTS, MetricName.REVENUE, PERIOD_END, ACCESSION),
    }
    metrics = [
        build_metric(MetricName.TOTAL_ASSETS, 404548000000),
        build_metric(MetricName.REVENUE, 478834000000 * 10),
        build_metric(MetricName.GROSS_PROFIT, 5.0, extractor=Extractor.DERIVED),
    ]
    report = compare_to_ground_truth(build_bundle(), metrics, truth)
    outcomes = {outcome.metric_name: outcome for outcome in report.outcomes}
    assert outcomes[MetricName.TOTAL_ASSETS].is_correct is True
    assert outcomes[MetricName.REVENUE].is_correct is False
    assert outcomes[MetricName.GROSS_PROFIT].is_correct is None
    assert report.overall_accuracy == 0.5
    assert len(report.per_metric) == 2


def test_escalation_precision_counts_flagged_real_errors():
    truth = {
        MetricName.TOTAL_ASSETS: find_fact(COMPANY_FACTS, MetricName.TOTAL_ASSETS, PERIOD_END, ACCESSION),
        MetricName.REVENUE: find_fact(COMPANY_FACTS, MetricName.REVENUE, PERIOD_END, ACCESSION),
    }
    metrics = [
        build_metric(MetricName.TOTAL_ASSETS, 404548000000),
        build_metric(MetricName.REVENUE, 478834000000 * 10),
    ]
    report = compare_to_ground_truth(
        build_bundle(),
        metrics,
        truth,
        flagged=[MetricName.TOTAL_ASSETS, MetricName.REVENUE],
    )
    assert report.escalated_count == 2
    assert report.auto_approved_count == 0
    assert report.escalation_precision == 0.5


def test_escalation_precision_is_none_when_nothing_is_flagged():
    report = compare_to_ground_truth(build_bundle(), [], {})
    assert report.escalation_precision is None
    assert report.overall_accuracy is None
