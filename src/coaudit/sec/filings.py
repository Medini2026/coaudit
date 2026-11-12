import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Optional

from coaudit.contracts import FilingBundle
from coaudit.sec.client import SecClient

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_URL = (
    "https://www.sec.gov/Archives/edgar/data/{cik_number}/{accession_folder}/{document}"
)
ANNUAL_REPORT_FORM = "10-K"


class FilingNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class AnnualReportRef:
    accession_number: str
    primary_document: str
    filing_date: date
    report_date: Optional[date]


def lookup_cik(client: SecClient, ticker: str) -> tuple[str, str]:
    entries = client.fetch(TICKERS_URL).json()
    rows = entries.values() if isinstance(entries, dict) else entries
    wanted = ticker.strip().upper()
    for entry in rows:
        if str(entry["ticker"]).upper() == wanted:
            return f"{int(entry['cik_str']):010d}", str(entry["title"])
    raise FilingNotFoundError(f"No SEC company found for ticker {ticker}")


def find_latest_annual_report(client: SecClient, cik: str) -> AnnualReportRef:
    submissions = client.fetch(SUBMISSIONS_URL.format(cik=cik)).json()
    recent = submissions["filings"]["recent"]
    candidates = []
    for index, form in enumerate(recent["form"]):
        if form != ANNUAL_REPORT_FORM:
            continue
        report_text = recent["reportDate"][index]
        candidates.append(
            AnnualReportRef(
                accession_number=recent["accessionNumber"][index],
                primary_document=recent["primaryDocument"][index],
                filing_date=date.fromisoformat(recent["filingDate"][index]),
                report_date=date.fromisoformat(report_text) if report_text else None,
            )
        )
    if not candidates:
        raise FilingNotFoundError(f"No 10-K found in the recent filings of CIK {cik}")
    return max(candidates, key=lambda candidate: candidate.filing_date)


def build_document_url(cik: str, accession_number: str, primary_document: str) -> str:
    return ARCHIVE_URL.format(
        cik_number=int(cik),
        accession_folder=accession_number.replace("-", ""),
        document=primary_document,
    )


def fetch_filing(ticker: str, client: SecClient) -> FilingBundle:
    cik, company_name = lookup_cik(client, ticker)
    reference = find_latest_annual_report(client, cik)
    url = build_document_url(
        cik, reference.accession_number, reference.primary_document
    )
    result = client.fetch(url)
    html_path = (
        client.cache_dir / "filings" / f"{ticker.upper()}_{reference.accession_number}.html"
    )
    html_path.parent.mkdir(parents=True, exist_ok=True)
    if not html_path.exists():
        html_path.write_bytes(result.body)
    return FilingBundle(
        ticker=ticker.strip().upper(),
        cik=cik,
        company_name=company_name,
        accession_number=reference.accession_number,
        url=url,
        downloaded_at=result.downloaded_at,
        document_hash=hashlib.sha256(result.body).hexdigest(),
        html_path=str(html_path),
        fiscal_year_end=reference.report_date,
        tables=[],
        from_cache=result.from_cache,
    )
