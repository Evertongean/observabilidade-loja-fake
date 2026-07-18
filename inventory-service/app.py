import time
import logging
from fastapi import FastAPI
from opentelemetry import metrics, trace

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
logger = logging.getLogger("inventory-service")

app = FastAPI()
tracer = trace.get_tracer("inventory-service")
meter = metrics.get_meter("inventory-service")

reserve_duration = meter.create_histogram(
    "inventory_reserve_duration_seconds",
    unit="s",
    description="Duration of inventory reserve requests.",
)
requested_units = meter.create_histogram(
    "inventory_requested_units",
    unit="{unit}",
    description="Number of units requested in inventory reservations.",
)
reserve_requests = meter.create_counter(
    "inventory_reserve_requests_total",
    unit="{request}",
    description="Total inventory reserve requests.",
)


def total_quantity(items):
    return sum(int(item.get("qty", 0)) for item in items)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/reserve")
def reserve(payload: dict):
    start = time.perf_counter()
    items = payload["items"]
    quantity = total_quantity(items)

    reserve_requests.add(1)
    requested_units.record(quantity)

    try:
        with tracer.start_as_current_span("inventory.check_stock_batch") as batch_span:
            batch_span.set_attribute("inventory.item_count", len(items))
            batch_span.set_attribute("inventory.total_quantity", quantity)
            batch_span.set_attribute("inventory.operation", "reserve")

            unique_skus = {item["sku"] for item in items}
            if len(unique_skus) == 1:
                batch_span.set_attribute("inventory.sku", next(iter(unique_skus)))

            for item in items:
                with tracer.start_as_current_span("inventory.check_stock_item") as item_span:
                    item_span.set_attribute("inventory.sku", item["sku"])
                    item_span.set_attribute("inventory.quantity", item["qty"])
                    item_span.set_attribute("inventory.simulated_delay_per_unit_ms", 30)
                    for _ in range(item["qty"]):
                        check_stock(item["sku"])
        logger.info(f"reservado {len(items)} skus")
        return {"reserved": True}
    finally:
        reserve_duration.record(time.perf_counter() - start)


def check_stock(sku: str):
    time.sleep(0.03)
    return True
