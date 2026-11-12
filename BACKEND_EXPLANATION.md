# Co Audit: Backend Explanation

Problem PS-01: a Human-in-the-Loop Financial Filing Auditor Agent. Python only.

## 1. What the backend does

It reads a company's annual report (10-K) from the SEC, extracts 8 key numbers, checks them with accounting tests, scores how sure it is, and pauses for a human reviewer when it is not sure. Every human decision is stored in a log that can only be added to.

## 2. The flow, step by step

| Step | What happens | Code |
|---|---|---|
| fetch | Looks up the company, finds its latest 10-K on the SEC and downloads it. Saves a local copy. | `sec/` |
| parse | Turns the HTML tables into numbers. Handles units (thousands, millions), column order, negative numbers and percentages. | `parsing/tables.py` |
| extract | Finds 8 metrics with rules: revenue, net income, total assets, total liabilities, shareholders' equity, operating cash flow, gross profit, operating income. Values we calculate ourselves are marked as derived. | `extraction/rules.py` |
| second reading | A language model reads the same tables on its own. Its numbers must exist in the table or they are rejected. | `llm/` |
| validate | Six accounting checks run on the numbers. | `validation/checks.py` |
| score | Each number gets a confidence score. | `scoring/confidence.py` |
| route | A value is flagged if its confidence is below the threshold or any check failed. | `scoring/confidence.py` |
| human review | The run pauses. A reviewer approves, edits or rejects each flagged value. | `workflow/graph.py` |
| finalize and verify | Applies the decisions, then compares the final numbers with the SEC's official data. | `groundtruth/` |

## 3. The six checks

1. Accounting equation: assets equal liabilities plus equity (with minority interest allowed).
2. Segment sum: segment figures add up to the total.
3. Year on year: flags a change bigger than the configured threshold.
4. Net income tie-out: net income on the income statement agrees with the other statements.
5. Gross profit identity: revenue minus cost of sales equals gross profit.
6. Operating income identity: gross profit minus operating costs equals operating income.

## 4. Confidence score

confidence = 0.35 x how the value was read + 0.15 x unit clarity + 0.50 x checks

- How it was read: parser 1.0, LLM 0.8, derived 0.4.
- Unit clarity: 1.0 if the unit is known, otherwise 0.5.
- Checks: 0.0 if any check failed, 1.0 if checks passed, 0.6 if no check applied.

A value is flagged when the score is below the threshold (0.85 by default) or any check failed. The threshold can be changed through the API.

## 5. The LLM second reading

- It is optional. It runs only when `LLM_API_KEY` is set in `.env`. Providers: gemini, openai, anthropic.
- The model gets the three statement tables as text and must name the row and the number for each metric.
- Grounding check: the row must exist, and the number must equal that row's most recent year value. Otherwise the answer is rejected. This stops invented numbers.
- If the model agrees with the rules, that is one more passed check. If it disagrees, the check fails and the value goes to a human.
- If the model service is down or the answer is bad, the run continues without it.
- The key is read from `.env`, never committed, and never put in a URL or error message.

## 6. Human in the loop

The workflow uses LangGraph. When values are flagged, the graph calls `interrupt()`. The state is saved in a SQLite checkpoint. When the last pending review is decided, the run resumes from the checkpoint. A step ledger records the steps that ran. A restarted server can resume a paused run.

## 7. Database

SQLite. Tables: runs, review items, step ledger, settings and the audit log.

The audit log is append-only:
- Triggers reject UPDATE and DELETE.
- The application connection denies UPDATE, DELETE, DROP TABLE, and ALTER or DROP of the triggers.
- Each entry stores the reviewer id, original value, final value, decision, notes, timestamp, and whether the value came from the simulated fault.

Limit: someone who opens the database file with outside tools and removes the triggers could still change it.

## 8. API (FastAPI)

| Method and path | Purpose |
|---|---|
| POST /runs | Start a run for a ticker. Optional `fault_switch` for the simulation. |
| GET /runs | List runs. |
| GET /runs/{id} | Status, metrics, checks, decisions, report. Includes `data_source` (CACHE or LIVE_SEC). |
| GET /reviews?status= | List review items with the metric, reasons and company. |
| POST /reviews/{id}/decision | APPROVE, EDIT or REJECT, with reviewer id and notes. |
| GET /audit | The audit log. |
| GET /report/{id} | Accuracy before and after review. |
| GET /config, PUT /config | Read and change thresholds. |
| GET /health | Service check. |

Start it with `uvicorn coaudit.api.main:app --port 8000`.

## 9. Proof

- Compared with the SEC's XBRL data: 58 of 58 comparable values match, across 8 companies (Apple, Microsoft, Nvidia, Walmart, Tesla, Costco, Amazon, Coca-Cola).
- On the first six companies, 36 values were auto-approved and none was wrong.
- Error injection (synthetic mistakes): 90% caught overall. Scale 100%, sign 100%, wrong row 86%, wrong year 72%.
- 202 automated tests pass.

## 10. The fault demo

`POST /runs` with `"fault_switch": true` multiplies one value (operating cash flow) by 1,000. The checks catch it, the run pauses, a reviewer corrects it, and the run resumes. Accuracy goes from 88% back to 100%. This is a simulation and is labelled simulated in the audit log.

Run it with `python -m coaudit.demo AAPL --offline`.

## 11. Limits we state openly

- The rules were tuned on 8 companies. An unusual filing may need a new rule.
- Values we derive ourselves are always flagged for a human look.
- The LLM part is covered by tests with a simulated model service. Accuracy with a real model depends on the key and the model.
- The error-injection numbers use synthetic mistakes. They are not real extraction accuracy.
- Not built: MCP tools and thresholds that adjust themselves.
- The dashboard connection to this API is done by the other half of the team.

## 12. How to run

```
pip install -r requirements.txt
python -m pytest -q
python -m coaudit.analysis AAPL --offline
python -m coaudit.groundtruth AAPL MSFT NVDA WMT TSLA COST
python -m coaudit.demo AAPL --offline
python -m coaudit.llm AAPL --offline
uvicorn coaudit.api.main:app --port 8000
```

The SEC requires a contact in the User-Agent. Set `SEC_USER_AGENT` in `.env`.
