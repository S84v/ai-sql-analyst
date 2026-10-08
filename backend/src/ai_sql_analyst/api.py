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
