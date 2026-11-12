# Co Audit: Team Split and How the Parts Connect

Four people, two parts, two people per part. All code is written during the 24-hour event.

## The two parts

**Part 1: Data and Intelligence.** Everything that turns a filing into checked, scored numbers.
**Part 2: Workflow and Review.** Everything that runs the agent, pauses for a human, and records decisions.

| Person | Part | Role | Ideas |
|---|---|---|---|
| Person 1 | Part 1 | Data and evaluation | 1 SEC fetcher, 2 (find and parse the tables), 8 ground-truth report, 10 MCP tools, 14 calibration chart, 18 error injection, 25 source fingerprint, 28 cached demo mode |
| Person 2 | Part 1 | Extraction and validation | 2 (read values with the AI), 3 confidence score, 4 three checks, 11 variance explanations, 13 earned confidence, 15 tie-outs, 16 unit and sign guard, 24 grounding check, 30 (the corrupting function) |
| Person 3 | Part 2 | Workflow and backend | 5 routing and pause, 6 checkpointed resume, 9 append-only audit log, 12 adaptive thresholds, 19 crash-and-resume proof, 22 approval receipts, 23 spot-checks, the API |
| Person 4 | Part 2 | Reviewer dashboard | 7 dashboard, 17 materiality queue, 21 click-to-source, 26 two-person approval, 27 workpaper export, 29 scoreboard, 30 (the toggle), 31 escalation curve |

Idea 20 was removed from the list. The append-only rule lives in idea 9.

## Priority tiers (same for everyone)

| Tier | Ideas |
|---|---|
| Must | 1 to 9 and 28 |
| Should | 13, 14, 18, 19, 21, 22, 23, 24, 25, 30, 31 |
| Could | 10, 11, 12, 15, 16, 17, 26, 27, 29 |

Finish every Must idea before starting a Should idea. If time is tight, drop from Could first.

## How the parts connect

The two parts never edit each other's code. They meet at one shared file of agreed data shapes (the contract) and a short list of functions.

### The contract (agreed in the first 2 hours, then changed only by both parts together)

| Shape | What it holds |
|---|---|
| FilingBundle | ticker, CIK, accession number, URL, download time, document hash, path to the saved HTML, list of tables |
| ExtractedMetric | company, metric name, value in USD, period end date, fiscal label, unit scale, raw text, source snippet, table and row location, extractor used (parser or LLM), confidence, confidence signals |
| ValidationResult | check name, status (PASSED, FAILED or NOT_APPLICABLE), reason, metrics involved |
| Config | confidence threshold, year-on-year threshold, equation tolerance, spot-check rate |
| ReviewItem | id, thread id, metric, reason, status (PENDING or DONE), created time |
| AuditEntry | audit id, thread id, company, metric, original value, final value, decision, reviewer notes, reviewer id, simulated flag, timestamp |

### The functions Part 1 gives to Part 2

| Function | Returns |
|---|---|
| fetch_filing(ticker) | FilingBundle |
| extract_metrics(bundle, fault_switch) | list of ExtractedMetric |
| run_validations(metrics, config) | list of ValidationResult |
| score_confidence(metrics, validations, config) | list of ExtractedMetric with confidence set |
| load_ground_truth(ticker, period) | official XBRL values |
| compare_to_ground_truth(final_metrics, truth) | accuracy report, with a label per value saying whether it was a real error |

Part 2 calls these as the steps of the LangGraph workflow. Part 1 never touches the workflow, the database tables of Part 2, or the dashboard.

### What Part 2 gives back
Only decisions: the final value for each metric (approved, corrected or rejected) and the audit entries. Part 1 uses the final values for the ground-truth comparison.

## How to work in parallel without waiting

1. **Hours 0 to 2:** write the contract file together. Person 1 downloads and caches 5 real filings and their XBRL data.
2. **By hour 3:** Part 1 delivers a sample file with real-looking ExtractedMetric and ValidationResult data for one company. Part 2 builds against that sample, so the workflow and dashboard need no real extraction yet.
3. **Hours 3 to 9:** Part 1 makes the real functions return the same shapes as the sample. Part 2 builds the workflow, audit log and dashboard.
4. **Hours 9 to 12: integration.** Swap the sample data for the real functions, one function at a time. Run one company end to end after each swap.
5. **Hours 12 to 18:** run 3 companies end to end, fix unit and period problems, and start the Should tier.
6. **Hours 18 to 21:** finish the Should tier, then freeze features.
7. **Hours 21 to 24:** test, rehearse the demo, final deploy.

## Working rules

- **Folders:** each part has its own folder. Only the contract file and the config file are shared.
- **Branches:** one branch per person. Merge into the main branch at every integration point, and run the end-to-end test after each merge.
- **One config file** holds all thresholds, so the dashboard sliders and the code agree.
- **One database file path**, set in the config.
- **Secrets** go in an environment file that is never committed: the LLM key and the SEC User-Agent.
- **Fake-first:** if something is not ready, use the sample data and say so on screen. Nothing simulated is shown as real.
- **Environment:** compiled Python libraries may be blocked on Windows by Smart App Control. Test installs in the first 30 minutes on every laptop. Use WSL or a cloud workspace if blocked.

## Demo-day hand-over

Person 4 owns the demo script. Each person prepares a 30-second explanation of their own part. Every number shown must come from a real run, and the fault switch and error injection are always labelled as simulations.
