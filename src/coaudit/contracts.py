from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetricName(str, Enum):
    REVENUE = "revenue"
    NET_INCOME = "net_income"
    TOTAL_ASSETS = "total_assets"
    TOTAL_LIABILITIES = "total_liabilities"
    SHAREHOLDERS_EQUITY = "shareholders_equity"
    OPERATING_CASH_FLOW = "operating_cash_flow"
    GROSS_PROFIT = "gross_profit"
    OPERATING_INCOME = "operating_income"


class Extractor(str, Enum):
    PARSER = "parser"
    LLM = "llm"
    DERIVED = "derived"


class CheckName(str, Enum):
    ACCOUNTING_EQUATION = "accounting_equation"
    SEGMENT_SUM = "segment_sum"
    YEAR_ON_YEAR = "year_on_year"
    NET_INCOME_TIE_OUT = "net_income_tie_out"
    GROSS_PROFIT_IDENTITY = "gross_profit_identity"
    OPERATING_INCOME_IDENTITY = "operating_income_identity"


class CheckStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class TableKind(str, Enum):
    INCOME_STATEMENT = "income_statement"
    BALANCE_SHEET = "balance_sheet"
    CASH_FLOW = "cash_flow"
    SEGMENT_NOTE = "segment_note"
    OTHER = "other"


class ReviewStatus(str, Enum):
    PENDING = "PENDING"
    DONE = "DONE"


class Decision(str, Enum):
    APPROVED = "APPROVED"
    CORRECTED = "CORRECTED"
    REJECTED = "REJECTED"


class TableRef(ContractModel):
    table_id: str
    kind: TableKind
    title: str
    unit_scale: int = Field(gt=0)
    row_count: int = Field(ge=0)


class FilingBundle(ContractModel):
    ticker: str
    cik: str = Field(pattern=r"^\d{10}$")
    company_name: str
    accession_number: str = Field(pattern=r"^\d{10}-\d{2}-\d{6}$")
    url: str
    downloaded_at: datetime
    document_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    html_path: str
    fiscal_year_end: Optional[date] = None
    tables: list[TableRef] = Field(default_factory=list)
    from_cache: bool = False


class SourceLocation(ContractModel):
    table_id: str
    row_index: int = Field(ge=0)
    column_index: Optional[int] = Field(default=None, ge=0)


class ExtractedMetric(ContractModel):
    company: str
    ticker: str
    metric_name: MetricName
    value: float
    period_end: date
    fiscal_label: str
    unit_scale: int = Field(gt=0)
    raw_text: str
    source_snippet: str
    source_location: Optional[SourceLocation] = None
    extractor: Extractor
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    confidence_signals: dict[str, float] = Field(default_factory=dict)
    derived_from: list[MetricName] = Field(default_factory=list)
    prior_value: Optional[float] = None
    simulated_fault: bool = False


class ValidationResult(ContractModel):
    ticker: str
    period_end: date
    check_name: CheckName
    status: CheckStatus
    reason: str
    metrics_involved: list[MetricName] = Field(default_factory=list)


class Config(ContractModel):
    confidence_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    year_on_year_threshold: float = Field(default=0.25, gt=0.0)
    equation_tolerance_units: float = Field(default=1.0, ge=0.0)
    spot_check_rate: float = Field(default=0.05, ge=0.0, le=1.0)


class ReviewItem(ContractModel):
    id: str
    thread_id: str
    ticker: str
    metric_name: MetricName
    reason: str
    status: ReviewStatus
    created_at: datetime


class AuditEntry(ContractModel):
    audit_id: Optional[int] = None
    thread_id: str
    company: str
    metric_name: MetricName
    original_value: float
    final_value: Optional[float] = None
    decision: Decision
    reviewer_notes: str = ""
    reviewer_id: str
    simulated: bool = False
    timestamp: datetime


class ValueOutcome(ContractModel):
    metric_name: MetricName
    period_end: date
    extracted_value: float
    truth_value: Optional[float] = None
    is_correct: Optional[bool] = None
    was_flagged: bool = False
    is_real_error: Optional[bool] = None
    simulated: bool = False


class MetricAccuracy(ContractModel):
    metric_name: MetricName
    correct: int = Field(ge=0)
    total: int = Field(ge=0)


class AccuracyReport(ContractModel):
    ticker: str
    overall_accuracy: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    per_metric: list[MetricAccuracy] = Field(default_factory=list)
    auto_approved_count: int = Field(default=0, ge=0)
    escalated_count: int = Field(default=0, ge=0)
    escalation_precision: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    outcomes: list[ValueOutcome] = Field(default_factory=list)
