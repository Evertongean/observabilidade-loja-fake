import time
import random
import logging
import threading
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from opentelemetry import metrics, trace
from opentelemetry.trace import Status, StatusCode

LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s "
    "trace_id=%(otelTraceID)s span_id=%(otelSpanID)s %(message)s"
)


class OpenTelemetryLogDefaults(logging.Filter):
    def filter(self, record):
        if not hasattr(record, "otelTraceID"):
            record.otelTraceID = "0" * 32
        if not hasattr(record, "otelSpanID"):
            record.otelSpanID = "0" * 16
        return True


def configure_logging():
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    otel_filter = OpenTelemetryLogDefaults()

    for handler in root_logger.handlers:
        if not getattr(handler, "_loja_fake_otel_defaults", False):
            handler.addFilter(otel_filter)
            handler._loja_fake_otel_defaults = True
        if isinstance(handler, logging.StreamHandler):
            handler.setFormatter(logging.Formatter(LOG_FORMAT))

    if not any(isinstance(handler, logging.StreamHandler) for handler in root_logger.handlers):
        handler = logging.StreamHandler()
        handler.addFilter(otel_filter)
        handler._loja_fake_otel_defaults = True
        handler._loja_fake_console = True
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root_logger.addHandler(handler)


configure_logging()
logger = logging.getLogger("payment-service")

app = FastAPI()
tracer = trace.get_tracer("payment-service")
meter = metrics.get_meter("payment-service")

gateway_pool = threading.Semaphore(2)

gateway_wait = meter.create_histogram(
    "payment_gateway_wait_seconds",
    unit="s",
    description="Time spent waiting for a payment gateway slot.",
)
gateway_processing = meter.create_histogram(
    "payment_gateway_processing_seconds",
    unit="s",
    description="Time spent processing a payment after acquiring a gateway slot.",
)
gateway_declines = meter.create_counter(
    "payment_gateway_declines_total",
    unit="{decline}",
    description="Total payment gateway declines.",
)
charge_requests = meter.create_counter(
    "payment_charge_requests_total",
    unit="{request}",
    description="Total payment charge requests.",
)
gateway_active_requests = meter.create_up_down_counter(
    "payment_gateway_active_requests",
    unit="{request}",
    description="Active payment gateway requests currently holding a slot.",
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/charge")
def charge(payload: dict):
    amount = payload["amount"]
    charge_requests.add(1)
    acquired = False
    active = False

    wait_start = time.perf_counter()
    with tracer.start_as_current_span("payment.gateway.wait_for_slot") as wait_span:
        wait_span.set_attribute("payment.gateway.pool_size", 2)
        wait_span.set_attribute("payment.amount", amount)
        gateway_pool.acquire()
        acquired = True
        wait_seconds = time.perf_counter() - wait_start
        wait_span.set_attribute("payment.gateway.wait_seconds", wait_seconds)
        gateway_wait.record(wait_seconds)

    try:
        gateway_active_requests.add(1)
        active = True
        processing_start = time.perf_counter()
        try:
            with tracer.start_as_current_span("payment.gateway.process_charge") as process_span:
                process_span.set_attribute("payment.gateway.pool_size", 2)
                process_span.set_attribute("payment.amount", amount)
                time.sleep(random.uniform(0.4, 1.2))
                if random.random() < 0.2:
                    gateway_declines.add(1)
                    process_span.set_attribute("payment.gateway.declined", True)
                    process_span.add_event(
                        "payment.gateway.declined",
                        attributes={"payment.amount": amount},
                    )
                    process_span.set_status(Status(StatusCode.ERROR, "gateway_declined"))
                    logger.error(f"gateway externo recusou cobranca de {amount}")
                    return JSONResponse(status_code=502, content={"error": "gateway_declined"})

                process_span.set_attribute("payment.gateway.declined", False)
                return {"status": "charged", "amount": amount}
        finally:
            gateway_processing.record(time.perf_counter() - processing_start)
    finally:
        if active:
            gateway_active_requests.add(-1)
        if acquired:
            gateway_pool.release()
