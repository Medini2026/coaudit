import sys

from coaudit.contracts import MetricName
from coaudit.extraction.rules import extract_filing_metrics
from coaudit.sec.client import SecClient, SecClientError
from coaudit.sec.filings import FilingNotFoundError, fetch_filing


def format_dollars(value) -> str:
    if value is None:
        return "-"
    return f"{value:,.0f}"


def main(arguments: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not arguments:
        print("Usage: python -m coaudit.extraction TICKER [TICKER ...]")
        return 2
    try:
        client = SecClient.from_environment("data/cache")
    except SecClientError as error:
        print(error)
        return 1
    exit_code = 0
    for ticker in arguments:
        try:
            bundle = fetch_filing(ticker, client)
        except (SecClientError, FilingNotFoundError) as error:
            print(f"{ticker}: {error}")
            exit_code = 1
            continue
        metrics = extract_filing_metrics(bundle)
        print(f"{bundle.ticker}  {bundle.company_name}  period end {bundle.fiscal_year_end}")
        found = {metric.metric_name: metric for metric in metrics}
        for name in MetricName:
            metric = found.get(name)
            if metric is None:
                print(f"  {name.value:<22} NOT FOUND")
                continue
            print(
                f"  {name.value:<22} {format_dollars(metric.value):>20}"
                f"   prior {format_dollars(metric.prior_value):>20}"
                f"   {metric.extractor.value}"
            )
            print(f"    source: {metric.source_snippet[:90]}")
    client.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
