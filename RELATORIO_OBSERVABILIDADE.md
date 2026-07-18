# Relatório de Observabilidade — Loja Fake

## 1. Introdução

O projeto Loja Fake representa uma aplicação fictícia de comércio eletrônico composta por três microsserviços desenvolvidos com FastAPI. A atividade teve como objetivo instrumentar esse sistema com OpenTelemetry para viabilizar o diagnóstico de problemas de desempenho, inconsistência e falhas em cenários de maior carga.

A instrumentação realizada permitiu coletar traces, métricas e logs dos serviços e enviá-los ao Grafana LGTM. Com isso, tornou-se possível analisar o comportamento distribuído da aplicação por meio do Tempo, do Loki e do Prometheus. O propósito principal foi investigar três chamados de suporte e identificar suas causas raízes a partir dos sinais de observabilidade.

Os defeitos de negócio existentes não foram corrigidos antes da investigação. Essa decisão foi necessária para preservar o comportamento problemático durante a coleta dos sinais e garantir que as soluções propostas fossem fundamentadas no diagnóstico, e não em suposições iniciais.

## 2. Arquitetura do sistema

A arquitetura observada é composta por três serviços principais. O `order-service`, disponível na porta `8000`, recebe a solicitação de checkout e coordena o fluxo do pedido. Para concluir essa operação, ele se comunica com o serviço de estoque e com o serviço de pagamento.

O `inventory-service`, disponível na porta `8001`, simula a reserva dos itens solicitados no pedido. Já o `payment-service`, disponível na porta `8002`, simula a cobrança em um gateway externo. A comunicação entre os serviços ocorre por chamadas HTTP realizadas pelo `order-service`.

O fluxo principal pode ser representado da seguinte forma:

```text
cliente -> order-service -> inventory-service
                       \-> payment-service
```

O Grafana LGTM, disponível na porta `3000`, foi utilizado como backend de observabilidade. Ele recebeu os sinais enviados por OpenTelemetry e disponibilizou os recursos necessários para consultar traces, logs e métricas em um único ambiente.

## 3. Estratégia de observabilidade

A estratégia de observabilidade combinou instrumentação automática e instrumentação manual. A auto-instrumentação do FastAPI permitiu capturar requisições HTTP recebidas pelos três microsserviços, enquanto a instrumentação do `httpx` registrou as chamadas HTTP realizadas entre eles. A propagação de contexto garantiu que um checkout pudesse ser acompanhado como um trace distribuído, preservando a relação entre os spans gerados pelo `order-service`, pelo `inventory-service` e pelo `payment-service`.

Além da instrumentação automática, foram criados spans manuais nos pontos críticos da aplicação. No serviço de estoque, os spans `inventory.check_stock_batch` e `inventory.check_stock_item` permitiram observar o custo do processamento por lote e por item. No serviço de pagamento, os spans `payment.gateway.wait_for_slot` e `payment.gateway.process_charge` separaram o tempo de espera por uma vaga no gateway do tempo efetivamente gasto na cobrança.

Também foram criadas métricas customizadas para complementar a análise. Entre elas estão `order_checkout_duration_seconds`, `inventory_reserve_duration_seconds`, `inventory_requested_units`, `payment_gateway_wait_seconds`, `payment_gateway_processing_seconds`, `payment_gateway_active_requests` e `order_payment_call_errors_total`. Os logs dos serviços foram configurados com `trace_id` e `span_id`, permitindo relacionar mensagens específicas com os traces correspondentes.

O uso conjunto de traces, métricas e logs foi essencial porque cada sinal respondeu a uma parte diferente da investigação. As métricas mostraram tendências gerais e comportamento sob carga. Os traces indicaram em quais spans o tempo era gasto. Os logs registraram eventos específicos, como recusas do gateway e problemas nas chamadas de pagamento. Dessa forma, nenhum sinal foi tratado isoladamente como suficiente para explicar todos os chamados.

## 4. Chamado 1 — Pedidos grandes travam, mas não sempre

### 4.1 O problema

O primeiro chamado relatava que alguns checkouts apresentavam duração normal, enquanto outros demoravam significativamente mais. Inicialmente, não havia um padrão claramente identificado para explicar por que determinados pedidos eram processados rapidamente e outros ficavam lentos.

