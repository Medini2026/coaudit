from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from coaudit.contracts import FilingBundle, MetricName
from coaudit.sec.client import SecClient

COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
ANNUAL_DAY_RANGE = range(340, 381)

XBRL_TAGS: dict[MetricName, tuple[str, ...]] = {
    MetricName.REVENUE: (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    ),
    MetricName.NET_INCOME: ("NetIncomeLoss", "ProfitLoss"),
    MetricName.TOTAL_ASSETS: ("Assets",),
    MetricName.TOTAL_LIABILITIES: ("Liabilities",),
    MetricName.SHAREHOLDERS_EQUITY: (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    MetricName.OPERATING_CASH_FLOW: (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    MetricName.GROSS_PROFIT: ("GrossProfit",),
    MetricName.OPERATING_INCOME: ("OperatingIncomeLoss",),
}


@dataclass(frozen=True)
class GroundTruthValue:
    metric: MetricName
    tag: str
    value: float
    period_end: date
    accession_number: str
    form: str
    filed: str


def is_annual_entry(entry: dict[str, Any]) -> bool:
    start = entry.get("start")
    if start is None:
        return True
    days = (date.fromisoformat(entry["end"]) - date.fromisoformat(start)).days
    return days in ANNUAL_DAY_RANGE


def find_fact(
    company_facts: dict[str, Any],
    metric: MetricName,
    period_end: date,
    accession_number: str,
) -> Optional[GroundTruthValue]:
    concepts = company_facts.get("facts", {}).get("us-gaap", {})
    for tag in XBRL_TAGS[metric]:
        concept = concepts.get(tag)
        if concept is None:
            continue
        entries = concept.get("units", {}).get("USD", [])
        candidates = [
            entry
            for entry in entries
            if entry.get("end") == period_end.isoformat()
            and str(entry.get("form", "")).startswith("10-K")
            and is_annual_entry(entry)
        ]
        if not candidates:
            continue
        same_filing = [e for e in candidates if e.get("accn") == accession_number]
        chosen = same_filing[0] if same_filing else max(
            candidates, key=lambda entry: entry.get("filed", "")
        )
        return GroundTruthValue(
            metric=metric,
            tag=tag,
            value=float(chosen["val"]),
            period_end=period_end,
            accession_number=str(chosen.get("accn", "")),
            form=str(chosen.get("form", "")),
            filed=str(chosen.get("filed", "")),
        )
    return None


def load_ground_truth(
    client: SecClient, bundle: FilingBundle
) -> dict[MetricName, GroundTruthValue]:
    if bundle.fiscal_year_end is None:
        return {}
    company_facts = client.fetch(COMPANY_FACTS_URL.format(cik=bundle.cik)).json()
    truth: dict[MetricName, GroundTruthValue] = {}
    for metric in XBRL_TAGS:
        found = find_fact(
            company_facts, metric, bundle.fiscal_year_end, bundle.accession_number
        )
        if found is not None:
            truth[metric] = found
    return truth
