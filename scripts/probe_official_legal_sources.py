"""Probe approved official legal-information hosts without downloading legal texts.

Produces machine-readable diagnostics for restricted CI egress; no license inference.
"""
import argparse
import json
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from scripts.monitor_official_sources import NoRedirect

SOURCES = {
    "official_publication": "https://publication.pravo.gov.ru/",
    "official_law_portal": "https://www.pravo.gov.ru/",
    "minjust_portal": "https://pravo.minjust.ru/",
    "minjust_government": "https://www.minjust.gov.ru/ru/pages/normativnye-pravovye-akty/",
}


def probe(*, opener=None):
    if opener is None:
        opener = build_opener(NoRedirect)
    results = {}
    for key, url in SOURCES.items():
        entry = {"url": url, "reachable": False, "legal_acceptance": False}
        try:
            with opener.open(Request(url, method="HEAD",
                                     headers={"User-Agent": "LegalSourceAvailabilityProbe/1.0"}),
                             timeout=4) as response:
                entry["http_status"] = response.status
                entry["reachable"] = response.status == 200
        except HTTPError as exc:
            entry["http_status"] = exc.code
            entry["reason"] = "http_error"
        except (URLError, TimeoutError, OSError, ssl.SSLError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, socket.gaierror):
                category = "dns_resolution_error"
            elif isinstance(reason, (TimeoutError, socket.timeout)):
                category = "timeout"
            elif isinstance(reason, ssl.SSLError):
                category = "tls_error"
            else:
                category = "network_error"
            entry["reason"] = category
        results[key] = entry
    return {"sources": results, "legal_acceptance": False, "documents_imported": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    from pathlib import Path
    Path(args.output).write_text(json.dumps(probe(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
