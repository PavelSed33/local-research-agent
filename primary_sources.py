"""Pinned original public court PDF and extracted text; not accepted legal editions."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent / 'fixtures/primary_sources'


def load_sources(root=ROOT):
    root = Path(root)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema_version') != 1 or not isinstance(manifest.get('sources'), list):
        raise ValueError('Неверный реестр первичных файлов.')
    result = []
    for row in manifest['sources']:
        for key, fingerprint in [('original_file', 'original_sha256'), ('text_file', 'text_sha256')]:
            name = row[key]
            if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9-]+\.(?:pdf|txt)', name):
                raise ValueError('Недопустимый файл первоисточника.')
            path = root / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 10 * 1024 * 1024:
                raise ValueError('Недоступный первоисточник.')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != row[fingerprint]:
                raise ValueError('Нарушена целостность первоисточника.')
        result.append({**row, 'text': (root / row['text_file']).read_text(encoding='utf-8')})
    return result
