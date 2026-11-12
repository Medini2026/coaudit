import sys

from coaudit.contracts import Config
from coaudit.evaluation.harness import (
    FilingInput,
    calibration_table,
    detection_by_kind,
    evaluate_all,
    sweep_year_on_year,
)
from coaudit.parsing.tables import load_tables
from coaudit.sec.client import SecClient, SecClientError
from coaudit.sec.filings import FilingNotFoundError, fetch_filing
from coaudit.settings import load_config

USAGE = "Usage: python -m coaudit.evaluation TICKER [TICKER ...] [--offline]"
SWEEP_LIMITS = [0.10, 0.25, 0.50, 1.00]


def percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "n/a"


def main(arguments: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    offline = "--offline" in arguments
    tickers = [argument for argument in arguments if not argument.startswith("--")]
    if not tickers:
        print(USAGE)
        return 2
    try:
        client = SecClient.from_environment("data/cache", offline=True if offline else None)
    except SecClientError as error:
        print(error)
        return 1
    filings: list[FilingInput] = []
    for ticker in tickers:
        try:
            bundle = fetch_filing(ticker, client)
        except (SecClientError, FilingNotFoundError) as error:
            print(f"{ticker}: {error}")
            continue
        filings.append(FilingInput(bundle=bundle, tables=load_tables(bundle)))
    client.close()
    if not filings:
        return 1
    config: Config = load_config()
    outcomes = evaluate_all(filings, config)
    print("SYNTHETIC ERROR INJECTION: errors are inserted on purpose into values that")
    print("were auto-approved, to measure how many our checks would catch.")
    print(f"filings: {len(filings)}   year-on-year limit {config.year_on_year_threshold:.0%}   "
          f"confidence threshold {config.confidence_threshold:.2f}")
    print()
    print(f"  {'fault':<14}{'trials':>8}{'caught':>8}{'missed':>8}{'detection':>12}")
    total_caught = 0
    total_trials = 0
    for kind, (caught, count) in detection_by_kind(outcomes).items():
        total_caught += caught
        total_trials += count
        print(f"  {kind.value:<14}{count:>8}{caught:>8}{count - caught:>8}{percent(caught, count):>12}")
    print(f"  {'ALL':<14}{total_trials:>8}{total_caught:>8}{total_trials - total_caught:>8}"
          f"{percent(total_caught, total_trials):>12}")
    print()
    print("CALIBRATION: how often a value in each confidence band is really correct")
    print("(clean values are correct; injected values are wrong)")
    print(f"  {'confidence band':<18}{'values':>8}{'correct':>10}")
    for low, high, count, correct in calibration_table(outcomes):
        print(f"  {low:.2f} to {high:.2f}       {count:>8}{percent(correct, count):>10}")
    print()
    print("ESCALATION CURVE: changing the year-on-year limit")
    print(f"  {'limit':<8}{'values flagged (clean)':>24}{'injected errors caught':>26}")
    for point in sweep_year_on_year(filings, config, SWEEP_LIMITS):
        caught = f"{100 * point.caught_rate:.0f}%" if point.caught_rate is not None else "n/a"
        print(f"  {point.threshold:<8.0%}{100 * point.clean_flag_rate:>23.0f}%{caught:>26}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
