"""OpenTelemetry production boundary (ADR-010).

Single, narrow home for process-level instrumentation: tracer/meter provider
setup, the official FastAPI and LangChain/LangGraph instrumentations, opt-in
export, the request/run outcome tap, and provider shutdown.

Design constraints:

* **No external export by default.** Providers are only created (and the
  LangChain instrumentor only installed) when an exporter is explicitly
  configured, so an unconfigured process pays essentially nothing.
* **Metadata only.** ``OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`` is
  pinned to ``NO_CONTENT`` before instrumentation, and this module never records
  questions, prompts, SQL, parameters, rows, answers, or credentials.
* **Idempotent.** Providers and the LangChain instrumentor are process-global,
  so setup is guarded by module state; FastAPI instrumentation is guarded per
  app. Tests may construct many apps.
* **No sanitizing/exporter framework.** Privacy relies on the explicit
  content-capture setting and on application code simply never recording
  sensitive values.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.genai.langchain import LangChainInstrumentor

from ai_sql_analyst.agent import TIMEOUT_STOP_NODE

logger = logging.getLogger(__name__)

SERVICE_NAME = "olistiq"

# Standard OpenTelemetry / GenAI environment variables. Only these are honored;
# no bespoke configuration surface beyond the console toggle.
OTEL_EXPORTER_OTLP_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"
OTEL_SERVICE_NAME_ENV = "OTEL_SERVICE_NAME"
GENAI_CAPTURE_CONTENT_ENV = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"
GENAI_NO_CONTENT = "NO_CONTENT"
OLISTIQ_OTEL_CONSOLE_ENV = "OLISTIQ_OTEL_CONSOLE"

# Request/run outcome values recorded on the HTTP server span.
OUTCOME_SUCCESS = "success"
OUTCOME_NO_ANSWER = "no_answer"
OUTCOME_TIMEOUT_STOP = "timeout_stop"
OUTCOME_RECURSION_LIMIT = "recursion_limit"
OUTCOME_ERROR = "error"

_lock = threading.Lock()
_tracer_provider: Any | None = None
_meter_provider: Any | None = None
_owns_providers = False
_langchain_instrumented = False
_shutdown_done = False


def configure(
    *,
    tracer_provider: Any | None = None,
    meter_provider: Any | None = None,
) -> None:
    """Establish providers once and install the LangChain instrumentation once.

    Providers come from the injected arguments (tests) or, when none are given,
    from the environment (OTLP endpoint or console toggle). With no exporter
    configured, no provider is created and the LangChain instrumentor is left
    uninstalled, keeping the default path a no-op.
    """
    # Enforce privacy before any instrumentation reads the setting. This is an
    # unconditional assignment, not a default: an externally pre-set value (for
    # example SPAN_ONLY) must never weaken the ADR-010 contract that telemetry
    # captures no prompts, completions, reasoning, tool arguments/results, or DB
    # content.
    os.environ[GENAI_CAPTURE_CONTENT_ENV] = GENAI_NO_CONTENT

    global _tracer_provider, _meter_provider, _owns_providers, _langchain_instrumented
    with _lock:
        if _tracer_provider is None and _meter_provider is None:
            if tracer_provider is not None or meter_provider is not None:
                _tracer_provider = tracer_provider
                _meter_provider = meter_provider
            else:
                _tracer_provider, _meter_provider, _owns_providers = _build_from_env()
            if _tracer_provider is not None:
                trace.set_tracer_provider(_tracer_provider)
            if _meter_provider is not None:
                metrics.set_meter_provider(_meter_provider)

        if not _langchain_instrumented and (
            _tracer_provider is not None or _meter_provider is not None
        ):
            # The upstream instrumentor declares the `langchain` meta-package as
            # its target, but it only imports/wraps `langchain_core` and
            # `langgraph`, both of which this project installs. Skipping the
            # distribution check enables it without adding the meta-package.
            LangChainInstrumentor().instrument(
                tracer_provider=_tracer_provider,
                meter_provider=_meter_provider,
                skip_dep_check=True,
            )
            _langchain_instrumented = True


def setup(
    app: Any,
    *,
    tracer_provider: Any | None = None,
    meter_provider: Any | None = None,
) -> None:
    """Configure providers and instrument ``app`` for HTTP server spans.

    Called once per app at construction time (the ASGI middleware stack is built
    on first dispatch, so this must happen before the app serves requests).
    Idempotent per app: ``FastAPIInstrumentor`` guards each app instance.
    """
    configure(tracer_provider=tracer_provider, meter_provider=meter_provider)
    effective_tracer = tracer_provider if tracer_provider is not None else _tracer_provider
    effective_meter = meter_provider if meter_provider is not None else _meter_provider
    if effective_tracer is None and effective_meter is None:
        # No exporter configured and no injected provider: stay fully inert
        # (no HTTP server spans) exactly as the ADR specifies.
        return
    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=effective_tracer, meter_provider=effective_meter
    )


def shutdown() -> None:
    """Flush and shut down providers this module owns (no-op otherwise)."""
    global _shutdown_done
    with _lock:
        if _shutdown_done or not _owns_providers:
            return
        _shutdown_done = True
    for provider in (_tracer_provider, _meter_provider):
        if provider is None:
            continue
        try:
            provider.shutdown()
        except Exception:  # shutdown must never break app teardown
            logger.warning("OpenTelemetry provider shutdown failed", exc_info=True)


def _build_from_env() -> tuple[Any | None, Any | None, bool]:
    """Build providers from the environment, or ``(None, None, False)`` if disabled."""
    endpoint = os.environ.get(OTEL_EXPORTER_OTLP_ENDPOINT_ENV)
    console = os.environ.get(OLISTIQ_OTEL_CONSOLE_ENV) == "1"
    if not endpoint and not console:
        return None, None, False

    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    resource = Resource.create(
        {"service.name": os.environ.get(OTEL_SERVICE_NAME_ENV, SERVICE_NAME)}
    )
    tracer_provider = TracerProvider(resource=resource)

    if console:
        # Console exporters go to stderr: MCP owns stdout in this repo.
        from opentelemetry.sdk.metrics.export import ConsoleMetricExporter
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

        tracer_provider.add_span_processor(
            SimpleSpanProcessor(ConsoleSpanExporter(out=sys.stderr))
        )
        metric_exporter = ConsoleMetricExporter(out=sys.stderr)
    else:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        metric_exporter = OTLPMetricExporter()

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[PeriodicExportingMetricReader(metric_exporter)],
    )
    return tracer_provider, meter_provider, True


@dataclass
class RunOutcome:
    """The classified outcome of one ``/query`` run.

    ``timeout_stop`` is tracked separately because it occurs *before* the
    translator emits the final ``done`` event and must take precedence over it.
    """

    value: str = OUTCOME_ERROR
    timeout_stop: bool = False

    def resolved(self) -> str:
        return OUTCOME_TIMEOUT_STOP if self.timeout_stop else self.value


async def watch_run(
    events: AsyncIterator[Mapping[str, Any]],
    outcome: RunOutcome,
) -> AsyncIterator[Mapping[str, Any]]:
    """Pass the raw graph event stream through, flagging timeout-stop termination.

    Detecting the terminal node keeps the classification independent of answer
    text while leaving the stream (and therefore the SSE protocol) unchanged.
    """
    async for event in events:
        if isinstance(event, Mapping) and event.get("type") == "updates":
            data = event.get("data")
            if isinstance(data, Mapping) and TIMEOUT_STOP_NODE in data:
                outcome.timeout_stop = True
        yield event


def record_outcome(span: Any, outcome: RunOutcome) -> None:
    """Attach the resolved run outcome to the current server span."""
    span.set_attribute("olistiq.run.outcome", outcome.resolved())
