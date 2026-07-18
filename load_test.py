import argparse
import random
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

DEFAULT_URL = "http://localhost:8000/checkout"

SCENARIOS = {
    "legacy": {
        "requests": 50,
        "concurrency": 10,
        "qty_choices": [1, 2, 3, 20, 30],
        "description": "comportamento original: mistura baixa e alta quantidade",
    },
    "baseline": {
        "requests": 20,
        "concurrency": 4,
        "qty_choices": [1, 2, 3],
        "description": "baixa concorrencia e quantidades pequenas",
    },
    "large": {
        "requests": 30,
        "concurrency": 8,
        "qty_choices": [20, 30],
        "description": "pedidos grandes com concorrencia moderada",
    },
    "declines": {
        "requests": 80,
        "concurrency": 4,
        "qty_choices": [1, 2, 3],
        "description": "volume suficiente para observar recusas aleatorias",
    },
    "peak": {
        "requests": 50,
        "concurrency": 50,
        "qty_choices": [1, 2, 3],
        "description": "pico de concorrencia para expor espera no Semaphore",
    },
}


def build_payload(qty_choices):
    qty = random.choice(qty_choices)
    return {
        "items": [{"sku": "SKU-1", "qty": qty}],
        "amount": round(qty * 19.9, 2),
    }


def make_order(url, timeout, qty_choices):
    payload = build_payload(qty_choices)
    try:
        response = httpx.post(url, json=payload, timeout=timeout)
        try:
            body = response.json()
        except ValueError:
            body = response.text
        print(response.status_code, body)
        return response.status_code
    except Exception as exc:
        print("erro:", exc)
        return None


def run_scenario(url, timeout, qty_choices, requests, concurrency):
    statuses = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(make_order, url, timeout, qty_choices)
            for _ in range(requests)
        ]
        for future in as_completed(futures):
            statuses.append(future.result())
    return statuses


def parse_args():
    parser = argparse.ArgumentParser(
        description="Gerador de trafego para a loja fake instrumentada com OpenTelemetry."
    )
    parser.add_argument(
        "--scenario",
        choices=sorted(SCENARIOS),
        default="legacy",
        help="cenario de carga a executar",
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="endpoint de checkout")
    parser.add_argument("--requests", type=int, help="numero total de requests")
    parser.add_argument("--concurrency", type=int, help="numero de workers simultaneos")
    parser.add_argument("--timeout", type=float, default=15.0, help="timeout do cliente")
    return parser.parse_args()


def main():
    args = parse_args()
    scenario = SCENARIOS[args.scenario]
    requests = args.requests or scenario["requests"]
    concurrency = args.concurrency or scenario["concurrency"]

    print(
        "scenario=",
        args.scenario,
        "requests=",
        requests,
        "concurrency=",
        concurrency,
        "description=",
        scenario["description"],
    )
    statuses = run_scenario(
        url=args.url,
        timeout=args.timeout,
        qty_choices=scenario["qty_choices"],
        requests=requests,
        concurrency=concurrency,
    )
    ok = sum(1 for status in statuses if status and 200 <= status < 300)
    failures = len(statuses) - ok
    print("summary=", {"ok": ok, "failures": failures})


if __name__ == "__main__":
    main()
