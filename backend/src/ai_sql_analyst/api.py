"""FastAPI HTTP boundary for the agent, streamed over SSE (ADR-006).

Thin adapter: it validates the request, obtains the app-lifetime compiled agent,
translates its LangGraph event stream via ``streaming.translate_agent_events``,
and emits application events using FastAPI's first-party SSE support. It owns no
database connections and no agent internals.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langchain_core.messages import HumanMessage
from langgraph.errors import GraphRecursionError
from opentelemetry import trace
from pydantic import BaseModel, Field, field_validator

from ai_sql_analyst import observability
from ai_sql_analyst.agent import build_agent
from ai_sql_analyst.model import build_model
from ai_sql_analyst.streaming import translate_agent_events

logger = logging.getLogger(__name__)

# Fixed, user-facing message. Internal exception details are logged server-side,
# never streamed (ADR-006).
_STREAM_ERROR_MESSAGE = "The analysis could not be completed."

# Explicit production execution backstop for the LangGraph loop, mirroring the
# evaluation budget (run_evals.py) and the current LangGraph default
# (DEFAULT_RECURSION_LIMIT = 25). Passed here, at the HTTP boundary, so the
# provider-neutral agent (ADR-004) keeps no assumption about a specific value.
_RECURSION_LIMIT = 25


class QueryRequest(BaseModel):
    """A single, independent natural-language analytical question."""

    question: str = Field(min_length=1)

    @field_validator("question")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value


# Production browser -> Cloud Run access control. Exact origins only, supplied
# by the environment at runtime; never committed and never a wildcard.
_CORS_ALLOWED_ORIGINS_ENV = "CORS_ALLOWED_ORIGINS"


def build_default_agent() -> Any:
    """Build the DeepSeek-backed graph. Called once, at application startup."""
    return build_agent(build_model())


def _parse_allowed_origins(raw: str | None) -> list[str]:
    """Parse a comma-separated list of exact CORS origins.

    Missing, empty, or whitespace-only configuration yields no origins, so the
    middleware is not installed and current local behavior is preserved. Entries
    are trimmed and empty ones dropped.

    A wildcard (``*``) anywhere in the configuration is invalid: CORS must be
    exact-origin, so a wildcard raises ``ValueError`` instead of being silently
    filtered. This also rejects mixed values such as ``https://host,*``.
    """
    if not raw:
        return []
    if "*" in raw:
        raise ValueError(
            f"{_CORS_ALLOWED_ORIGINS_ENV} must list exact origins and must not "
            "contain '*'"
        )
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


# ---------------------------------------------------------------------------
# TEMPORARY client-IP diagnostic (remove after verifying the deployed path).
#
# One logged record per application process, only for a specifically marked,
# harmless CORS preflight. It records exactly: the application-visible client
# address, every raw X-Forwarded-For value, and the method/path. It never logs
# bodies, headers, or configuration, never changes the request or response, and
# never reaches the agent, model, SQL, or database. Delete this whole block and
# the single `add_middleware` call in `create_app` to remove it completely.
# ---------------------------------------------------------------------------

_TEMP_DIAGNOSTIC_PREFIX = "TEMP_CLIENT_IP_DIAGNOSTIC"
_TEMP_DIAGNOSTIC_ORIGIN = "https://olistiq-prod-2026.web.app"


def _temp_diagnostic_header_values(scope: dict[str, Any], name: bytes) -> list[str]:
    """All values for one header, preserving duplicate header lines."""
    return [
        value.decode("latin1")
        for key, value in scope.get("headers", [])
        if key == name
    ]


def _is_temp_client_ip_diagnostic(scope: dict[str, Any]) -> bool:
    """True only for the marked, harmless preflight this diagnostic targets."""
    if scope.get("method") != "OPTIONS" or scope.get("path") != "/query":
        return False
    query = parse_qs(scope.get("query_string", b"").decode("latin1"))
    if query.get("client_ip_diag") != ["1"]:
        return False
    if _TEMP_DIAGNOSTIC_ORIGIN not in _temp_diagnostic_header_values(scope, b"origin"):
        return False
    request_methods = _temp_diagnostic_header_values(
        scope, b"access-control-request-method"
    )
    return any(method.upper() == "POST" for method in request_methods)


class _TempClientIpDiagnosticMiddleware:
    """TEMPORARY pure-ASGI pass-through that logs the marked preflight once.

    Registered outside CORS so it observes the request before CORS answers the
    preflight. As a pass-through it does not alter the request or the streaming
    response, so normal behavior is unchanged.
    """

    def __init__(self, app: Any) -> None:
        self.app = app
        # One app instance per process in production, so this logs once/process.
        self._logged = False

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if (
            scope.get("type") == "http"
            and not self._logged
            and _is_temp_client_ip_diagnostic(scope)
        ):
            self._logged = True
            client = scope.get("client")
            # warning, not info: Uvicorn's default logging config does not emit
            # INFO records from application loggers, so an info record would be
            # dropped in production. Remove with the rest of the diagnostic.
            logger.warning(
                "%s client_host=%r x_forwarded_for=%s method=%s path=%s",
                _TEMP_DIAGNOSTIC_PREFIX,
                client[0] if client else None,
                _temp_diagnostic_header_values(scope, b"x-forwarded-for"),
                scope.get("method"),
                scope.get("path"),
            )
        await self.app(scope, receive, send)


def create_app(
    agent_factory: Callable[[], Any] | None = None,
    *,
    tracer_provider: Any | None = None,
    meter_provider: Any | None = None,
) -> FastAPI:
    """Create the FastAPI app.

    ``agent_factory`` is injectable so tests can supply a deterministic fake
    agent without a DeepSeek key or a database. ``tracer_provider`` and
    ``meter_provider`` let tests inject in-memory OpenTelemetry providers
    (ADR-010); production configures them from the environment.
    """
    factory = agent_factory or build_default_agent

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Build the model and compile the graph exactly once; reuse across requests.
        app.state.agent = factory()
        try:
            yield
        finally:
            observability.shutdown()

    app = FastAPI(lifespan=lifespan)
    # CORS is only needed when running cross-origin (browser -> Cloud Run in
    # production, ADR-011). Without configured origins the middleware is absent
    # and the same-origin/dev-proxy behavior is unchanged.
    allowed_origins = _parse_allowed_origins(os.environ.get(_CORS_ALLOWED_ORIGINS_ENV))
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=False,
            allow_methods=["POST"],
            allow_headers=["Content-Type"],
        )
    # TEMPORARY diagnostic: added after CORS so it wraps it (middleware added
    # later is outermost) and observes the marked preflight before CORS answers
    # it. Remove alongside the diagnostic block above.
    app.add_middleware(_TempClientIpDiagnosticMiddleware)
    # Instrument here, not in the lifespan: the ASGI middleware stack is built on
    # first dispatch, so the HTTP server-span boundary must exist before that.
    observability.setup(
        app, tracer_provider=tracer_provider, meter_provider=meter_provider
    )

    @app.post("/query", response_class=EventSourceResponse)
    async def query(request: Request, req: QueryRequest) -> AsyncIterator[ServerSentEvent]:
        agent = request.app.state.agent
        span = trace.get_current_span()
        outcome = observability.RunOutcome()
        try:
            stream = agent.astream(
                {"messages": [HumanMessage(content=req.question)]},
                config={"recursion_limit": _RECURSION_LIMIT},
                stream_mode=["messages", "updates"],
                version="v2",
            )
            events = observability.watch_run(stream, outcome)
            async for event in translate_agent_events(events):
                if event["type"] == "done":
                    outcome.value = observability.OUTCOME_SUCCESS
                elif event["type"] == "error":
                    outcome.value = observability.OUTCOME_NO_ANSWER
                yield ServerSentEvent(event=event["type"], data=event)
        except GraphRecursionError:
            # The production execution backstop (ADR-004/ADR-010). Classify the
            # outcome for telemetry only; the client still receives the same
            # generic error event as any other unexpected failure.
            outcome.value = observability.OUTCOME_RECURSION_LIMIT
            logger.exception("agent run hit the recursion limit")
            error = {"type": "error", "message": _STREAM_ERROR_MESSAGE}
            yield ServerSentEvent(event="error", data=error)
        except Exception:
            # asyncio.CancelledError is a BaseException and deliberately not
            # caught: client disconnects terminate the stream naturally.
            outcome.value = observability.OUTCOME_ERROR
            logger.exception("agent run failed")
            error = {"type": "error", "message": _STREAM_ERROR_MESSAGE}
            yield ServerSentEvent(event="error", data=error)
        finally:
            observability.record_outcome(span, outcome)

    return app


app = create_app()
