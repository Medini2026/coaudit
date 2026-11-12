# Handoff: how to use the data and checks code

This is for the people building the workflow, API and dashboard. Everything here is plain Python and returns the shared data shapes from `src/coaudit/contracts.py`.

## Setup

- Python 3.12 or newer, with `pydantic httpx beautifulsoup4 lxml python-dotenv` installed.
- A `.env` file with `SEC_USER_AGENT` (team name and a contact address). Never commit it.
- Run from the project folder with `src` on the Python path.
- The first call for a company downloads from the SEC and takes a few seconds. Every later call reads the local cache in `data/cache`.
- `SecClient` is not thread-safe. Call it from one place at a time.

## The calls

| Call | Module | Returns |
|---|---|---|
| `SecClient.from_environment("data/cache")` | `coaudit.sec.client` | a client that rate-limits and caches |
| `fetch_filing(ticker, client)` | `coaudit.sec.filings` | `FilingBundle` |
| `analyze_filing(bundle, config)` | `coaudit.analysis.pipeline` | `FilingAnalysis` |
| `load_ground_truth(client, bundle)` | `coaudit.groundtruth.xbrl` | dict of official SEC values by metric |
| `compare_to_ground_truth(bundle, metrics, truth, flagged)` | `coaudit.groundtruth.compare` | `AccuracyReport` |

`config` is optional. It is a `Config` from the contract: `confidence_threshold` (0.85), `year_on_year_threshold` (0.25), `equation_tolerance_units` (1.0), `spot_check_rate` (0.05). The dashboard sliders map to the first two.

## What `analyze_filing` returns

`FilingAnalysis` has four fields:

- `bundle`: the `FilingBundle` (ticker, CIK, company name, accession number, URL, download time, document hash, saved file path, fiscal year end, `from_cache`).
- `metrics`: a list of `ExtractedMetric`, up to 8 per filing, already scored. Each has `metric_name`, `value` (USD), `prior_value` (last year, USD), `period_end`, `fiscal_label`, `unit_scale`, `source_snippet`, `source_location` (table id and row index), `extractor` (`parser` or `derived`), `confidence` (0 to 1) and `confidence_signals`.
- `validations`: a list of `ValidationResult`. Each has `check_name` (`accounting_equation`, `segment_sum` or `year_on_year`), `status` (`PASSED`, `FAILED` or `NOT_APPLICABLE`), a plain `reason` and `metrics_involved`.
- `decisions`: one `RoutingDecision` per metric with `metric_name`, `flagged` (true or false) and `reasons` (a list of strings).

The metrics and decisions are in the same order. A flagged metric is one that needs a human. Its `reasons` is ready to show on screen.

To send any of these as JSON, call `model_dump(mode="json")` on the Pydantic objects. `RoutingDecision` is a dataclass, so use `dataclasses.asdict`.

## Minimal example

```python
from coaudit.analysis.pipeline import analyze_filing
from coaudit.contracts import MetricName
from coaudit.groundtruth.compare import compare_to_ground_truth
from coaudit.groundtruth.xbrl import load_ground_truth
from coaudit.sec.client import SecClient
from coaudit.sec.filings import fetch_filing

client = SecClient.from_environment("data/cache")
bundle = fetch_filing("AAPL", client)
analysis = analyze_filing(bundle)
truth = load_ground_truth(client, bundle)
flagged = [MetricName(d.metric_name) for d in analysis.decisions if d.flagged]
report = compare_to_ground_truth(bundle, analysis.metrics, truth, flagged)
```

`report` is an `AccuracyReport`: `overall_accuracy`, `per_metric`, `auto_approved_count`, `escalated_count`, `escalation_precision` and one `ValueOutcome` per metric.

## What the workflow should do with a flagged metric

1. Show the value, its confidence, the `reasons` and its `source_snippet` to the reviewer.
2. Record the decision (approve, correct with a new value, or reject) in the append-only audit log.
3. When every flagged metric has a decision, build the final list of metrics: keep approved ones, replace corrected ones, drop rejected ones.
4. Call `compare_to_ground_truth` on the final list for the accuracy report.

The reviewer types corrected values in dollars (the same unit as `value`).

## Try it from the terminal

Each command takes tickers. These work today:

- `python -m coaudit.sec AAPL` downloads and caches a filing.
- `python -m coaudit.parsing AAPL --kind=balance_sheet --rows=40` shows the parsed table.
- `python -m coaudit.extraction AAPL` shows the 8 extracted metrics.
- `python -m coaudit.groundtruth AAPL` compares with the SEC's official data.
- `python -m coaudit.analysis AAPL --confidence=0.85 --yoy=0.25` runs everything and shows flags.

## Tested on real filings

Apple, Microsoft, Nvidia, Walmart, Tesla and Costco, plus Amazon and Coca-Cola. Across the first six: 30 values auto-approved with none wrong, and 17 flagged (15 real year-on-year changes and 2 values we derived ourselves).

## Known limits

- The extraction is rule-based. It matches the wording of the statement rows, so an unusual company may need a new rule.
- Values we derive ourselves (such as Walmart's gross profit and total liabilities) are always flagged for a human look, and carry `extractor = derived`.
- The LLM second reading and its grounding check are built (`src/coaudit/llm`) and covered by tests with a simulated model service. They run only when `LLM_API_KEY` is set in `.env`. Accuracy with a real model has to be checked with `python -m coaudit.llm TICKER --offline`.
