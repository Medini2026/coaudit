import sys

from coaudit.extraction.rules import extract_filing_metrics
from coaudit.groundtruth.compare import compare_to_ground_truth
from coaudit.groundtruth.xbrl import load_ground_truth
from coaudit.sec.client import SecClient, SecClientError
from coaudit.sec.filings import FilingNotFoundError, fetch_filing


def describe(outcome) -> str:
    if outcome.is_correct is None:
        return "no SEC value to compare"
    return "MATCH" if outcome.is_correct else "DIFFERENT"


def main(arguments: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not arguments:
        print("Usage: python -m coaudit.groundtruth TICKER [TICKER ...]")
        return 2
    try:
        client = SecClient.from_environment("data/cache")
    except SecClientError as error:
        print(error)
        return 1
    exit_code = 0
    total_correct = 0
    total_comparable = 0
    for ticker in arguments:
        try:
            bundle = fetch_filing(ticker, client)
            metrics = extract_filing_metrics(bundle)
            truth = load_ground_truth(client, bundle)
        except (SecClientError, FilingNotFoundError) as error:
            print(f"{ticker}: {error}")
            exit_code = 1
            continue
        report = compare_to_ground_truth(bundle, metrics, truth)
        print(f"{bundle.ticker}  {bundle.company_name}  period end {bundle.fiscal_year_end}")
        for outcome in report.outcomes:
            name = outcome.metric_name.value
            ours = f"{outcome.extracted_value:,.0f}"
            reference = truth.get(outcome.metric_name)
            official = f"{outcome.truth_value:,.0f}" if outcome.truth_value is not None else "-"
            tag = reference.tag if reference else ""
            print(f"  {name:<22} ours {ours:>20}   SEC {official:>20}   {describe(outcome)}  {tag}")
        comparable = sum(1 for outcome in report.outcomes if outcome.is_correct is not None)
        correct = sum(1 for outcome in report.outcomes if outcome.is_correct)
        total_correct += correct
        total_comparable += comparable
        print(f"  match: {correct} of {comparable} comparable values")
    if total_comparable:
        share = 100 * total_correct / total_comparable
        print(f"ALL COMPANIES: {total_correct} of {total_comparable} comparable values match ({share:.1f}%)")
    client.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
