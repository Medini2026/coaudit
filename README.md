# Co Audit

A human-in-the-loop financial filing auditor (MineArena 1.0, PS-01). It downloads a company's latest 10-K from the SEC, reads 8 key figures, checks them, scores its confidence, and pauses for a human review when a value is doubtful. Every human decision goes into an append-only audit log.

## Setup

1. Python 3.12 or newer.
2. Create and activate a virtual environment, then install:

```
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and set `SEC_USER_AGENT` to your team name and a contact address. Never commit `.env`.
4. Run the tests:

```
python -m pytest -q
```

On Windows PowerShell, set the code path before running modules: `$env:PYTHONPATH="src"`.

## Command-line tools

| Command | What it does |
|---|---|
| `python -m coaudit.sec AAPL` | Downloads and caches the latest 10-K |
| `python -m coaudit.extraction AAPL` | Shows the 8 extracted figures |
| `python -m coaudit.groundtruth AAPL MSFT` | Compares with the SEC's official XBRL data |
| `python -m coaudit.analysis AAPL --offline` | Runs the checks, scores and flags |
| `python -m coaudit.evaluation AAPL MSFT --offline` | Error-injection test, calibration and escalation curve |
| `python -m coaudit.demo AAPL --offline` | The whole pause, correct and resume flow in one command, with the fault switch on |

Thresholds live in `config.json`. Add `--offline` to use only saved copies.

## Start the backend API

```
uvicorn coaudit.api.main:app --port 8000
```

Data is stored in `data/coaudit.db` (runs, review items, audit log) and `data/checkpoints.db` (workflow progress). Interactive docs are at `http://localhost:8000/docs`.

| Method and path | What it does |
|---|---|
| `GET /health` | Liveness check |
| `POST /runs` | Start an audit. Body: `{"ticker": "AAPL", "fault_switch": false}` |
| `GET /runs` | List runs |
| `GET /runs/{thread_id}` | One run: metrics, checks, flags, status, step ledger |
| `GET /reviews?status=PENDING` | Review items (`PENDING` or `DONE`), optional `thread_id` |
| `POST /reviews/{id}/decision` | Body: `{"action": "APPROVE" or "EDIT" or "REJECT", "corrected_value": 123.0, "notes": "", "reviewer_id": "r1"}` |
| `GET /audit?thread_id=...` | The append-only audit log |
| `GET /report/{thread_id}` | Accuracy against the SEC, before and after review |
| `GET /config`, `PUT /config` | Read and change the thresholds |

A run pauses after the checks if any value is flagged. When the last pending review gets a decision, the run resumes by itself and finishes with the accuracy report. Corrected values are in dollars.

## The fault switch (live demo)

`POST /runs` with `"fault_switch": true` multiplies one value (operating cash flow) by 1,000 before the checks. The normal checks catch it, the run pauses, and a reviewer corrects it. Decisions on such values are tagged `simulated` in the audit log. This is a simulation and must be presented as one.

## How the audit log is protected

- The application code can only add rows.
- Database triggers reject `UPDATE` and `DELETE`.
- The application's connection denies `UPDATE`, `DELETE`, `DROP` and `ALTER` on the table.

Someone who opens the database file with outside tools and removes the triggers could still change it. That limit is real.

## Known limits

- Extraction is rule-based and tuned on 8 companies. Unusual statement wording may need a new rule.
- Values we derive ourselves (for example Walmart's gross profit) are always flagged for a human look.
- An LLM second reading is not built yet.
- All code was written during the 24-hour event. Libraries used: pydantic, httpx, beautifulsoup4, lxml, python-dotenv, pytest, langgraph, fastapi, uvicorn (all open source).
