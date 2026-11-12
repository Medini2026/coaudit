from dataclasses import dataclass
from typing import Optional

from coaudit.analysis.pipeline import analyze_filing
from coaudit.contracts import Config, Extractor, FilingBundle, MetricName
from coaudit.evaluation.faults import FaultKind, corrupt
from coaudit.extraction.rules import extract_metrics_by_rules
from coaudit.parsing.tables import ParsedTable
from coaudit.scoring.confidence import route, score_confidence
from coaudit.validation.checks import run_validations

CONFIDENCE_BANDS = ((0.0, 0.6), (0.6, 0.85), (0.85, 1.0001))


@dataclass(frozen=True)
class FilingInput:
    bundle: FilingBundle
    tables: list[ParsedTable]


@dataclass(frozen=True)
class Trial:
    ticker: str
    metric: MetricName
    kind: FaultKind
    flagged: bool
    confidence: float


@dataclass(frozen=True)
class FilingOutcome:
    trials: list[Trial]
    clean_points: list[tuple[float, bool]]
    clean_total: int
    clean_flagged: int


@dataclass(frozen=True)
class SweepPoint:
    threshold: float
    clean_flag_rate: float
    caught_rate: Optional[float]
    trials: int


def evaluate_filing(filing: FilingInput, config: Config) -> FilingOutcome:
    analysis = analyze_filing(filing.bundle, config, filing.tables)
    flagged_clean = {item.metric_name for item in analysis.decisions if item.flagged}
    clean_points = [(metric.confidence, False) for metric in analysis.metrics]
    originals = extract_metrics_by_rules(filing.bundle, filing.tables)
    trials: list[Trial] = []
    for metric in originals:
        if metric.metric_name.value in flagged_clean or metric.extractor != Extractor.PARSER:
            continue
        for kind in FaultKind:
            corrupted = corrupt(originals, metric.metric_name, kind)
            if corrupted is None:
                continue
            validations = run_validations(corrupted, filing.tables, config)
            scored = score_confidence(corrupted, validations, config)
            decisions = route(scored, validations, config)
            decision = next(d for d in decisions if d.metric_name == metric.metric_name.value)
            confidence = next(m for m in scored if m.metric_name == metric.metric_name).confidence
            trials.append(
                Trial(
                    ticker=filing.bundle.ticker,
                    metric=metric.metric_name,
                    kind=kind,
                    flagged=decision.flagged,
                    confidence=confidence,
                )
            )
    return FilingOutcome(
        trials=trials,
        clean_points=clean_points,
        clean_total=len(analysis.metrics),
        clean_flagged=len(flagged_clean),
    )


def evaluate_all(filings: list[FilingInput], config: Config) -> list[FilingOutcome]:
    return [evaluate_filing(filing, config) for filing in filings]


def detection_by_kind(outcomes: list[FilingOutcome]) -> dict[FaultKind, tuple[int, int]]:
    totals: dict[FaultKind, tuple[int, int]] = {kind: (0, 0) for kind in FaultKind}
    for outcome in outcomes:
        for trial in outcome.trials:
            caught, count = totals[trial.kind]
            totals[trial.kind] = (caught + int(trial.flagged), count + 1)
    return totals


def calibration_table(
    outcomes: list[FilingOutcome],
) -> list[tuple[float, float, int, int]]:
    points: list[tuple[float, bool]] = []
    for outcome in outcomes:
        points.extend(outcome.clean_points)
        points.extend((trial.confidence, True) for trial in outcome.trials)
    rows = []
    for low, high in CONFIDENCE_BANDS:
        band = [wrong for confidence, wrong in points if low <= confidence < high]
        correct = sum(1 for wrong in band if not wrong)
        rows.append((low, min(high, 1.0), len(band), correct))
    return rows


def sweep_year_on_year(
    filings: list[FilingInput], config: Config, thresholds: list[float]
) -> list[SweepPoint]:
    points: list[SweepPoint] = []
    for threshold in thresholds:
        active = config.model_copy(update={"year_on_year_threshold": threshold})
        outcomes = evaluate_all(filings, active)
        trial_count = sum(len(outcome.trials) for outcome in outcomes)
        caught = sum(int(trial.flagged) for outcome in outcomes for trial in outcome.trials)
        clean_total = sum(outcome.clean_total for outcome in outcomes)
        clean_flagged = sum(outcome.clean_flagged for outcome in outcomes)
        points.append(
            SweepPoint(
                threshold=threshold,
                clean_flag_rate=clean_flagged / clean_total if clean_total else 0.0,
                caught_rate=caught / trial_count if trial_count else None,
                trials=trial_count,
            )
        )
    return points
