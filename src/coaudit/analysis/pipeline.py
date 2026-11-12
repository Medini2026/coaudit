from dataclasses import dataclass
from typing import Optional

from coaudit.contracts import Config, ExtractedMetric, FilingBundle, ValidationResult
from coaudit.extraction.rules import extract_metrics_by_rules
from coaudit.llm.client import LlmClient
from coaudit.llm.reading import second_reading
from coaudit.parsing.tables import ParsedTable, load_tables
from coaudit.scoring.confidence import RoutingDecision, route, score_confidence
from coaudit.settings import load_config
from coaudit.validation.checks import run_validations


@dataclass(frozen=True)
class FilingAnalysis:
    bundle: FilingBundle
    metrics: list[ExtractedMetric]
    validations: list[ValidationResult]
    decisions: list[RoutingDecision]


def analyze_filing(
    bundle: FilingBundle,
    config: Optional[Config] = None,
    tables: Optional[list[ParsedTable]] = None,
    llm: Optional[LlmClient] = None,
) -> FilingAnalysis:
    active = config or load_config()
    parsed = tables if tables is not None else load_tables(bundle)
    extracted = extract_metrics_by_rules(bundle, parsed)
    extras: list[ExtractedMetric] = []
    second: list[ValidationResult] = []
    if llm is not None:
        extras, second = second_reading(bundle, parsed, extracted, llm)
    combined = extracted + extras
    validations = run_validations(combined, parsed, active) + second
    scored = score_confidence(combined, validations, active)
    decisions = route(scored, validations, active)
    return FilingAnalysis(
        bundle=bundle,
        metrics=scored,
        validations=validations,
        decisions=decisions,
    )
