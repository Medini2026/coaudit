# Co Audit: submission document

MineArena 1.0, PS-01: Human-in-the-Loop Financial Filing Auditor Agent.

## 1. Project description

Co Audit downloads a company's latest Form 10-K from SEC EDGAR, reads 8 key financial figures from the statement tables, checks them with accounting rules, scores its confidence in each one, and pauses for a human review when a value is doubtful or fails a check. The reviewer approves, corrects or rejects each flagged value. The workflow then resumes from its saved checkpoint, compares the final values with the SEC's XBRL Company Facts data, and reports accuracy. Every human decision is stored in an append-only audit log.

## 2. How it meets the PS

| PS requirement | Where it is |
|---|---|
| 3.1 Real 10-Ks for 3 to 5 companies | `coaudit.sec` (tested on 8 companies) |
| 3.2 8+ metrics with period, unit and source evidence | `coaudit.parsing`, `coaudit.extraction` |
| 3.3 Confidence scores, configurable thresholds | `coaudit.scoring`, `config.json` |
| 3.4 Accounting equation, segment sum, year-on-year, pass or fail | `coaudit.validation` (plus three cross-checks) |
| 3.5 Pause for a human, approve, edit or reject | `coaudit.workflow`, `coaudit.service`, API |
| 3.6 Checkpointing and resume without repeating work | LangGraph with a SQLite checkpointer, step ledger |
| 3.7 Reviewer dashboard | Dashboard built by the other team, using our API |
| 3.8 XBRL ground truth, accuracy report, counts, audit log | `coaudit.groundtruth`, `coaudit.store` |

## 3. Technology stack

Python, LangGraph (interrupt, Command, SQLite checkpointer), FastAPI, SQLite, BeautifulSoup and lxml for HTML tables, Pydantic for data shapes, httpx for HTTP, pytest for tests.

## 4. Results (real SEC filings)

- 58 of 58 comparable values match the SEC's XBRL data across 8 companies (Apple, Microsoft, Nvidia, Walmart, Tesla, Costco, Amazon, Coca-Cola).
- On six companies at a 50 percent year-on-year limit: 36 values auto-approved with none wrong, 11 flagged (9 real year-on-year changes and 2 values we derived ourselves).
- Error-injection test (synthetic mistakes inserted on purpose): thousands-vs-millions 100 percent caught, sign flips 100 percent, wrong year's value 72 percent, wrong row 86 percent, overall 90 percent.
- 178 automated tests pass.

## 5. External resources, licences and acknowledgements

| Resource | Use | Licence or terms |
|---|---|---|
| SEC EDGAR filings and XBRL Company Facts API | Source data and answer key | Public data. We follow the SEC's fair access policy: declared User-Agent, request rate limit, local cache |
| pydantic | Data shapes | MIT |
| httpx | HTTP client | BSD-3-Clause |
| beautifulsoup4 | HTML parsing | MIT |
| lxml | HTML parsing | BSD-3-Clause |
| python-dotenv | Reads the `.env` file | BSD-3-Clause |
| langgraph and langgraph-checkpoint-sqlite | Pause and resume workflow | MIT |
| fastapi | API | MIT |
| uvicorn | API server | BSD-3-Clause |
| pytest | Tests | MIT |

Licences are those published by each project. Check them again if the organisers require a formal list.

AI assistance: the code and documents in this repository were developed with the help of an AI coding assistant (Claude Code). The team ran, tested and reviewed the results on real filings.

All code was written during the 24-hour event. No third-party datasets other than SEC filings were used. No personal data is collected. The SEC contact address and any API keys are kept in a local `.env` file that is not committed.

## 6. Known limitations

- Extraction is rule-based and tuned on 8 companies. A company with unusual statement wording may need a new rule.
- Values we derive ourselves (for example Walmart's gross profit) are always flagged for a human look.
- An LLM second reading of each figure is not built.
- The audit log is append-only for our application and database connections. Someone with direct access to the database file could still remove the protective rules.
- The error-injection numbers use synthetic mistakes and are not real extraction accuracy.

## 7. How to run

See `README.md`. In short:

```
pip install -r requirements.txt
python -m pytest -q
python -m coaudit.analysis AAPL --offline
python -m coaudit.demo AAPL --offline
uvicorn coaudit.api.main:app --port 8000
```

## 8. Five-minute demo script

1. Say the problem in one sentence.
2. `python -m coaudit.analysis AAPL --offline`: show scores and the flagged value with its reason.
3. `python -m coaudit.groundtruth AAPL MSFT NVDA WMT TSLA COST`: every number matches the SEC.
4. `python -m coaudit.demo AAPL --offline`: fault, pause, correction, resume, accuracy back to 100 percent. Say clearly that the fault is a simulation.
5. `python -m coaudit.evaluation AAPL MSFT NVDA WMT TSLA COST --offline`: the error-injection results and the escalation curve.
6. Show the dashboard connected to the API, if ready.

## 9. Final checklist before submitting

- [ ] `.env` is not in the repository and no key appears in any file.
- [ ] `python -m pytest -q` passes on the laptop used for the demo.
- [ ] `config.json` is set to the values you want to show first.
- [ ] Saved filings exist in `data/cache` so the demo works offline.
- [ ] The repository is accessible to the organisers.
- [ ] The deck and this document are attached.
- [ ] Known limitations are stated honestly in the pitch.
