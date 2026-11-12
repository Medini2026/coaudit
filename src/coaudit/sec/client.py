import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from dotenv import load_dotenv


OFFLINE_VALUES = {"1", "true", "yes", "on"}


class SecClientError(Exception):
    pass


@dataclass(frozen=True)
class FetchResult:
    body: bytes
    downloaded_at: datetime
    from_cache: bool

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class SecClient:
    def __init__(
        self,
        user_agent: str,
        cache_dir,
        transport: Optional[httpx.BaseTransport] = None,
        min_interval_seconds: float = 0.12,
        offline: bool = False,
    ) -> None:
        if not user_agent or not user_agent.strip():
            raise SecClientError(
                "SEC_USER_AGENT is required: use your team name and a contact address"
            )
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval_seconds = min_interval_seconds
        self.offline = offline
        self._last_request_at = 0.0
        self._http = httpx.Client(
            headers={"User-Agent": user_agent.strip()},
            transport=transport,
            timeout=30.0,
            follow_redirects=True,
        )

    @classmethod
    def from_environment(cls, cache_dir, offline: Optional[bool] = None) -> "SecClient":
        load_dotenv()
        if offline is None:
            offline = os.environ.get("COAUDIT_OFFLINE", "").strip().lower() in OFFLINE_VALUES
        return cls(os.environ.get("SEC_USER_AGENT", ""), cache_dir, offline=offline)

    def fetch(self, url: str) -> FetchResult:
        body_path, meta_path = self._cache_paths(url)
        if body_path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return FetchResult(
                body=body_path.read_bytes(),
                downloaded_at=datetime.fromisoformat(meta["downloaded_at"]),
                from_cache=True,
            )
        if self.offline:
            raise SecClientError(f"Offline mode: {url} is not in the saved copies")
        self._wait_for_slot()
        response = self._http.get(url)
        self._last_request_at = time.monotonic()
        if response.status_code != 200:
            raise SecClientError(
                f"SEC returned status {response.status_code} for {url}"
            )
        downloaded_at = datetime.now(timezone.utc)
        meta = json.dumps({"url": url, "downloaded_at": downloaded_at.isoformat()})
        self._write_atomic(body_path, response.content)
        self._write_atomic(meta_path, meta.encode("utf-8"))
        return FetchResult(
            body=response.content, downloaded_at=downloaded_at, from_cache=False
        )

    def close(self) -> None:
        self._http.close()

    def _cache_paths(self, url: str):
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return (
            self.cache_dir / f"{digest}.body",
            self.cache_dir / f"{digest}.json",
        )

    def _wait_for_slot(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)

    @staticmethod
    def _write_atomic(path: Path, content: bytes) -> None:
        temporary_path = path.with_suffix(path.suffix + ".tmp")
        temporary_path.write_bytes(content)
        os.replace(temporary_path, path)
