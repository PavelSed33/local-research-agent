"""Find possible original publications in official day indexes; no automatic legal admission."""
import argparse
import datetime as dt
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlencode, urljoin
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError

from scripts.monitor_official_sources import ALLOWED, NoRedirect, REGISTRY, allowed_url

PUBLICATIONS = "https://publication.pravo.gov.ru"
DOCUMENT = re.compile(r"/document/(\d{16})")
MAX_HTML = 2_000_000


class PublicationParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(" ".join(self._text).split())))
            self._href = None
            self._text = []


def publication_candidates(html, *, number, signed_on):
    parser = PublicationParser()
    parser.feed(html)
    found = []
    for href, label in parser.links:
        url = urljoin(PUBLICATIONS, href)
        match = DOCUMENT.search(url)
        if (match and allowed_url(url) and
                re.search(r"(?<![\w])" + re.escape(number) + r"(?![\w])", label, re.I) and
                (signed_on in label or dt.date.fromisoformat(signed_on).strftime("%d.%m.%Y") in label)):
            found.append({"url": url, "publication_number": match.group(1),
                          "matched_link_text": label[:350],
                          "status": "candidate_requires_manual_identity_edition_rights_review",
                          "verified": False})
    return found


def discover(registry, *, fetch=None, days=7):
    if fetch is None:
        opener = build_opener(NoRedirect)
        def fetch(url):
            with opener.open(Request(url, headers={"User-Agent": "LegalSourceDiscovery/1.0"}),
                             timeout=4) as response:
                if response.status != 200:
                    raise ValueError("Unexpected HTTP status")
                if int(response.headers.get("Content-Length", "0")) > MAX_HTML:
                    raise ValueError("Oversized page")
                data = response.read(MAX_HTML + 1)
                if len(data) > MAX_HTML:
                    raise ValueError("Oversized page")
                return data.decode("utf-8", errors="replace")
    results = []
    for act in registry["acts"]:
        result = {"id": act["id"], "title": act["title"], "candidates": [],
                  "verified": False, "imported": False}
        signed_on, number = act.get("signed_on"), act.get("number")
        try:
            start = dt.date.fromisoformat(signed_on)
            if not number or not isinstance(number, str):
                raise ValueError("Missing act number")
            for offset in range(days + 1):
                date = start + dt.timedelta(days=offset)
                url = PUBLICATIONS + "/documents?" + urlencode({
                    "date": date.isoformat(), "index": 1, "pageSize": 200, "periodType": "day"})
                if not allowed_url(url):
                    raise ValueError("Disallowed official index")
                try:
                    matches = publication_candidates(fetch(url), number=number, signed_on=signed_on)
                    result["candidates"].extend(matches)
                except (HTTPError, URLError, OSError, TimeoutError, ValueError, UnicodeError) as exc:
                    result.setdefault("errors", []).append({"date": date.isoformat(), "type": type(exc).__name__})
        except (ValueError, TypeError, OverflowError):
            result["status"] = "invalid_act_metadata"
        else:
            result["status"] = "candidate_found" if result["candidates"] else "not_found_in_checked_dates"
        results.append(result)
    return {"source": PUBLICATIONS, "acts_checked": len(results),
            "candidates_found": sum(len(x["candidates"]) for x in results),
            "legal_acceptance": False, "full_text_imported": 0, "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 0 <= args.days <= 14:
        parser.error("--days must be between 0 and 14")
    if not 1 <= args.limit <= 59:
        parser.error("--limit must be between 1 and 59")
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    registry["acts"] = registry["acts"][:args.limit]
    report = discover(registry, days=args.days)
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    main()
