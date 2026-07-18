import logging
import time
import uuid
import httpx
from fastapi import FastAPI
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
logger = logging.getLogger("order-service")

app = FastAPI()
meter = metrics.get_meter("order-service")

INVENTORY_URL = "http://inventory-service:8001"
PAYMENT_URL = "http://payment-service:8002"

checkout_duration = meter.create_histogram(
    "order_checkout_duration_seconds",
    unit="s",
    description="Duration of checkout requests handled by order-service.",
)
checkout_requests = meter.create_counter(
    "order_checkout_requests_total",
    unit="{request}",
    description="Total checkout requests handled by order-service.",
)
payment_call_errors = meter.create_counter(
    "order_payment_call_errors_total",
    unit="{error}",
    description="Total payment HTTP client errors seen by order-service.",
)
payment_http_status = meter.create_counter(
    "order_payment_http_status_total",
    unit="{response}",
    description="HTTP statuses returned by payment-service calls.",
)


def total_quantity(items):
    return sum(int(item.get("qty", 0)) for item in items)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/checkout")
def checkout(payload: dict):
    start = time.perf_counter()
    order_id = str(uuid.uuid4())[:8]
    items = payload["items"]
    amount = payload["amount"]
    item_count = len(items)
    quantity = total_quantity(items)
    span = trace.get_current_span()

    checkout_requests.add(1)
    span.set_attribute("order.id", order_id)
    span.set_attribute("order.item_count", item_count)
    span.set_attribute("order.total_quantity", quantity)
    span.set_attribute("order.amount", amount)

    logger.info(f"order={order_id} iniciando checkout")

    try:
        span.add_event(
            "order.inventory.request",
            attributes={"inventory.url": f"{INVENTORY_URL}/reserve"},
        )
        reserve = httpx.post(f"{INVENTORY_URL}/reserve", json={"items": items}, timeout=10)
        span.add_event(
            "order.inventory.response",
            attributes={"http.status_code": reserve.status_code},
        )
        reserve.raise_for_status()

        try:
            span.add_event(
                "order.payment.request",
                attributes={
                    "payment.url": f"{PAYMENT_URL}/charge",
                    "payment.amount": amount,
                },
            )
            charge = httpx.post(f"{PAYMENT_URL}/charge", json={"amount": amount}, timeout=3)
            span.set_attribute("payment.http.status_code", charge.status_code)
            span.add_event(
                "order.payment.response",
                attributes={"http.status_code": charge.status_code},
            )
            payment_http_status.add(1, {"status_code": str(charge.status_code)})
        except httpx.HTTPError as e:
            payment_call_errors.add(1, {"error_type": e.__class__.__name__})
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.add_event(
                "order.payment.error",
                attributes={"error.type": e.__class__.__name__},
            )
            logger.warning(f"order={order_id} payment call issue: {e}")

        logger.info(f"order={order_id} checkout finalizado")
        return {"order_id": order_id, "status": "confirmed"}
    finally:
        checkout_duration.record(time.perf_counter() - start)