Esse comportamento dificultava a análise apenas pela percepção do usuário, pois a lentidão não ocorria em todos os pedidos. Era necessário observar o fluxo interno da requisição para compreender se a variação estava relacionada ao tamanho do pedido, à comunicação entre serviços ou a algum processamento específico.

### 4.2 Suspeita de causa antes da observabilidade

Inicialmente, considerou-se a hipótese de que a lentidão poderia estar relacionada ao `inventory-service`. Essa suspeita partia da possibilidade de que pedidos com maior quantidade de unidades gerassem mais trabalho durante a reserva de estoque.

Também se considerou que poderiam existir chamadas repetitivas ao estoque ou demora na comunicação entre o `order-service` e o `inventory-service`. No entanto, antes da análise dos sinais de observabilidade, essas hipóteses ainda não podiam ser afirmadas como causa confirmada.

### 4.3 Solução proposta

Como solução, propõe-se que a reserva de estoque não execute uma consulta para cada unidade individual do pedido. A operação deveria receber o SKU e a quantidade total solicitada, realizando uma única consulta ou atualização atômica.

Um exemplo simplificado da alteração proposta seria substituir o processamento por unidade por uma chamada agregada:

```python
check_stock(item["sku"], item["qty"])
```

Em um sistema real, essa operação deveria ser implementada no banco de dados ou no mecanismo de controle de estoque de forma eficiente e transacional. Isso reduziria operações repetitivas, diminuiria o tempo de resposta para pedidos grandes e melhoraria a escalabilidade do serviço.

### 4.4 Diagnóstico com observabilidade

Para investigar esse chamado, foi executado o cenário `python load_test.py --scenario large`, que gera pedidos com quantidades maiores. A análise utilizou as métricas `inventory_reserve_duration_seconds`, `inventory_requested_units` e `order_checkout_duration_seconds`.

A métrica `inventory_requested_units` permitiu observar a quantidade de unidades solicitadas nos pedidos, enquanto `inventory_reserve_duration_seconds` indicou o tempo gasto na reserva do estoque. A métrica `order_checkout_duration_seconds` permitiu relacionar esse comportamento com a duração total do checkout. A partir dessa comparação, foi possível verificar que pedidos com mais unidades tendiam a gastar mais tempo no processamento do estoque.

A análise dos traces demonstrou que o span `inventory.check_stock_batch` concentrava o tempo de processamento da reserva. Esse span registra atributos como `inventory.total_quantity`, permitindo comparar a quantidade total do pedido com a duração da operação. O span `inventory.check_stock_item` complementou a análise ao registrar `inventory.quantity` e `inventory.simulated_delay_per_unit_ms`, evidenciando o custo associado a cada item processado.

Os logs correlacionados, como `order=... iniciando checkout`, `reservado ... skus` e `order=... checkout finalizado`, permitiram relacionar a execução do pedido com o trace correspondente por meio do `trace_id`. Dessa forma, confirmou-se que a variação de tempo não estava apenas na percepção externa do checkout, mas no processamento interno do estoque.

A causa raiz confirmada foi o processamento individual de cada unidade do pedido. O código percorre cada unidade, chama `check_stock` repetidamente e cada chamada executa um atraso simulado de `0.03` segundo. Assim, pedidos maiores acumulam mais atrasos e a duração da reserva aumenta proporcionalmente à quantidade.

Os trechos essenciais que comprovam essa causa são:

```python
for _ in range(item["qty"]):
    check_stock(item["sku"])
```

```python
def check_stock(sku: str):
    time.sleep(0.03)
    return True
```

## 5. Chamado 2 — Cliente diz que pagou, mas o pedido não foi cobrado

### 5.1 O problema

O segundo chamado relatava uma inconsistência entre o resultado do checkout e a cobrança financeira. Em determinadas execuções, o `order-service` retornava o status `confirmed`, embora o pagamento não tivesse sido efetivado pelo `payment-service`.

Esse comportamento dificultava o trabalho do suporte porque a resposta final indicava sucesso para o cliente. Ao mesmo tempo, o financeiro não encontrava a cobrança correspondente. A inconsistência surgia porque o estado do pedido não refletia corretamente o resultado da etapa de pagamento.

### 5.2 Suspeita de causa antes da observabilidade

Inicialmente, considerou-se a hipótese de falha no gateway externo de pagamento. Também foram consideradas possibilidades como timeout na comunicação entre serviços, erro de rede, tratamento inadequado de exceções ou ausência de validação da resposta HTTP retornada pelo `payment-service`.

