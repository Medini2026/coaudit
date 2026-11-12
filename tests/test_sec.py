import hashlib
from datetime import date

import httpx
import pytest

from coaudit.contracts import FilingBundle
from coaudit.sec.client import SecClient, SecClientError
from coaudit.sec.filings import (
    SUBMISSIONS_URL,
    TICKERS_URL,
    FilingNotFoundError,
    build_document_url,
    fetch_filing,
    find_latest_annual_report,
    lookup_cik,
)

TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
}

SUBMISSIONS = {
    "cik": "320193",
    "name": "Apple Inc.",
    "filings": {
        "recent": {
            "accessionNumber": [
                "0000320193-25-000100",
                "0000320193-25-000079",
                "0000320193-24-000123",
                "0000320193-25-000050",
            ],
            "filingDate": ["2025-11-03", "2025-10-31", "2024-11-01", "2025-08-01"],
            "reportDate": ["2025-09-27", "2025-09-27", "2024-09-28", "2025-06-28"],
            "form": ["10-K/A", "10-K", "10-K", "10-Q"],
            "primaryDocument": [
                "amendment.htm",
                "aapl-20250927.htm",
                "aapl-20240928.htm",
                "aapl-20250628.htm",
            ],
        }
    },
}

HTML_BODY = b"<html><body><table><tr><td>Total assets</td><td>500</td></tr></table></body></html>"
APPLE_SUBMISSIONS_URL = SUBMISSIONS_URL.format(cik="0000320193")
APPLE_DOCUMENT_URL = (
    "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm"
)


class FakeSec:
    def __init__(self):
        self.requests = []
        self.user_agents = []

    def __call__(self, request):
        url = str(request.url)
        self.requests.append(url)
        self.user_agents.append(request.headers.get("user-agent"))
        if url == TICKERS_URL:
            return httpx.Response(200, json=TICKERS)
        if url == APPLE_SUBMISSIONS_URL:
            return httpx.Response(200, json=SUBMISSIONS)
        if url == APPLE_DOCUMENT_URL:
            return httpx.Response(200, content=HTML_BODY)
        return httpx.Response(404)


def build_client(tmp_path, fake):
    return SecClient(
        "Test Team test@example.com",
        tmp_path,
        transport=httpx.MockTransport(fake),
        min_interval_seconds=0.0,
    )


def test_client_requires_a_user_agent(tmp_path):
    with pytest.raises(SecClientError):
        SecClient("   ", tmp_path)


def test_client_sends_the_user_agent_on_every_request(tmp_path):
    fake = FakeSec()
    client = build_client(tmp_path, fake)
    client.fetch(TICKERS_URL)
    assert fake.user_agents == ["Test Team test@example.com"]


def test_lookup_cik_pads_to_ten_digits_and_ignores_case(tmp_path):
    client = build_client(tmp_path, FakeSec())
    assert lookup_cik(client, "aapl") == ("0000320193", "Apple Inc.")
    assert lookup_cik(client, " MSFT ") == ("0000789019", "MICROSOFT CORP")


def test_lookup_cik_rejects_unknown_ticker(tmp_path):
    client = build_client(tmp_path, FakeSec())
    with pytest.raises(FilingNotFoundError):
        lookup_cik(client, "NOPE")


def test_latest_annual_report_skips_amendments_and_quarterlies(tmp_path):
    client = build_client(tmp_path, FakeSec())
    reference = find_latest_annual_report(client, "0000320193")
    assert reference.accession_number == "0000320193-25-000079"
    assert reference.primary_document == "aapl-20250927.htm"
    assert reference.filing_date == date(2025, 10, 31)
    assert reference.report_date == date(2025, 9, 27)


def test_latest_annual_report_fails_when_there_is_no_10k(tmp_path):
    def handler(request):
        body = {
            "filings": {
                "recent": {
                    "accessionNumber": ["0000320193-25-000050"],
                    "filingDate": ["2025-08-01"],
                    "reportDate": ["2025-06-28"],
                    "form": ["10-Q"],
                    "primaryDocument": ["q.htm"],
                }
            }
        }
        return httpx.Response(200, json=body)

    client = SecClient(
        "Test Team test@example.com",
        tmp_path,
        transport=httpx.MockTransport(handler),
        min_interval_seconds=0.0,
    )
    with pytest.raises(FilingNotFoundError):
        find_latest_annual_report(client, "0000320193")


def test_document_url_drops_leading_zeros_and_dashes():
    url = build_document_url("0000320193", "0000320193-25-000079", "aapl-20250927.htm")
    assert url == APPLE_DOCUMENT_URL


def test_fetch_filing_returns_a_valid_bundle(tmp_path):
    client = build_client(tmp_path, FakeSec())
    bundle = fetch_filing("aapl", client)
    assert isinstance(bundle, FilingBundle)
    assert bundle.ticker == "AAPL"
    assert bundle.cik == "0000320193"
    assert bundle.accession_number == "0000320193-25-000079"
    assert bundle.fiscal_year_end == date(2025, 9, 27)
    assert bundle.document_hash == hashlib.sha256(HTML_BODY).hexdigest()
    assert bundle.from_cache is False
    with open(bundle.html_path, "rb") as saved:
        assert saved.read() == HTML_BODY


def test_second_fetch_is_served_from_the_cache(tmp_path):
    fake = FakeSec()
    client = build_client(tmp_path, fake)
    fetch_filing("AAPL", client)
    requests_after_first = len(fake.requests)
    second = fetch_filing("AAPL", client)
    assert len(fake.requests) == requests_after_first
    assert second.from_cache is True


def test_a_new_client_reuses_the_disk_cache(tmp_path):
    fake = FakeSec()
    fetch_filing("AAPL", build_client(tmp_path, fake))
    requests_after_first = len(fake.requests)
    fetch_filing("AAPL", build_client(tmp_path, fake))
    assert len(fake.requests) == requests_after_first


def test_error_status_is_reported(tmp_path):
    client = build_client(tmp_path, FakeSec())
    with pytest.raises(SecClientError):
        client.fetch("https://www.sec.gov/does-not-exist")
