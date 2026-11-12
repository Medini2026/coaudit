import sys
from typing import Optional

from coaudit.contracts import TableKind
from coaudit.parsing.tables import load_tables, select_statement_tables
from coaudit.sec.client import SecClient, SecClientError
from coaudit.sec.filings import FilingNotFoundError, fetch_filing

DEFAULT_ROWS = 14
USAGE = "Usage: python -m coaudit.parsing TICKER [TICKER ...] [--kind=balance_sheet] [--rows=40]"


def format_values(values: tuple[float, ...]) -> str:
    return "  ".join(f"{value:,.2f}".rstrip("0").rstrip(".") for value in values[:3])


def print_table(kind: TableKind, table, rows_to_show: int) -> None:
    order = "oldest year first" if table.columns_ascending else "newest year first"
    print(
        f"  {kind.value}: {table.table_id}, {len(table.rows)} rows, "
        f"scale x{table.unit_scale:,}, columns {order}"
    )
    print(f"    title: {table.title or '(none found)'}")
    for row in table.rows[:rows_to_show]:
        print(f"    {row.index:>3}  {row.label[:60]:<60}  {format_values(row.values)}")


def parse_arguments(arguments: list[str]) -> tuple[list[str], int, Optional[TableKind]]:
    tickers: list[str] = []
    rows_to_show = DEFAULT_ROWS
    only_kind: Optional[TableKind] = None
    for argument in arguments:
        if argument.startswith("--rows="):
            rows_to_show = int(argument.split("=", 1)[1])
        elif argument.startswith("--kind="):
            only_kind = TableKind(argument.split("=", 1)[1])
        else:
            tickers.append(argument)
    return tickers, rows_to_show, only_kind


def main(arguments: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        tickers, rows_to_show, only_kind = parse_arguments(arguments)
    except ValueError:
        print(USAGE)
        return 2
    if not tickers:
        print(USAGE)
        return 2
    try:
        client = SecClient.from_environment("data/cache")
    except SecClientError as error:
        print(error)
        return 1
    exit_code = 0
    kinds = (
        TableKind.BALANCE_SHEET,
        TableKind.INCOME_STATEMENT,
        TableKind.CASH_FLOW,
        TableKind.SEGMENT_NOTE,
    )
    for ticker in tickers:
        try:
            bundle = fetch_filing(ticker, client)
        except (SecClientError, FilingNotFoundError) as error:
            print(f"{ticker}: {error}")
            exit_code = 1
            continue
        tables = load_tables(bundle)
        chosen = select_statement_tables(tables)
        print(f"{bundle.ticker}  {bundle.company_name}  ({len(tables)} tables found)")
        for kind in kinds:
            if only_kind is not None and kind != only_kind:
                continue
            if kind in chosen:
                print_table(kind, chosen[kind], rows_to_show)
            else:
                print(f"  {kind.value}: NOT FOUND")
    client.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
