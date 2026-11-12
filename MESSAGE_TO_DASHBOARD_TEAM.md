# Message for the dashboard team

You do not need to rewrite anything. Our package already has the real workflow, the audit log and the API. Your dashboard only has to call the API.

## Quick start

1. Extract `coaudit_final_v2.zip`.
2. Create a `.env` file from `.env.example` and set `SEC_USER_AGENT` to your own team name and contact address. Do not copy ours.
3. Install and start:

```
pip install -r requirements.txt
uvicorn coaudit.api.main:app --host 0.0.0.0 --port 8000
```

4. Point the dashboard at `http://localhost:8000`, or at `http://<our laptop IP>:8000` from another laptop on the same wifi.
5. Open `http://localhost:8000/docs` to try every endpoint in the browser.

For an offline demo, copy the `data/cache` folder (saved filings) and set `COAUDIT_OFFLINE=1`. No live SEC calls are needed then.

CORS is enabled, so a browser-based dashboard can call the API directly.

## The flow your screens follow

1. `POST /runs` with `{"ticker": "AAPL", "fault_switch": false}` starts a run. The response has `thread_id`, `status`, `metrics`, `validations`, `decisions` and `data_source` (`CACHE` or `LIVE_SEC`).
2. If `status` is `PAUSED`, call `GET /reviews?status=PENDING`. Each item has `id`, `metric_name`, `metric` (value, confidence, source text), `reasons`, `company` and `data_source`.
3. `POST /reviews/{id}/decision` with:

```
{"action": "APPROVE", "notes": "", "reviewer_id": "alice"}
{"action": "EDIT", "corrected_value": 123456789, "notes": "fixed", "reviewer_id": "alice"}
{"action": "REJECT", "notes": "bad value", "reviewer_id": "alice"}
```

4. When the last pending review is decided, the run resumes by itself. `status` becomes `COMPLETED`.
5. `GET /report/{thread_id}` returns the accuracy before and after review. `GET /audit` returns the append-only decision log.
6. `GET /config` and `PUT /config` read and change the thresholds (your slider).

`fault_switch: true` on `POST /runs` multiplies one value by 1,000 on purpose, to show the pause. Show a visible "Fault injected (simulation)" banner when it is on. Those audit rows carry `simulated: true`.

## Errors

- 404: the run or review id does not exist. The message says which one.
- 409: that review already has a decision.
- 502: the SEC could not be reached.
- 422: the request body has the wrong shape.

## If you want to call our code from Python instead

- `fetch_filing(ticker, client) -> FilingBundle` in `coaudit.sec.filings`.
- `analyze_filing(bundle, config=None, tables=None, llm=None) -> FilingAnalysis` in `coaudit.analysis.pipeline`. It takes a `FilingBundle`, not a ticker. `FilingAnalysis` has `bundle`, `metrics` (already scored), `validations` and `decisions`.
- Shapes are in `src/coaudit/contracts.py`. Call `model_dump(mode="json")` for plain dictionaries.
- Ground truth: `load_ground_truth(client, bundle)` then `compare_to_ground_truth(bundle, metrics, truth, flagged)` gives an accuracy report.
- `bundle.from_cache` is `True` for a saved copy and `False` for a live SEC download.

## Companies and packages

- Tested end to end: AAPL, MSFT, NVDA, WMT, TSLA, COST.
- Also tested for extraction and SEC comparison: AMZN, KO.
- Packages are in `requirements.txt`: pydantic, httpx, beautifulsoup4, lxml, python-dotenv, pytest, langgraph, langgraph-checkpoint-sqlite, fastapi, uvicorn. No `requests`.

## The LLM second reading

It is built and optional. It runs only if `LLM_API_KEY` is set in `.env` (with `LLM_PROVIDER` as gemini, openai or anthropic). Without a key the system runs on rules and checks, and nothing else changes for the dashboard. Its disagreements appear as a failed `second_reading` check in `validations` and in the `reasons` of a flagged item. It was tested with a simulated model service. Accuracy on a real model has not been measured yet.

## Documents in the package

- `README.md`: how to run everything.
- `HANDOFF.md`: what the backend does and its limits.
- `BACKEND_EXPLANATION.md`: the backend explained step by step.
- `SUBMISSION.md`: the submission write-up.
