"""Build temporary, labelled synthetic court archives for offline integration tests."""
from contextlib import contextmanager
from datetime import date
import hashlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import primary_sources


def pdf_bytes(pages):
    """Create a tagged multipage PDF. Its ActualText strings are the extraction oracle."""
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    kids = []
    for page in pages:
        number = len(objects) + 1
        kids.append(f'{number} 0 R')
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] '
                       f'/Resources << /Font << /F1 3 0 R >> >> /Contents {number + 1} 0 R >>'.encode())
        actual = (b'\xfe\xff' + page.encode('utf-16-be')).hex()
        stream = f'/Span << /ActualText <{actual}> >> BDC\nBT /F1 10 Tf 36 780 Td (Synthetic fixture) Tj ET\nEMC\n'.encode()
        objects.append(f'<< /Length {len(stream)} >>\nstream\n'.encode() + stream + b'endstream')
    objects[1] = f'<< /Type /Pages /Count {len(kids)} /Kids [{" ".join(kids)}] >>'.encode()
    data = bytearray(b'%PDF-1.4\n')
    offsets = []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f'{number} 0 obj\n'.encode() + obj + b'\nendobj\n')
    start = len(data)
    data.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for offset in offsets:
        data.extend(f'{offset:010d} 00000 n \n'.encode())
    data.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode())
    return bytes(data)


def _write_fixture(root):
    """Reuse public identifiers/dates to exercise business flows with invented text."""
    manifest = json.loads((primary_sources.ROOT / 'manifest.json').read_text(encoding='utf-8'))
    for row in manifest['sources']:
        identity = row['id']
        signed = date.fromisoformat(row['signed_on'])
        header = (f"Учебный пример № {row['document_number']} от {signed:%d.%m.%Y}. "
                  "Верховный Суд Российской Федерации. ")
        body = "Синтетический текст для поиска обязательств: поставка неустойка."
        if 'case_number' in row:
            header += f"Судебная коллегия по экономическим спорам. Дело № {row['case_number']}."
            for i, mention in enumerate(row['procedural_mentions'], 1):
                mention['court_mention'] = f'Условный суд {i}'
                mention['quote'] = f"Условный суд {i} упоминает дело № {row['case_number']} от {mention['decision_date']}."
                body += ' ' + mention['quote']
            row['disposition_quote'] = 'Синтетическое требование оставлено для учебной проверки.'
            body += ' Определила: ' + row['disposition_quote']
        for mention in row['amendment_mentions']:
            mention['quote'] = 'Учебное постановление № 6 от 7 февраля 2017 года.'
            body += ' ' + mention['quote']
        pages = [header.strip(), body]
        raw = pdf_bytes(pages)
        value = '\n\n'.join(pages)
        row.update(title=f'Учебный материал {identity}', edition_label='Синтетическая редакция',
                   identity_terms=['Учебный пример', 'Верховный Суд Российской Федерации'],
                   source_url=f'https://vsrf.ru/synthetic/{identity}',
                   original_sha256=hashlib.sha256(raw).hexdigest(),
                   text_sha256=hashlib.sha256(value.encode()).hexdigest(),
                   pages=[{'page': 1, 'start': 0, 'end': len(pages[0])},
                          {'page': 2, 'start': len(pages[0])+2, 'end': len(value)}])
        (root / row['original_file']).write_bytes(raw)
        (root / row['text_file']).write_text(value, encoding='utf-8')
    (root / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')


@contextmanager
def temporary_archive():
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        _write_fixture(root)
        with patch.object(primary_sources, 'ROOT', root), patch.dict('os.environ', {'PRIMARY_ARCHIVE_PDF_DIR': name}):
            yield root


def synthetic_archive(test):
    archive = temporary_archive()
    directory = archive.__enter__()
    test.addCleanup(archive.__exit__, None, None, None)
    return directory
