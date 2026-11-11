"""Check archive consistency; hashes do not authenticate a legal source."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from pravo_portal import _extract_document_text


def verify_archive(manifest: Path) -> dict:
    errors = []
    try:
        data = json.loads(manifest.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('Manifest must be an object')
        records = data.get('publications')
        if data.get('status') != 'text_retrieved_unverified':
            errors.append('Retrieval was not successful')
        if not isinstance(records, list) or not records:
            raise ValueError('No publication records')
        seen = set()
        root = manifest.parent.resolve()
        for item in records:
            if not isinstance(item, dict):
                raise ValueError('Publication record must be an object')
            identity = item.get('publication_id')
            if not isinstance(identity, str) or not identity.isascii() or not identity.isdigit() or identity in seen:
                raise ValueError('Missing, invalid or duplicate publication ID')
            seen.add(identity)
            for key, parameter, endpoint in (
                ('source_url', 'eoNumber', '/api/Document'),
                ('text_endpoint', 'eonumber', '/api/DocumentText'),
            ):
                url = urlsplit(item[key])
                if (url.scheme != 'https' or url.netloc != 'publication.pravo.gov.ru'
                        or url.path != endpoint or parse_qs(url.query).get(parameter) != [identity]):
                    raise ValueError('Publication URL does not match official endpoint and ID')
            content = {}
            for kind in ('response', 'text'):
                name = item[f'{kind}_file']
                if not isinstance(name, str):
                    raise ValueError('Invalid archive filename')
                path = (root / name).resolve()
                if path.parent != root:
                    raise ValueError('Archive file must be inside the manifest directory')
                payload = path.read_bytes()
                if hashlib.sha256(payload).hexdigest() != item[f'{kind}_sha256']:
                    errors.append(f'{kind} checksum mismatch: {identity}')
                content[kind] = payload.decode('utf-8')
            text = content['text']
            if not text.strip() or len(text) != item['text_chars']:
                errors.append(f'Invalid text length: {identity}')
            if _extract_document_text(json.loads(content['response'])) != text:
                errors.append(f'Text differs from saved endpoint response: {identity}')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(str(exc))
    return {'integrity_ok': not errors, 'errors': errors, 'verified': False,
            'coverage_verified': False,
            'limitation': 'Local consistency only; source authenticity and legal applicability are not established.'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    result = verify_archive(parser.parse_args().manifest)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['integrity_ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
