import sys

from coaudit.extraction.rules import extract_metrics_by_rules
from coaudit.llm.client import LlmError, client_from_environment
from coaudit.llm.reading import read_with_llm, second_reading
from coaudit.parsing.tables import load_tables
from coaudit.sec.client import SecClient, SecClientError
from coaudit.sec.filings import FilingNotFoundError, fetch_filing

USAGE = "Usage: python -m coaudit.llm TICKER [TICKER ...] [--offline]"


def main(arguments: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    offline = "--offline" in arguments
    tickers = [argument for argument in arguments if not argument.startswith("--")]
    if not tickers:
        print(USAGE)
        return 2
    try:
        llm = client_from_environment()
    except LlmError as error:
        print(error)
        return 1
    if llm is None:
        print("No LLM key found. Set LLM_API_KEY (and LLM_PROVIDER) in your .env file.")
        return 1
    try:
        client = SecClient.from_environment("data/cache", offline=True if offline else None)
    except SecClientError as error:
        print(error)
        return 1
    exit_code = 0
    for ticker in tickers:
        try:
            bundle = fetch_filing(ticker, client)
        except (SecClientError, FilingNotFoundError) as error:
            print(f"{ticker}: {error}")
            exit_code = 1
            continue
        tables = load_tables(bundle)
        metrics = extract_metrics_by_rules(bundle, tables)
        try:
            reading = read_with_llm(bundle, tables, llm)
        except LlmError as error:
            print(f"{bundle.ticker}: the AI reading failed: {error}")
            exit_code = 1
            continue
        extras, results = second_reading(bundle, tables, metrics, llm)
        by_llm = {metric.metric_name: metric for metric in reading.metrics}
        print(f"{bundle.ticker}  {bundle.company_name}  (model: {getattr(llm, 'model', 'unknown')})")
        for metric, result in zip(metrics, results):
            ai = by_llm.get(metric.metric_name)
            ai_text = f"{ai.value:>20,.0f}" if ai else f"{'-':>20}"
            print(
                f"  {metric.metric_name.value:<22} rules {metric.value:>20,.0f}   AI {ai_text}   "
                f"{result.status.value}"
            )
        for name, reason in reading.rejected:
            print(f"  rejected by the grounding check: {name}: {reason}")
        for metric in extras:
            print(f"  AI-only figure: {metric.metric_name.value} {metric.value:,.0f}")
    client.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
