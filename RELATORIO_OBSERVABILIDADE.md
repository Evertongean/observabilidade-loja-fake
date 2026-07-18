# Relatório de Observabilidade

Preencha as evidências depois de executar os cenários e analisar Grafana Tempo, Loki e Prometheus. As correções abaixo são propostas de código e não foram aplicadas aos serviços nesta etapa.

## Chamado 1 — "Pedidos grandes travam, mas não sempre"

### Hipótese inicial

Pedidos com maior quantidade de itens podem estar gastando tempo no serviço de estoque antes de chegar ao pagamento.

### Cenário executado

```bash
python load_test.py --scenario large
```

### Métricas utilizadas

- `order_checkout_duration_seconds`
- `inventory_reserve_duration_seconds`
- `inventory_requested_units`

Queries sugeridas:

```promql
histogram_quantile(0.95, sum(rate(order_checkout_duration_seconds_bucket[5m])) by (le))
```

```promql
histogram_quantile(0.95, sum(rate(inventory_reserve_duration_seconds_bucket[5m])) by (le))
```

```promql
histogram_quantile(0.95, sum(rate(inventory_requested_units_bucket[5m])) by (le))
```

### Trace analisado

- Trace ID:
- Span principal:
- Span crítico: `inventory.check_stock_batch`
- Atributos relevantes: `inventory.total_quantity`, `inventory.item_count`, `inventory.sku`

### Logs correlacionados

- Log do `order-service` com `order=... iniciando checkout`:
- Log do `inventory-service` com `reservado ... skus`:
- `trace_id` usado na correlação:

### Causa raiz confirmada

O tempo de reserva cresce proporcionalmente à quantidade solicitada porque o estoque executa `check_stock` uma vez por unidade.

### Linha ou trecho responsável

Arquivo: `inventory-service/app.py`

```python
for item in items:
    for _ in range(item["qty"]):
        check_stock(item["sku"])
```

O `time.sleep(0.03)` em `check_stock` multiplica o atraso pela quantidade total.

### Evidências e espaços para prints

- Print do painel/Explore com p95 de `inventory_reserve_duration_seconds`:
- Print do histograma `inventory_requested_units`:
- Print do trace mostrando `inventory.check_stock_batch` com alta duração:
- Print dos atributos `inventory.total_quantity` e `inventory.sku`:

### Correção proposta

Exemplo de correção por consulta/reserva em lote, a ser aplicada somente depois da confirmação:

```diff
diff --git a/inventory-service/app.py b/inventory-service/app.py
@@
-            for item in items:
-                with tracer.start_as_current_span("inventory.check_stock_item") as item_span:
-                    item_span.set_attribute("inventory.sku", item["sku"])
-                    item_span.set_attribute("inventory.quantity", item["qty"])
-                    item_span.set_attribute("inventory.simulated_delay_per_unit_ms", 30)
-                    for _ in range(item["qty"]):
-                        check_stock(item["sku"])
+            for item in items:
+                with tracer.start_as_current_span("inventory.check_stock_item") as item_span:
+                    item_span.set_attribute("inventory.sku", item["sku"])
+                    item_span.set_attribute("inventory.quantity", item["qty"])
+                    check_stock_batch(item["sku"], item["qty"])
@@
-def check_stock(sku: str):
+def check_stock_batch(sku: str, qty: int):
     time.sleep(0.03)
     return True
```

## Chamado 2 — "Cliente diz que pagou, mas o pedido não foi cobrado"

### Hipótese inicial

O `payment-service` pode estar retornando erro de gateway, mas o `order-service` pode estar ignorando essa resposta.

### Cenário executado

```bash
python load_test.py --scenario declines
```

### Métricas utilizadas

- `payment_gateway_declines_total`
- `order_payment_http_status_total`
- `order_checkout_requests_total`

Queries sugeridas:

```promql
sum(increase(payment_gateway_declines_total[5m]))
```

```promql
sum(increase(order_payment_http_status_total{status_code=~"5.."}[5m])) by (status_code)
```

### Trace analisado

- Trace ID:
- Span do `order-service`:
- Span `payment.gateway.process_charge`:
- Evento: `payment.gateway.declined`
- Atributo no order: `payment.http.status_code=502`

### Logs correlacionados

- Log do `payment-service`: `gateway externo recusou cobranca de ...`
- Log do `order-service`: `order=... checkout finalizado`
- `trace_id` comum:

### Causa raiz confirmada

Quando o gateway recusa a cobrança, o `payment-service` retorna HTTP 502, mas o `order-service` registra o status e segue retornando `{"status": "confirmed"}` porque não chama `raise_for_status()` nem altera a resposta.

### Linha ou trecho responsável

Arquivo: `order-service/app.py`

```python
charge = httpx.post(f"{PAYMENT_URL}/charge", json={"amount": amount}, timeout=3)
...
return {"order_id": order_id, "status": "confirmed"}
```

Arquivo: `payment-service/app.py`

```python
if random.random() < 0.2:
    logger.error(f"gateway externo recusou cobranca de {amount}")
    return JSONResponse(status_code=502, content={"error": "gateway_declined"})
```

### Evidências e espaços para prints

