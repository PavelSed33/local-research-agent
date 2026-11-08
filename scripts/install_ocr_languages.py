"""Explicit download of pinned Apache-2.0 Tesseract language data; never runs on API startup."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

COMMIT = '87416418657359cb625c412a48b6e1d6d41c29bd'
FILES = {'rus.traineddata': 'b146cb2263acbc6383f8e92ea0ce759537687bb8',
         'eng.traineddata': 'bbef4675053b5b468cdb477053e28b1c698ba08e',
         'LICENSE': 'd645695673349e3947e8e5ae42332d0ac3164cd7'}


def git_hash(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def install(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    staged = {}
    for name, expected in FILES.items():
        target = directory / name
        if target.exists():
            raw = target.read_bytes()
        else:
            url = f'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{COMMIT}/{name}'
            with urllib.request.urlopen(url, timeout=30) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024 or git_hash(raw) != expected:
            raise ValueError('Downloaded or existing OCR data failed pinned Git blob verification.')
        staged[name] = raw
    for name, raw in staged.items():
        temporary = directory / (name + '.tmp')
        temporary.write_bytes(raw)
        temporary.replace(directory / name)
    return {'status': 'installed', 'source_commit': COMMIT, 'license': 'Apache-2.0',
            'TESSDATA_PREFIX': str(directory),
            'sha256': {name: hashlib.sha256(raw).hexdigest() for name, raw in staged.items()}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path('runtime/tessdata'))
    args = parser.parse_args()
    try:
        print(json.dumps(install(args.directory), indent=2))
    except (ValueError, OSError):
        print(json.dumps({'status': 'failed', 'reason': 'ocr_language_download_or_integrity'}))
        raise SystemExit(1)
