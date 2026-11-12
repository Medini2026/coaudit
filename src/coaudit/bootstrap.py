from typing import Optional

from coaudit.contracts import FilingBundle
from coaudit.groundtruth.xbrl import load_ground_truth
from coaudit.llm.client import client_from_environment
from coaudit.sec.client import SecClient
from coaudit.sec.filings import fetch_filing
from coaudit.service import AuditService

DATABASE_PATH = "data/coaudit.db"
CHECKPOINT_PATH = "data/checkpoints.db"
CACHE_DIRECTORY = "data/cache"


def build_default_service(offline: Optional[bool] = None) -> AuditService:
    client = SecClient.from_environment(CACHE_DIRECTORY, offline=offline)

    def load_filing(ticker: str) -> FilingBundle:
        return fetch_filing(ticker, client)

    def load_truth(bundle: FilingBundle):
        return load_ground_truth(client, bundle)

    return AuditService(
        DATABASE_PATH, CHECKPOINT_PATH, load_filing, load_truth, llm=client_from_environment()
    )
