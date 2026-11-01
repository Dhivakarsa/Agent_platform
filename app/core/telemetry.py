"""
OpenTelemetry instrumentation for the agentic system.

Provides a configured ``Tracer``, counters, and histograms that other modules
import to emit spans and metrics without caring about the OTel plumbing.

Usage::

    from app.core.telemetry import tracer, tool_execution_counter

    with tracer.start_as_current_span("my_operation"):
        tool_execution_counter.add(1, {"tool.name": "create_order"})
"""

from __future__ import annotations

from opentelemetry import metrics, trace
from opentelemetry.metrics import Counter, Histogram, Meter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)
from opentelemetry.trace import Tracer

# ---------------------------------------------------------------------------
# Resource (shared identity for traces + metrics)
# ---------------------------------------------------------------------------

_SERVICE_NAME = "agentic-system"

_resource = Resource.create(
    {
        "service.name": _SERVICE_NAME,
        "service.version": "0.1.0",
    }
)

# ---------------------------------------------------------------------------
# Tracing setup
# ---------------------------------------------------------------------------


def _init_tracer_provider() -> TracerProvider:
    """Create and register a ``TracerProvider`` with a console exporter."""
    provider = TracerProvider(resource=_resource)
    processor = BatchSpanProcessor(ConsoleSpanExporter())
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    return provider


_tracer_provider: TracerProvider = _init_tracer_provider()

tracer: Tracer = trace.get_tracer(
    instrumenting_module_name=_SERVICE_NAME,
    instrumenting_library_version="0.1.0",
)
"""Pre-configured tracer – use ``tracer.start_as_current_span(...)``."""


# ---------------------------------------------------------------------------
# Metrics setup
# ---------------------------------------------------------------------------


def _init_meter_provider() -> MeterProvider:
    """Create and register a ``MeterProvider`` with a console exporter."""
    reader = PeriodicExportingMetricReader(
        ConsoleMetricExporter(),
        export_interval_millis=60_000,
    )
    provider = MeterProvider(resource=_resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)
    return provider


_meter_provider: MeterProvider = _init_meter_provider()

_meter: Meter = metrics.get_meter(
    name=_SERVICE_NAME,
    version="0.1.0",
)

# --- Counters --------------------------------------------------------------

tool_execution_counter: Counter = _meter.create_counter(
    name="tool.executions",
    description="Total number of tool executions (partitioned by tool name and status).",
    unit="1",
)

# --- Histograms ------------------------------------------------------------

tool_latency_histogram: Histogram = _meter.create_histogram(
    name="tool.latency",
    description="Latency of individual tool executions in milliseconds.",
    unit="ms",
)

retrieval_latency_histogram: Histogram = _meter.create_histogram(
    name="retrieval.latency",
    description="Latency of vector-store retrieval queries in milliseconds.",
    unit="ms",
)


# ---------------------------------------------------------------------------
# Shutdown helper
# ---------------------------------------------------------------------------


def shutdown_telemetry() -> None:
    """Flush and shut down all telemetry providers.  Call on app teardown."""
    _tracer_provider.shutdown()
    _meter_provider.shutdown()
