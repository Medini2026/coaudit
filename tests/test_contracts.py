from datetime import date, datetime

import pytest
from pydantic import ValidationError

from coaudit.contracts import (
    AccuracyReport,
    AuditEntry,
    CheckName,
    CheckStatus,
    Config,
    Decision,
    ExtractedMetric,
    Extractor,
    FilingBundle,
    MetricName,
    ReviewItem,
    ReviewStatus,
    ValidationResult,
)


def build_metric(**overrides):
    values = dict(
        company="Example Corp",
        ticker="EXM",
        metric_name=MetricName.TOTAL_ASSETS,
        value=500_000_000.0,
        period_end=date(2025, 9, 27),
        fiscal_label="FY2025",
        unit_scale=1_000_000,
        raw_text="Total assets 500",
        source_snippet="Total assets | 500 | 480",
        extractor=Extractor.PARSER,
        confidence=0.9,
    )
    values.update(overrides)
    return ExtractedMetric(**values)


def build_bundle(**overrides):
    values = dict(
        ticker="EXM",
        cik="0000320193",
        company_name="Example Corp",
        accession_number="0000320193-25-000079",
        url="https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/doc.htm",
        downloaded_at=datetime(2026, 11, 12, 16, 0, 0),
        document_hash="a" * 64,
        html_path="data/cache/exm.html",
    )
    values.update(overrides)
    return FilingBundle(**values)


def test_valid_metric_round_trips_through_json():
    metric = build_metric()
    restored = ExtractedMetric.model_validate_json(metric.model_dump_json())
    assert restored == metric


def test_confidence_outside_zero_to_one_is_rejected():
    with pytest.raises(ValidationError):
        build_metric(confidence=1.2)


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        build_metric(surprise="value")


def test_unit_scale_must_be_positive():
    with pytest.raises(ValidationError):
        build_metric(unit_scale=0)


def test_cik_must_have_ten_digits():
    with pytest.raises(ValidationError):
        build_bundle(cik="320193")


def test_accession_number_format_is_enforced():
    with pytest.raises(ValidationError):
        build_bundle(accession_number="not-an-accession")


def test_document_hash_must_be_sha256_hex():
    with pytest.raises(ValidationError):
        build_bundle(document_hash="xyz")


def test_bundle_defaults_to_not_from_cache():
    assert build_bundle().from_cache is False


def test_validation_result_uses_agreed_status_values():
    result = ValidationResult(
        ticker="EXM",
        period_end=date(2025, 9, 27),
        check_name=CheckName.SEGMENT_SUM,
        status=CheckStatus.NOT_APPLICABLE,
        reason="No segment table in this filing",
    )
    assert result.status.value == "NOT_APPLICABLE"


def test_config_defaults_and_bounds():
    config = Config()
    assert config.confidence_threshold == 0.85
    assert config.year_on_year_threshold == 0.25
    with pytest.raises(ValidationError):
        Config(confidence_threshold=2.0)


def test_audit_entry_allows_rejection_without_final_value():
    entry = AuditEntry(
        thread_id="thread-1",
        company="Example Corp",
        metric_name=MetricName.REVENUE,
        original_value=100.0,
        decision=Decision.REJECTED,
        reviewer_id="reviewer-1",
        timestamp=datetime(2026, 11, 12, 16, 30, 0),
    )
    assert entry.final_value is None
    assert entry.simulated is False


def test_review_item_status_values():
    item = ReviewItem(
        id="r1",
        thread_id="thread-1",
        ticker="EXM",
        metric_name=MetricName.NET_INCOME,
        reason="Year-on-year jump above threshold",
        status=ReviewStatus.PENDING,
        created_at=datetime(2026, 11, 12, 16, 31, 0),
    )
    assert item.status == ReviewStatus.PENDING


def test_accuracy_report_defaults_are_empty():
    report = AccuracyReport(ticker="EXM")
    assert report.overall_accuracy is None
    assert report.outcomes == []
