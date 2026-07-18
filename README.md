# Loja Fake — Mini Projeto de Observabilidade

Loja fictícia com três microsserviços FastAPI instrumentados com OpenTelemetry para uma atividade acadêmica de diagnóstico por observabilidade. Os defeitos de negócio continuam intencionais: a proposta é confirmar a causa raiz usando traces, métricas e logs correlacionados no Grafana LGTM.

## Arquitetura

- **order-service** (`:8000`) recebe `POST /checkout`, chama estoque e pagamento com `httpx`.
- **inventory-service** (`:8001`) reserva estoque dos itens.
- **payment-service** (`:8002`) simula a cobrança em um gateway externo limitado por `Semaphore(2)`.
- **Grafana LGTM** (`:3000`, OTLP `:4318`) recebe traces, métricas e logs via OTLP HTTP.

```text
cliente -> order-service -> inventory-service
                       \-> payment-service

order-service, inventory-service, payment-service -> OTLP HTTP -> Grafana LGTM
```

## Subir o ambiente

```bash
docker compose up --build
```

Abra o Grafana em:

```text
http://localhost:3000
```

O container `grafana/otel-lgtm` já inclui Grafana, Tempo, Loki e Prometheus. Se o Grafana pedir login, tente o padrão `admin` / `admin`.

## Gerar tráfego

Instale a dependência local do gerador:

```bash
python -m pip install httpx
```

Cenários principais:

```bash
python load_test.py --scenario baseline
python load_test.py --scenario large
python load_test.py --scenario declines
python load_test.py --scenario peak --concurrency 50
```

Cenários disponíveis:

- `baseline`: baixa concorrência, quantidades pequenas.
- `large`: pedidos com `qty` 20 e 30, concorrência moderada.
- `declines`: volume suficiente para observar recusas aleatórias no pagamento.
- `peak`: pico de concorrência para evidenciar espera no `Semaphore(2)` e timeouts internos na chamada ao pagamento.
- `legacy`: comportamento padrão de `python load_test.py`, preservando a mistura original de quantidades.

O resultado impresso pelo script é apenas feedback operacional. Para a atividade, use evidências do Grafana, não `print()` nem `docker compose logs`.

## O que foi instrumentado

- Auto-instrumentação FastAPI para spans e métricas HTTP dos três serviços.
- Auto-instrumentação `httpx` para spans das chamadas `order-service -> inventory-service` e `order-service -> payment-service`.
- Propagação W3C Trace Context padrão entre serviços.
- Logs com `trace_id` e `span_id`.
- Métricas customizadas:
  - `order_checkout_duration_seconds`
  - `order_checkout_requests_total`
  - `order_payment_call_errors_total`
  - `order_payment_http_status_total`
  - `inventory_reserve_duration_seconds`
  - `inventory_requested_units`
  - `inventory_reserve_requests_total`
  - `payment_gateway_wait_seconds`
  - `payment_gateway_processing_seconds`
  - `payment_gateway_declines_total`
  - `payment_charge_requests_total`
  - `payment_gateway_active_requests`
- Spans manuais:
  - `inventory.check_stock_batch`
  - `inventory.check_stock_item`
  - `payment.gateway.wait_for_slot`
  - `payment.gateway.process_charge`

## Encontrar traces no Tempo

No Grafana:

1. Acesse **Explore**.
2. Selecione o datasource **Tempo**.
3. Use a busca por serviço ou TraceQL.

Consultas úteis:

```traceql
{ resource.service.name = "order-service" }
{ resource.service.name = "inventory-service" && name = "inventory.check_stock_batch" }
{ resource.service.name = "payment-service" && name = "payment.gateway.wait_for_slot" }
```

Para um checkout específico, abra um trace do `order-service` e procure:

- atributos `order.id`, `order.item_count`, `order.total_quantity`, `order.amount`;
- eventos `order.inventory.request`, `order.inventory.response`, `order.payment.request`, `order.payment.response` ou `order.payment.error`;
- spans filhos HTTP de `httpx`;
- spans manuais dos serviços de estoque e pagamento.

## Encontrar logs no Loki

No Grafana:

1. Acesse **Explore**.
2. Selecione o datasource **Loki**.
3. Pesquise por serviço ou mensagem.

Exemplos:

```logql
{service_name="order-service"}
{service_name="payment-service"} |= "gateway externo recusou cobranca"
{service_name="inventory-service"} |= "reservado"
```

Os labels podem variar conforme a normalização do LGTM. Se `service_name` não aparecer, abra o seletor de labels do Loki e procure o label equivalente de `service.name`.

## Encontrar métricas no Prometheus

No Grafana:

1. Acesse **Explore**.
2. Selecione o datasource **Prometheus**.
3. Use o navegador de métricas ou as consultas em [GRAFANA_QUERIES.md](./GRAFANA_QUERIES.md).

Exemplo:

```promql
histogram_quantile(0.95, sum(rate(order_checkout_duration_seconds_bucket[5m])) by (le))
```

Alguns backends normalizam nomes e labels de métricas OpenTelemetry. Se uma métrica não aparecer exatamente com o nome do código, procure pelo prefixo, por exemplo `order_checkout_duration`.

## Correlacionar log e trace

1. Em **Loki**, encontre um log como `order=... payment call issue` ou `gateway externo recusou cobranca`.
2. Expanda a linha do log e copie o `trace_id`.
3. Abra o datasource **Tempo**.
4. Cole o `trace_id` no campo de busca por Trace ID ou use a ação **Open in Tempo** quando o Grafana oferecer o link.
5. No trace, compare o log com os spans relacionados ao mesmo checkout.

Essa correlação é essencial para provar o chamado em que o `order-service` retorna `confirmed` mesmo quando a cobrança falha.

## Chamados da atividade

Os três chamados continuam presentes por design:

- Pedidos grandes ficam lentos porque o estoque executa um loop por unidade solicitada.
- O pagamento pode ser recusado pelo gateway e ainda assim o pedido pode terminar como `confirmed`.
- Em pico, o `Semaphore(2)` do gateway gera fila; com timeout de 3 segundos no `order-service`, muitas chamadas de pagamento falham por timeout.

Use [RELATORIO_OBSERVABILIDADE.md](./RELATORIO_OBSERVABILIDADE.md) para registrar hipótese, caminho de diagnóstico, evidências e correção proposta.
