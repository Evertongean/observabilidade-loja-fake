# Grafana Queries

As métricas abaixo usam os nomes emitidos pela instrumentação. Dependendo da versão do pipeline OTLP -> Prometheus no LGTM, nomes e labels podem aparecer normalizados, por exemplo com sufixos como `_bucket`, `_count`, `_sum` ou labels como `service_name` no lugar de `service.name`.

## Prometheus

### p95 da duração do checkout

```promql
histogram_quantile(0.95, sum(rate(order_checkout_duration_seconds_bucket[5m])) by (le))
```

Por serviço:

```promql
histogram_quantile(0.95, sum(rate(order_checkout_duration_seconds_bucket[5m])) by (le, service_name))
```

### p95 do processamento do estoque

```promql
histogram_quantile(0.95, sum(rate(inventory_reserve_duration_seconds_bucket[5m])) by (le))
```

### Relação entre quantidade solicitada e duração

Compare os dois painéis no mesmo intervalo:

```promql
histogram_quantile(0.95, sum(rate(inventory_requested_units_bucket[5m])) by (le))
```

```promql
histogram_quantile(0.95, sum(rate(inventory_reserve_duration_seconds_bucket[5m])) by (le))
```

Para confirmar por trace, procure spans `inventory.check_stock_batch` com o atributo `inventory.total_quantity`.

### p95 da espera pelo Semaphore

```promql
histogram_quantile(0.95, sum(rate(payment_gateway_wait_seconds_bucket[5m])) by (le))
```

### p95 do processamento da cobrança

```promql
histogram_quantile(0.95, sum(rate(payment_gateway_processing_seconds_bucket[5m])) by (le))
```

### Número de recusas do gateway

```promql
sum(increase(payment_gateway_declines_total[5m]))
```

### Erros e timeouts nas chamadas de pagamento

```promql
sum(increase(order_payment_call_errors_total[5m])) by (error_type)
```

```promql
sum(increase(order_payment_http_status_total{status_code=~"5.."}[5m])) by (status_code)
```

### Requisições ativas no gateway

```promql
payment_gateway_active_requests
```

### Volume de checkouts

```promql
sum(increase(order_checkout_requests_total[5m]))
```

## Loki

### Logs de cobrança recusada

```logql
{service_name="payment-service"} |= "gateway externo recusou cobranca"
```

### Logs de problemas na chamada de pagamento

```logql
{service_name="order-service"} |= "payment call issue"
```

### Logs do order-service relacionados ao mesmo trace_id

Substitua `<TRACE_ID>` pelo valor copiado do log ou do trace:

```logql
{service_name="order-service"} |= "trace_id=<TRACE_ID>"
```

Se o LGTM expuser `trace_id` como metadata estruturada, esta forma também pode funcionar:

```logql
{service_name="order-service"} | trace_id = "<TRACE_ID>"
```

### Logs de reserva de estoque

```logql
{service_name="inventory-service"} |= "reservado"
```

## Tempo / TraceQL

### Traces do checkout

```traceql
{ resource.service.name = "order-service" }
```

### Spans manuais do estoque

```traceql
{ resource.service.name = "inventory-service" && name = "inventory.check_stock_batch" }
```

### Spans de espera pelo Semaphore

```traceql
{ resource.service.name = "payment-service" && name = "payment.gateway.wait_for_slot" }
```

### Spans de processamento da cobrança com erro

```traceql
{ resource.service.name = "payment-service" && name = "payment.gateway.process_charge" && status = error }
```

### Traces com chamadas de pagamento que retornaram 5xx

```traceql
{ resource.service.name = "order-service" && .payment.http.status_code >= 500 }
```

Se a sintaxe de atributos variar na interface do Tempo, abra um trace conhecido e copie o nome do atributo exibido no painel de detalhes do span.
