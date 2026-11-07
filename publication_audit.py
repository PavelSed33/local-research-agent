"""Archive a bounded official portal response without asserting legal verification."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import uuid

from agent import AgentError
from pravo_portal import PravoPortalSearch, _extract_document_text


def archive_act(signed_date: str, act_number: str, output_dir: Path) -> Path:
    folder = output_dir / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=False)
    result = {
        'signed_date': signed_date, 'act_number': act_number,
        'attempted_at': datetime.now(timezone.utc).isoformat(),
        'verified': False, 'coverage_verified': False,
        'status': 'retrieval_failed', 'publications': [],
        'limitation': 'Retrieved text is not proof of completeness or legal applicability.',
    }
    try:
        portal = PravoPortalSearch()
        sources = portal.search_act(signed_date, act_number)
        if not sources:
            raise AgentError('Не получено ни одной публикации.')
        for index, source in enumerate(sources):
            publication_id = parse_qs(urlsplit(source.url).query)['eoNumber'][0]
            # Fetch the bounded response again: Source.text is deliberately truncated.
            response = portal._get('/api/DocumentText', {'eonumber': publication_id})
            text = _extract_document_text(response)
            if not text.strip():
                raise AgentError('Повторный запрос не вернул текст публикации.')
            raw = json.dumps(response, ensure_ascii=False, indent=2).encode('utf-8')
            encoded = text.encode('utf-8')
            (folder / f'{index}.json').write_bytes(raw)
            (folder / f'{index}.txt').write_bytes(encoded)
            result['publications'].append({
                'publication_id': publication_id, 'source_url': source.url,
                'text_endpoint': f'{portal.base_url}/api/DocumentText?eonumber={publication_id}',
                'retrieved_at': datetime.now(timezone.utc).isoformat(),
                'response_file': f'{index}.json', 'text_file': f'{index}.txt',
                'response_sha256': hashlib.sha256(raw).hexdigest(),
                'text_sha256': hashlib.sha256(encoded).hexdigest(),
                'text_chars': len(text),
            })
        result['status'] = 'text_retrieved_unverified'
    except (AgentError, ValueError, KeyError) as exc:
        result['error'] = str(exc)
    manifest = folder / 'manifest.json'
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('signed_date')
    parser.add_argument('act_number')
    parser.add_argument('--output-dir', type=Path, default=Path('publication-evidence'))
    args = parser.parse_args()
    manifest = archive_act(args.signed_date, args.act_number, args.output_dir)
    print(manifest)
    return 0 if json.loads(manifest.read_text(encoding='utf-8'))['status'] == 'text_retrieved_unverified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
