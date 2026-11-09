"""Offline archive replay checks; original bytes do not establish current legal validity."""
import argparse
import base64
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from document_tools import parse_file
from primary_sources import load_sources, original_bytes


def verify(parse_pdfs=False):
    sources = load_sources()
    results = []
    for source in sources:
        raw = original_bytes(source)
        result = {'id': source['id'], 'pdf_bytes': len(raw), 'pages': len(source['pages']),
                  'text_characters': len(source['text']), 'integrity_checked': True,
                  'identity_checked': True, 'legal_acceptance': False}
        if parse_pdfs:
            parsed = parse_file({'filename': source['original_file'], 'file_base64': base64.b64encode(raw).decode()})
            result['pdf_page_count_matches'] = len(parsed['pages']) == len(source['pages'])
            result['text_replay_matches'] = parsed['document_sha256'] == source['text_sha256']
            result['page_map_replay_matches'] = parsed['pages'] == source['pages']
            result['status'] = 'passed' if all(result[key] for key in ('pdf_page_count_matches', 'text_replay_matches', 'page_map_replay_matches')) else 'extraction_differs'
        else:
            result['status'] = 'passed'
        results.append(result)
    return {'status': 'passed' if all(row['status'] == 'passed' for row in results) else 'extraction_differs',
            'sources': len(sources), 'total_pages': sum(row['pages'] for row in results),
            'total_characters': sum(row['text_characters'] for row in results),
            'native_pdf_replay': parse_pdfs, 'external_requests_attempted': False,
            'legal_acceptance': False, 'results': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parse-pdfs', action='store_true')
    args = parser.parse_args()
    try:
        report = verify(args.parse_pdfs)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(0 if report['status'] == 'passed' else 1)
    except (ValueError, OSError, KeyError, TypeError):
        print(json.dumps({'status': 'failed', 'code': 'primary_archive_unavailable_or_parser_missing', 'legal_acceptance': False}))
        raise SystemExit(2)
