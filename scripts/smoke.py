"""Offline-only HTTP smoke checks for the recovered API surface.

No real legal validity or external provider availability is asserted.
"""
import argparse
import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit


def smoke(base_url: str) -> list[str]:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Smoke test is restricted to a local server.")
    base_url = base_url.rstrip("/")
    token = os.getenv("API_TOKEN", "")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    passed = []

    with urllib.request.urlopen(base_url + "/health", timeout=10) as response:
        health = json.load(response)
    assert health["status"] == "ok"
    assert health["demo_available"] is True
    passed.append("GET /health")

    with urllib.request.urlopen(base_url + "/openapi.json", timeout=10) as response:
        spec = json.load(response)
    assert "/ask" in spec["paths"]
    passed.append("GET /openapi.json")

    def ask(payload):
        request = urllib.request.Request(
            base_url + "/ask", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers, method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    first = ask({"question": "Демонстрационный правовой вопрос", "demo": True, "as_of": "2023-03-01"})
    assert first["answer"] and first["demo"] is True
    assert first["as_of"] == "2023-03-01"
    assert first["session_id"]
    assert first["report_file"] is None and first["journal_file"] is None
    assert first["results_persisted"] is False
    passed.append("POST /ask demo")

    followup = ask({"question": "Продолжение вопроса", "demo": True, "session_id": first["session_id"]})
    assert followup["session_id"] == first["session_id"]
    passed.append("POST /ask session follow-up")

    delete = urllib.request.Request(
        base_url + "/sessions/delete",
        data=json.dumps({"session_id": first["session_id"]}).encode("utf-8"),
        headers=headers, method="POST",
    )
    with urllib.request.urlopen(delete, timeout=10) as response:
        assert json.load(response)["deleted"] is True
    passed.append("POST /sessions/delete")

    bad = urllib.request.Request(
        base_url + "/ask", data=b'{"question":""}', headers=headers, method="POST",
    )
    try:
        urllib.request.urlopen(bad, timeout=10)
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
    else:
        raise AssertionError("Empty question was accepted")
    passed.append("POST /ask invalid input rejected")
    return passed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    print(json.dumps({"passed": smoke(parser.parse_args().base_url),
                      "scope": "offline_demo_only", "legal_acceptance": False},
                     ensure_ascii=False, indent=2))