- Print da métrica `payment_gateway_declines_total`:
- Print da métrica `order_payment_http_status_total{status_code="502"}`:
- Print do log de recusa no Loki:
- Print do mesmo `trace_id` mostrando checkout finalizado:
- Print do retorno `confirmed` no span/log do `order-service`:

### Correção proposta

Exemplo de correção para não confirmar pedido quando a cobrança falha:

```diff
diff --git a/order-service/app.py b/order-service/app.py
@@
 import httpx
 from fastapi import FastAPI
+from fastapi.responses import JSONResponse
@@
             charge = httpx.post(f"{PAYMENT_URL}/charge", json={"amount": amount}, timeout=3)
             span.set_attribute("payment.http.status_code", charge.status_code)
             span.add_event(
                 "order.payment.response",
                 attributes={"http.status_code": charge.status_code},
             )
             payment_http_status.add(1, {"status_code": str(charge.status_code)})
+            charge.raise_for_status()
         except httpx.HTTPError as e:
             payment_call_errors.add(1, {"error_type": e.__class__.__name__})
             span.record_exception(e)
             span.set_status(Status(StatusCode.ERROR, str(e)))
             span.add_event(
                 "order.payment.error",
                 attributes={"error.type": e.__class__.__name__},
             )
             logger.warning(f"order={order_id} payment call issue: {e}")
+            return JSONResponse(
+                status_code=502,
+                content={"order_id": order_id, "status": "payment_failed"},
+            )
```

## Chamado 3 — "Em horário de pico, muitos checkouts falham"

### Hipótese inicial

O `payment-service` tem pouca capacidade simultânea por causa do `Semaphore(2)`, e o `order-service` usa timeout de 3 segundos na chamada de pagamento.

### Cenário executado

```bash
python load_test.py --scenario peak --concurrency 50
```

### Métricas utilizadas

- `payment_gateway_wait_seconds`
- `payment_gateway_processing_seconds`
- `payment_gateway_active_requests`
- `order_payment_call_errors_total`
- `order_checkout_duration_seconds`

Queries sugeridas:

```promql
histogram_quantile(0.95, sum(rate(payment_gateway_wait_seconds_bucket[5m])) by (le))
```

```promql
payment_gateway_active_requests
```

```promql
sum(increase(order_payment_call_errors_total[5m])) by (error_type)
```

### Trace analisado

- Trace ID:
- Span do `order-service` com evento `order.payment.error`:
- Span crítico: `payment.gateway.wait_for_slot`
- Atributos: `payment.gateway.wait_seconds`, `payment.gateway.pool_size=2`, `payment.amount`

### Logs correlacionados

- Log do `order-service`: `order=... payment call issue: timed out`
- Log do `order-service`: `order=... checkout finalizado`
- `trace_id` usado:

### Causa raiz confirmada

Sob pico, apenas duas cobranças processam simultaneamente. As demais requisições ficam esperando vaga no `Semaphore(2)`. Como o `order-service` espera no máximo 3 segundos pela chamada HTTP ao `payment-service`, muitas chamadas estouram timeout antes da cobrança terminar.

### Linha ou trecho responsável

Arquivo: `payment-service/app.py`

```python
gateway_pool = threading.Semaphore(2)
gateway_pool.acquire()
```

Arquivo: `order-service/app.py`

```python
charge = httpx.post(f"{PAYMENT_URL}/charge", json={"amount": amount}, timeout=3)
```

### Evidências e espaços para prints

- Print do p95 de `payment_gateway_wait_seconds`:
- Print do `payment_gateway_active_requests` ficando limitado a 2:
- Print de `order_payment_call_errors_total` por `error_type`:
- Print do trace com `payment.gateway.wait_for_slot` maior que 3s:
- Print do log correlacionado com timeout:

### Correção proposta

Exemplo didático de correção após validar capacidade do gateway: parametrizar o tamanho do pool e alinhar timeout. Em produção, essa decisão deve vir de limite real do provedor, fila assíncrona ou controle de backpressure.

```diff
diff --git a/payment-service/app.py b/payment-service/app.py
@@
 import random
 import logging
+import os
 import threading
@@
-gateway_pool = threading.Semaphore(2)
+GATEWAY_POOL_SIZE = int(os.getenv("GATEWAY_POOL_SIZE", "10"))
+gateway_pool = threading.Semaphore(GATEWAY_POOL_SIZE)
@@
-        wait_span.set_attribute("payment.gateway.pool_size", 2)
+        wait_span.set_attribute("payment.gateway.pool_size", GATEWAY_POOL_SIZE)
@@
-                process_span.set_attribute("payment.gateway.pool_size", 2)
+                process_span.set_attribute("payment.gateway.pool_size", GATEWAY_POOL_SIZE)
diff --git a/order-service/app.py b/order-service/app.py
@@
 PAYMENT_URL = "http://payment-service:8002"
+PAYMENT_TIMEOUT_SECONDS = 8
@@
-            charge = httpx.post(f"{PAYMENT_URL}/charge", json={"amount": amount}, timeout=3)
+            charge = httpx.post(
+                f"{PAYMENT_URL}/charge",
+                json={"amount": amount},
+                timeout=PAYMENT_TIMEOUT_SECONDS,
+            )
```
