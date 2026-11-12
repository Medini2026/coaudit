from datetime import datetime

import httpx
import pytest
from pydantic import ValidationError

from coaudit.analysis.pipeline import analyze_filing
from coaudit.contracts import Config, FilingBundle
from coaudit.sec.client import SecClient, SecClientError
from coaudit.settings import load_config, save_config
from test_validation import BALANCED_HTML

URL = "https://data.sec.gov/submissions/CIK0000320193.json"


def test_a_missing_config_file_gives_the_defaults(tmp_path):
    assert load_config(tmp_path / "missing.json") == Config()


def test_a_partial_config_file_keeps_the_other_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"year_on_year_threshold": 0.5}', encoding="utf-8")
    config = load_config(path)
    assert config.year_on_year_threshold == 0.5
    assert config.confidence_threshold == 0.85


def test_a_config_saved_to_disk_is_read_back_unchanged(tmp_path):
    path = tmp_path / "config.json"
    original = Config(confidence_threshold=0.9, year_on_year_threshold=0.4)
    save_config(original, path)
    assert load_config(path) == original


def test_invalid_config_values_are_rejected(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"confidence_threshold": 3}', encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(path)


def test_unknown_config_keys_are_rejected(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"surprise": 1}', encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(path)


def test_the_default_config_file_matches_the_built_in_defaults():
    assert load_config("config.json") == Config()


def test_the_pipeline_uses_the_config_file_when_no_config_is_given(tmp_path, monkeypatch):
    html_path = tmp_path / "filing.html"
    html_path.write_text(BALANCED_HTML, encoding="utf-8")
    bundle = FilingBundle(
        ticker="EXM",
        cik="0000320193",
        company_name="Example Corp",
        accession_number="0000320193-26-000026",
        url="https://www.sec.gov/Archives/edgar/data/320193/x/doc.htm",
        downloaded_at=datetime(2026, 11, 12, 16, 0, 0),
        document_hash="a" * 64,
        html_path=str(html_path),
        fiscal_year_end=datetime(2026, 9, 26).date(),
    )
    monkeypatch.chdir(tmp_path)
    assert not any(decision.flagged for decision in analyze_filing(bundle).decisions)
    save_config(Config(year_on_year_threshold=0.05), tmp_path / "config.json")
    assert any(decision.flagged for decision in analyze_filing(bundle).decisions)


def build_client(tmp_path, requests, offline=False):
    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    return SecClient(
        "Test Team test@example.com",
        tmp_path,
        transport=httpx.MockTransport(handler),
        min_interval_seconds=0.0,
        offline=offline,
    )


def test_offline_mode_refuses_to_contact_the_sec(tmp_path):
    requests = []
    client = build_client(tmp_path, requests, offline=True)
    with pytest.raises(SecClientError) as error:
        client.fetch(URL)
    assert "Offline mode" in str(error.value)
    assert requests == []


def test_offline_mode_still_serves_saved_copies(tmp_path):
    requests = []
    build_client(tmp_path, requests).fetch(URL)
    offline_client = build_client(tmp_path, requests, offline=True)
    result = offline_client.fetch(URL)
    assert result.from_cache is True
    assert len(requests) == 1


def test_the_environment_variable_turns_offline_mode_on(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Test Team test@example.com")
    monkeypatch.setenv("COAUDIT_OFFLINE", "1")
    assert SecClient.from_environment(tmp_path).offline is True
    monkeypatch.setenv("COAUDIT_OFFLINE", "0")
    assert SecClient.from_environment(tmp_path).offline is False


def test_an_explicit_offline_choice_beats_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Test Team test@example.com")
    monkeypatch.setenv("COAUDIT_OFFLINE", "0")
    assert SecClient.from_environment(tmp_path, offline=True).offline is True
