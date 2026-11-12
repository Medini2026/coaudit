import sys

from coaudit.sec.client import SecClient, SecClientError
from coaudit.sec.filings import FilingNotFoundError, fetch_filing


def main(arguments: list[str]) -> int:
    if not arguments:
        print("Usage: python -m coaudit.sec TICKER [TICKER ...]")
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
        print(f"{bundle.ticker}  {bundle.company_name}")
        print(f"  accession  {bundle.accession_number}")
        print(f"  period end {bundle.fiscal_year_end}")
        print(f"  url        {bundle.url}")
        print(f"  sha256     {bundle.document_hash}")
        print(f"  saved to   {bundle.html_path}")
        print(f"  from cache {bundle.from_cache}")
    client.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
