import sys

from coaudit.analysis.pipeline import analyze_filing
from coaudit.contracts import CheckName, CheckStatus, Config, MetricName
from coaudit.groundtruth.compare import compare_to_ground_truth
from coaudit.groundtruth.xbrl import load_ground_truth
from coaudit.sec.client import SecClient, SecClientError
from coaudit.sec.filings import FilingNotFoundError, fetch_filing
from coaudit.settings import load_config

USAGE = (
    "Usage: python -m coaudit.analysis TICKER [TICKER ...] "
    "[--confidence=0.85] [--yoy=0.25] [--offline]"
)


def parse_arguments(arguments: list[str]) -> tuple[list[str], Config, bool]:
    tickers: list[str] = []
    settings = load_config().model_dump()
    offline = False
    for argument in arguments:
        if argument.startswith("--confidence="):
            settings["confidence_threshold"] = float(argument.split("=", 1)[1])
        elif argument.startswith("--yoy="):
            settings["year_on_year_threshold"] = float(argument.split("=", 1)[1])
        elif argument == "--offline":
            offline = True
        else:
            tickers.append(argument)
    return tickers, Config(**settings), offline


def main(arguments: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        tickers, config, offline = parse_arguments(arguments)
    except ValueError:
        print(USAGE)
        return 2
    if not tickers:
        print(USAGE)
        return 2
    try:
        client = SecClient.from_environment("data/cache", offline=True if offline else None)
    except SecClientError as error:
        print(error)
        return 1
    exit_code = 0
    totals = {
        "auto": 0,
        "flagged": 0,
        "auto_wrong": 0,
        "flagged_wrong": 0,
        "flagged_anomaly": 0,
    }
    for ticker in tickers:
        try:
            bundle = fetch_filing(ticker, client)
            analysis = analyze_filing(bundle, config)
            truth = load_ground_truth(client, bundle)
        except (SecClientError, FilingNotFoundError) as error:
            print(f"{ticker}: {error}")
            exit_code = 1
            continue
        print(
            f"{bundle.ticker}  {bundle.company_name}  period end {bundle.fiscal_year_end}  "
            f"(confidence threshold {config.confidence_threshold:.2f}, "
            f"year-on-year limit {config.year_on_year_threshold:.0%})"
        )
        source = (
            f"saved copy from {bundle.downloaded_at:%Y-%m-%d %H:%M}"
            if bundle.from_cache
            else "downloaded from the SEC just now"
        )
        print(f"  source: {source}")
        flagged_names = [
            MetricName(decision.metric_name) for decision in analysis.decisions if decision.flagged
        ]
        report = compare_to_ground_truth(bundle, analysis.metrics, truth, flagged_names)
        outcomes = {outcome.metric_name: outcome for outcome in report.outcomes}
        for metric, decision in zip(analysis.metrics, analysis.decisions):
            status = "FLAG" if decision.flagged else "AUTO"
            print(
                f"  {metric.metric_name.value:<22} {metric.value:>20,.0f}   "
                f"confidence {metric.confidence:.2f}   {status}"
            )
            for reason in decision.reasons:
                print(f"      - {reason}")
            outcome = outcomes[metric.metric_name]
            wrong = outcome.is_correct is False
            key = "flagged" if decision.flagged else "auto"
            totals[key] += 1
            if wrong:
                totals[f"{key}_wrong"] += 1
            year_on_year_failed = any(
                result.status == CheckStatus.FAILED
                and result.check_name == CheckName.YEAR_ON_YEAR
                and metric.metric_name in result.metrics_involved
                for result in analysis.validations
            )
            if decision.flagged and outcome.is_correct is True and year_on_year_failed:
                totals["flagged_anomaly"] += 1
        counts = {status: 0 for status in CheckStatus}
        for result in analysis.validations:
            counts[result.status] += 1
        print(
            f"  checks: {counts[CheckStatus.PASSED]} passed, "
            f"{counts[CheckStatus.FAILED]} failed, "
            f"{counts[CheckStatus.NOT_APPLICABLE]} not applicable"
        )
        for result in analysis.validations:
            if result.status != CheckStatus.PASSED:
                print(f"    {result.status.value:<14} {result.check_name.value}: {result.reason}")
    justified = totals["flagged_wrong"] + totals["flagged_anomaly"]
    precision = f"{100 * justified / totals['flagged']:.0f}%" if totals["flagged"] else "n/a"
    print(
        f"ALL COMPANIES: auto-approved {totals['auto']} "
        f"(wrong among them: {totals['auto_wrong']}); "
        f"flagged {totals['flagged']}: {totals['flagged_wrong']} real extraction errors, "
        f"{totals['flagged_anomaly']} real year-on-year changes confirmed by the SEC; "
        f"escalation precision {precision}"
    )
    client.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