Essas hipóteses indicavam que o problema poderia estar tanto no serviço de pagamento quanto na forma como o `order-service` interpretava o resultado da cobrança. Antes do diagnóstico com observabilidade, entretanto, ainda não era possível afirmar qual desses pontos explicava a inconsistência.

### 5.3 Solução proposta

Como solução, propõe-se que o `order-service` valide explicitamente o status HTTP retornado pelo `payment-service`. Após realizar a chamada de cobrança, o serviço deve executar `charge.raise_for_status()` para interromper o fluxo quando a resposta indicar erro.

Um trecho ilustrativo da proposta é:

```python
charge = httpx.post(...)
charge.raise_for_status()
```

Quando a cobrança falhar, o checkout não deve retornar `confirmed`. O serviço deve registrar adequadamente o erro e retornar um estado coerente, como falha de pagamento ou pagamento pendente, de acordo com a regra de negócio definida. Também é recomendável aplicar idempotência em novas tentativas, evitando cobranças duplicadas caso o cliente ou o sistema repita a operação.

### 5.4 Diagnóstico com observabilidade

Para analisar esse chamado, foi utilizado o cenário `python load_test.py --scenario declines`, que gera volume suficiente para observar recusas aleatórias do gateway simulado. A investigação concentrou-se nos logs do `payment-service`, nos traces distribuídos entre `order-service` e `payment-service` e no status HTTP registrado pelo span do checkout.

Os logs do `payment-service` registraram a mensagem `gateway externo recusou cobranca` quando a cobrança foi recusada. Como os logs continham `trace_id`, foi possível correlacionar essa recusa com a chamada HTTP correspondente no trace e com o log de finalização do checkout no `order-service`.

A análise do trace demonstrou o caminho `order-service -> payment-service`. No span `payment.gateway.process_charge`, o evento `payment.gateway.declined` indica a recusa da cobrança. No `order-service`, o atributo `payment.http.status_code` registra o status HTTP retornado pelo pagamento, incluindo o caso de HTTP `502`.

Dessa forma, confirmou-se que o `payment-service` pode retornar HTTP `502` em razão da recusa simulada do gateway. O `order-service` recebe essa resposta, mas não executa `charge.raise_for_status()` após a chamada. Com isso, o processamento continua e o pedido pode retornar `confirmed` mesmo sem a cobrança concluída. Além disso, quando ocorrem exceções HTTP, elas podem ser apenas registradas no log e ignoradas pelo fluxo principal, mantendo a confirmação do pedido.

Os trechos essenciais do `payment-service/app.py` que demonstram a recusa são:

```python
if random.random() < 0.2:
    logger.error(f"gateway externo recusou cobranca de {amount}")
    return JSONResponse(status_code=502, content={"error": "gateway_declined"})
```

No `order-service/app.py`, a chamada ao pagamento registra o status, mas não interrompe o fluxo com `raise_for_status()`:

```python
charge = httpx.post(f"{PAYMENT_URL}/charge", json={"amount": amount}, timeout=3)
span.set_attribute("payment.http.status_code", charge.status_code)
```

O fluxo ainda finaliza o checkout com sucesso:

```python
logger.info(f"order={order_id} checkout finalizado")
return {"order_id": order_id, "status": "confirmed"}
```

## 6. Chamado 3 — Em horário de pico, muitos checkouts falham

### 6.1 O problema

O terceiro chamado indicava que o sistema funcionava adequadamente com baixa concorrência, mas apresentava aumento de latência e falhas durante picos de tráfego. Nesses períodos, as chamadas internas podiam exceder o tempo limite configurado.

Esse comportamento também podia produzir uma resposta inconsistente para o cliente. Como erros relacionados ao pagamento podiam ser capturados e não interromper a confirmação do pedido, a aplicação ainda poderia retornar `confirmed` mesmo quando a cobrança não tivesse sido concluída corretamente.

### 6.2 Suspeita de causa antes da observabilidade

Inicialmente, considerou-se a hipótese de saturação no `payment-service`. Essa suspeita estava relacionada a um possível limite de concorrência no processamento de cobranças, à formação de fila de requisições e à baixa capacidade do gateway simulado.

Também foi considerada a possibilidade de timeout inadequado no `order-service`. Antes da análise dos sinais, essas hipóteses ainda precisavam ser confirmadas por métricas de espera, traces da chamada de pagamento e registros de erro correlacionados.

### 6.3 Solução proposta

