# Co Audit: final status

## What it does

The program downloads a company's latest 10-K from the SEC, finds the statement tables, reads 8 key figures, checks them, scores its confidence, and pauses for a human when a value is doubtful. Decisions go into an append-only audit log, and the final values are compared with the SEC's official data.

## Built and tested

| Part | Result |
|---|---|
| SEC download with cache, rate limit and offline mode | Works on 8 real companies |
| Table parser and 8-figure extraction | Handles different layouts and column orders |
| Ground truth against SEC XBRL | 58 of 58 comparable values match (8 companies) |
| Six accounting checks | Equation, segment sum, year-on-year, net income tie-out, gross profit and operating income identities |
| Confidence score and flagging | Evidence-based, threshold in `config.json` |
| Pause and resume workflow | LangGraph with a SQLite checkpoint, no step repeated |
| Append-only audit log | Blocked from edits and deletes by code and by the database |
| API | Start runs, review items, decisions, audit log, report, config |
| One-command demo | Fault, pause, correction, resume, accuracy back to 100 percent |
| Error-injection test | 90 percent of inserted mistakes caught |
| Automated tests | 178 pass |

## Results on six real companies (year-on-year limit 50 percent)

- 36 values auto-approved, none wrong.
- 11 flagged: 9 real year-on-year changes and 2 values we derived ourselves.

## Not built

- LLM second reading and the grounding check.
- MCP tools, multi-period variance explanations, adaptive thresholds.
- Click-to-source highlighting.

## In progress

- Connecting the other team's dashboard to our API.

## Honest limits

- Rule-based extraction tuned on 8 companies.
- Derived values are always flagged.
- Error-injection results are synthetic.
