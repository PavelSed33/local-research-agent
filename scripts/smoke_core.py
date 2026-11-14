"""Contract smoke test for the implemented core HTTP API only.

The legacy scripts/smoke.py additionally expects experimental UI and legal
research endpoints not implemented on this integration branch. Keep it as a
separate acceptance target; do not report those endpoints as passing.
"""
import argparse
import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit


def smoke(base_url: str) -> list[str]:
    parsed = urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Only local servers can be smoke-tested")
    base = base_url.rstrip("/")
    headers = {"Content-Type": "application/json"}
    token = os.getenv("API_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    passed = []
    for path in ("/health", "/openapi.json"):
        with urllib.request.urlopen(urllib.request.Request(base + path, headers=headers), timeout=10) as response:
            data = json.load(response)
            assert response.status == 200
            assert isinstance(data, dict)
        passed.append("GET " + path)
    payloads = (
        ("/ask", {"question": "Техническая проверка", "demo": True, "as_of": "2023-03-01"}, "answer"),
        ("/diff", {"document_a": "Стороны установили срок поставки десять календарных дней.", "document_b": "Стороны установили срок поставки пять календарных дней."}, "changes"),
        ("/extract", {"document": "ИНН 7707083893. Цена 1000 рублей", "demo": True}, "parties"),
    )
    for path, data, key in payloads:
        request = urllib.request.Request(base + path, data=json.dumps(data).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
            assert response.status == 200 and key in result, path
        passed.append("POST " + path)
    # Demonstrate that the quarantined corpus is not available without opt-in.
    request = urllib.request.Request(base + "/corpus/search", data=b'{"code":"gk","query":"395"}', headers=headers)
    try:
        urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as exc:
        assert exc.code in (400, 503), exc.code
    else:
        raise AssertionError("Unconfigured quarantined corpus unexpectedly accessible")
    passed.append("POST /corpus/search (disabled by default)")
    return passed


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    result = smoke(parser.parse_args().base_url)
    print(json.dumps({"passed": result, "legal_acceptance": False,
                      "legacy_experimental_acceptance": "not_run"}, ensure_ascii=False, indent=2))
