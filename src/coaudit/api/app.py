from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from coaudit.contracts import Config, ReviewStatus
from coaudit.sec.client import SecClientError
from coaudit.sec.filings import FilingNotFoundError
from coaudit.service import AuditService, RunNotFoundError
from coaudit.store.database import ReviewAlreadyDecidedError, ReviewNotFoundError


class RunRequest(BaseModel):
    ticker: str = Field(pattern=r"^[A-Za-z][A-Za-z.\-]{0,9}$")
    fault_switch: bool = False


class DecisionRequest(BaseModel):
    action: str
    corrected_value: Optional[float] = None
    notes: str = ""
    reviewer_id: str = Field(min_length=1)


def error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": message})


def create_app(service: AuditService) -> FastAPI:
    app = FastAPI(title="Co Audit backend")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RunNotFoundError)
    def run_not_found(request: Request, error: RunNotFoundError) -> JSONResponse:
        return error_response(404, f"Run {error} was not found")

    @app.exception_handler(ReviewNotFoundError)
    def review_not_found(request: Request, error: ReviewNotFoundError) -> JSONResponse:
        return error_response(404, f"Review {error} was not found")

    @app.exception_handler(ReviewAlreadyDecidedError)
    def review_decided(request: Request, error: ReviewAlreadyDecidedError) -> JSONResponse:
        return error_response(409, f"Review {error} already has a decision")

    @app.exception_handler(FilingNotFoundError)
    def filing_not_found(request: Request, error: FilingNotFoundError) -> JSONResponse:
        return error_response(404, str(error))

    @app.exception_handler(SecClientError)
    def sec_failure(request: Request, error: SecClientError) -> JSONResponse:
        return error_response(502, str(error))

    @app.exception_handler(ValueError)
    def bad_value(request: Request, error: ValueError) -> JSONResponse:
        return error_response(422, str(error))

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/runs")
    def start_run(request: RunRequest) -> dict:
        thread_id = service.start_run(request.ticker, request.fault_switch)
        return service.get_run(thread_id)

    @app.get("/runs")
    def list_runs() -> list:
        return service.list_runs()

    @app.get("/runs/{thread_id}")
    def get_run(thread_id: str) -> dict:
        return service.get_run(thread_id)

    @app.get("/reviews")
    def list_reviews(status: Optional[ReviewStatus] = None, thread_id: Optional[str] = None) -> list:
        return service.list_reviews_detailed(status, thread_id)

    @app.post("/reviews/{review_id}/decision")
    def decide(review_id: str, request: DecisionRequest) -> dict:
        return service.decide(
            review_id,
            request.action,
            request.corrected_value,
            request.notes,
            request.reviewer_id,
        )

    @app.get("/audit")
    def list_audit(thread_id: Optional[str] = None) -> list:
        return [entry.model_dump(mode="json") for entry in service.list_audit(thread_id)]

    @app.get("/report/{thread_id}")
    def get_report(thread_id: str) -> dict:
        return service.get_report(thread_id)

    @app.get("/config")
    def get_config() -> dict:
        return service.get_config().model_dump()

    @app.put("/config")
    def put_config(config: Config) -> dict:
        return service.set_config(config).model_dump()

    return app