Como solução, propõe-se tratar o problema de capacidade de forma arquitetural. O sistema deve aplicar controle de capacidade e backpressure, dimensionar adequadamente o pool de processamento do pagamento e alinhar os timeouts entre os serviços.

Também é necessário tratar corretamente erros e timeouts, evitando que falhas internas sejam escondidas. Em vez de confirmar o pedido quando a cobrança não for concluída, o sistema deve retornar um erro coerente ou um estado pendente. Para cenários reais, recomenda-se considerar processamento assíncrono de cobranças, uso de idempotência em novas tentativas e monitoramento contínuo da saturação do gateway.

Apenas aumentar o valor do `Semaphore` pode aliviar o sintoma em curto prazo, mas não representa uma solução completa. O dimensionamento deve considerar a capacidade real do gateway, a política de retry, os limites de timeout e o comportamento esperado sob pico de tráfego.

### 6.4 Diagnóstico com observabilidade

Para investigar esse chamado, foi executado o cenário `python load_test.py --scenario peak --concurrency 50`, que aumenta a concorrência sobre o checkout. A análise utilizou as métricas `payment_gateway_wait_seconds`, `payment_gateway_processing_seconds`, `payment_gateway_active_requests`, `order_payment_call_errors_total` e `order_checkout_duration_seconds`.

A métrica `payment_gateway_wait_seconds` permitiu observar o tempo gasto aguardando uma vaga no gateway, enquanto `payment_gateway_processing_seconds` representou o tempo de cobrança depois que a vaga foi adquirida. A métrica `payment_gateway_active_requests` indicou a quantidade de requisições mantendo uma vaga ativa. Já `order_payment_call_errors_total` registrou erros nas chamadas de pagamento, e `order_checkout_duration_seconds` mostrou o impacto desse comportamento na duração total do checkout.

Nos traces, a análise concentrou-se nos spans `payment.gateway.wait_for_slot` e `payment.gateway.process_charge`. O span `payment.gateway.wait_for_slot` começa antes da chamada a `gateway_pool.acquire()`, portanto sua duração representa o tempo real de espera por uma vaga. O span `payment.gateway.process_charge` envolve o atraso simulado da cobrança e a decisão de aprovação ou recusa.

Dessa forma, confirmou-se que o `payment-service` utiliza `threading.Semaphore(2)`, permitindo que apenas duas cobranças sejam processadas simultaneamente. As demais requisições aguardam uma vaga. Como cada processamento possui atraso simulado entre `0.4` e `1.2` segundo, a fila cresce durante períodos de pico.

O `order-service`, por sua vez, possui timeout de três segundos na chamada ao `payment-service`. Assim, a chamada pode expirar antes que a cobrança seja processada. Quando esse erro é capturado e apenas registrado, o fluxo pode continuar até retornar `confirmed`, reforçando a inconsistência observada no comportamento do checkout.

Os trechos essenciais que demonstram o limite de concorrência e o timeout são:

```python
gateway_pool = threading.Semaphore(2)
```

```python
charge = httpx.post(f"{PAYMENT_URL}/charge", json={"amount": amount}, timeout=3)
```

## 7. Conclusão

A investigação demonstrou que a observabilidade permitiu diferenciar três problemas que, externamente, poderiam parecer apenas falhas genéricas de checkout. No primeiro chamado, os traces e métricas mostraram que a lentidão estava relacionada ao processamento individual de unidades no estoque. No segundo, os logs correlacionados por `trace_id` e os traces distribuídos indicaram que recusas de pagamento podiam ocorrer sem impedir a confirmação do pedido. No terceiro, as métricas de espera e os spans do pagamento evidenciaram a saturação causada pelo limite de concorrência do gateway simulado.

As métricas permitiram observar tendências e comportamentos gerais sob diferentes cenários de carga. Os traces revelaram onde o tempo foi gasto dentro do fluxo distribuído, e os logs identificaram eventos específicos que explicavam inconsistências de negócio. O `trace_id` foi fundamental para correlacionar eventos entre microsserviços e acompanhar uma mesma requisição do início ao fim.

Conclui-se que nenhum dos sinais, isoladamente, seria suficiente para explicar todas as causas raízes. A combinação de métricas, traces e logs permitiu confirmar os diagnósticos e fundamentar as soluções propostas para cada chamado, preservando a separação entre investigação por observabilidade e alteração do comportamento funcional do sistema.
