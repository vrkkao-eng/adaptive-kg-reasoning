"""Optional FastAPI adapter. Bind to loopback; this is not an authenticated API."""
import argparse
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sqlite3
import uuid

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Annotated, Literal

from . import __version__
from .jobs import JobService, spec_request
from .job_registry import ServiceError
from .process_resume import read_json


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    fixture: Literal["demo"] = "demo"
    width: int = Field(default=30, ge=5, le=120)
    slide: int = Field(default=10, ge=1, le=60)
    threshold: float = Field(default=450.0, ge=-100000, le=100000, allow_inf_nan=False)
    flush: bool = True
    workload: Literal["none", "sparse", "dense", "bursty"] = "dense"
    node: Literal["edge", "fog", "cloud"] = "fog"

    @model_validator(mode="after")
    def check_slide(self):
        if self.slide > self.width:
            raise ValueError("Slide exceeds width")
        return self


class BoundedRequest:
    """ASGI body bound before parsing, including chunked/incorrect length input."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        async def correlated_send(message):
            if message["type"] == "http.response.start":
                message["headers"].append((b"x-request-id", request_id.encode()))
            await send(message)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > 2048:
                return await JSONResponse({"error": "request_too_large", "request_id": request_id}, status_code=413)(scope, receive, correlated_send)
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        if body:
            try:
                read_json(bytes(body))  # Reject duplicate keys/non-finite JSON before Pydantic.
            except (ValueError, UnicodeError):
                return await JSONResponse({"error": "invalid_request", "request_id": request_id}, status_code=422)(scope, receive, correlated_send)
        sent = False
        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        await self.app(scope, replay, correlated_send)


def create_app(root=None, **service_options):
    root = Path(root or os.environ.get("KG_JOB_ROOT", "results/runs/service"))

    @asynccontextmanager
    async def lifespan(app):
        service = JobService(root, **service_options)
        app.state.service = service
        try:
            yield
        finally:
            service.close()

    app = FastAPI(title="Fixed-trace KG Job Service", version=__version__, lifespan=lifespan)
    app.add_middleware(BoundedRequest)

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return JSONResponse({"error": exc.code, "request_id": request.state.request_id}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({"error": "invalid_request", "request_id": request.state.request_id}, status_code=422)

    @app.exception_handler(sqlite3.DatabaseError)
    async def storage_error(request, exc):
        return JSONResponse({"error": "storage_unavailable", "request_id": request.state.request_id}, status_code=503)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        return JSONResponse({"error": "internal_unavailable", "request_id": request_id}, status_code=503,
                            headers={"x-request-id": request_id})

    @app.get("/healthz")
    def health():
        return {"status": "alive"}

    @app.get("/readyz")
    def ready(request: Request):
        return request.app.state.service.ready()

    @app.post("/jobs")
    def submit(spec: JobRequest, request: Request, idempotency_key: Annotated[str, Header(min_length=1, max_length=64)]):
        result, created = request.app.state.service.submit(spec.model_dump(), idempotency_key, request.state.request_id)
        return JSONResponse(result, status_code=201 if created else 200)

    @app.get("/jobs/{job_id}")
    def status(job_id: str, request: Request):
        return request.app.state.service.status(job_id)

    @app.post("/jobs/{job_id}/resume")
    def resume(job_id: str, request: Request):
        return request.app.state.service.resume(job_id, request.state.request_id)

    @app.get("/jobs/{job_id}/receipts")
    def receipts(job_id: str, request: Request, after: Annotated[int, Query(ge=-1)] = -1,
                 limit: Annotated[int, Query(ge=1, le=100)] = 100):
        return request.app.state.service.receipts(job_id, after, limit)

    @app.get("/jobs/{job_id}/events")
    def events(job_id: str, request: Request, after: Annotated[int, Query(ge=0)] = 0,
               limit: Annotated[int, Query(ge=1, le=100)] = 100):
        service = request.app.state.service
        service.registry.get(job_id)
        return {"items": service.registry.events(job_id, after, limit)}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics(request: Request):
        return request.app.state.service.metrics()

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("results/runs/service"))
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(args.root), host="127.0.0.1", port=args.port, workers=1, access_log=False)


if __name__ == "__main__":
    main()
