"""Refresh pinned, public article copies into a local staging directory.

No arbitrary URL input and no API route. Only known statutory URLs from the
shipped corpus are fetched. Importing the staging directory is a separate step.
"""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent import current_check_date
from statute_corpus import corpus_report


def _public_copy(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname != 'www.consultant.ru'
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or not parsed.path.startswith('/document/cons_doc_LAW_') or parsed.query or parsed.fragment):
        raise ValueError('Only pinned public statutory copy URLs are allowed')


class PinnedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, newurl):
        _public_copy(newurl)
        return super().redirect_request(request, file, code, message, headers, newurl)


def fetch_article(entry, output_dir):
    url = entry['url']
    _public_copy(url)
    target = output_dir / entry['act_id']
    target.mkdir(parents=True, exist_ok=True)
    raw_path = target / (entry['provision'] + '.raw')
    meta_path = raw_path.with_suffix('.json')
    meta = {key: entry[key] for key in ('act_id', 'provision', 'url', 'title')}
    meta['captured_on'] = current_check_date()
    try:
        opener = urllib.request.build_opener(PinnedRedirects())
        request = urllib.request.Request(url, headers={'User-Agent': 'local-research-agent/statutory-research'})
        with opener.open(request, timeout=15) as response:
            _public_copy(response.url)
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError('Response too large')
            content_type = response.headers.get_content_type()
            if content_type != 'text/html':
                raise ValueError('Not an HTML statutory page')
            meta['url'] = response.url
        temporary = raw_path.with_suffix('.tmp')
        temporary.write_bytes(raw)
        temporary.replace(raw_path)
        meta.update(status='body_received_unchecked', sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))
    except (OSError, ValueError) as exc:
        meta.update(status='unavailable', error_type=type(exc).__name__)
        # A failed refresh must not leave a stale body paired with a new date.
        raw_path.unlink(missing_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return meta['status']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--all', action='store_true')
    selection.add_argument('--card-id', action='append')
    args = parser.parse_args()
    report = corpus_report(args.card_id)
    if report['errors'] or report.get('missing_card_ids'):
        raise SystemExit('Pinned source corpus is incomplete or corrupted')
    counts = {}
    # Deduplicate IDs before concurrent writes to the staging directory.
    entries = {entry['card_id']: entry for entry in report['entries']}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for status in pool.map(lambda entry: fetch_article(entry, args.output_dir), entries.values()):
            counts[status] = counts.get(status, 0) + 1
    print(json.dumps(counts, ensure_ascii=False))
    return 1 if counts.get('unavailable') else 0


if __name__ == '__main__':
    raise SystemExit(main())
