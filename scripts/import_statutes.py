"""Import already retrieved article HTML. No network or legal verification occurs here.

Input: <act_id>/<provision>.raw and matching .json from the retrieval job.
Editorial inserts, navigation, scripts and comments are excluded. Save only
statutory text extracts, never claim a full official publication was archived.
"""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from statute_corpus import _url, corpus_report


class NormativeHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.content_depth = None
        self.skip_depth = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'br', 'hr', 'img', 'input', 'meta', 'link', 'wbr', 'source'}:
            if tag == 'br' and self.content_depth and not self.skip_depth:
                self.parts.append('\n')
            return
        self.stack.append(tag)
        classes = attrs.get('class', '').split()
        if self.content_depth is None and 'document-page__content' in classes:
            self.content_depth = len(self.stack)
        if self.content_depth and self.skip_depth is None and (
                'document__insert' in classes or tag in {'script', 'style', 'noscript'}):
            self.skip_depth = len(self.stack)
        if self.content_depth and not self.skip_depth and tag in {'p', 'div', 'h1', 'h2', 'li', 'tr'}:
            self.parts.append('\n')
        if self.content_depth and not self.skip_depth and tag in {'td', 'th'}:
            self.parts.append('\t')

    def handle_endtag(self, tag):
        if tag not in self.stack:
            return
        depth = len(self.stack) - self.stack[::-1].index(tag)
        if self.content_depth and not self.skip_depth and tag in {'p', 'div', 'h1', 'h2', 'li', 'tr'}:
            self.parts.append('\n')
        if self.skip_depth and depth <= self.skip_depth:
            self.skip_depth = None
        if self.content_depth and depth <= self.content_depth:
            self.content_depth = None
        self.stack = self.stack[:depth - 1]

    def handle_data(self, text):
        if self.content_depth and not self.skip_depth:
            self.parts.append(text)

    def text(self):
        lines = [re.sub(r'[^\S\t]+', ' ', line).strip() for line in ''.join(self.parts).splitlines()]
        return '\n'.join(line for line in lines if line and
                         line not in {'(см. текст в предыдущей редакции)', 'КонсультантПлюс: примечание.'})


def import_corpus(download_dir, destination):
    data = json.loads((ROOT / 'fixtures/legal_coverage.json').read_text(encoding='utf-8'))
    sources = {source['id']: source for source in data['sources']}
    cards = {(card['act_id'], card['provision']): card for card in data['cards']}
    previous = corpus_report(corpus_dir=destination, include_text=True)
    retained = {entry['card_id']: {key: entry[key] for key in
                ('card_id', 'act_id', 'provision', 'title', 'heading', 'text', 'url', 'kind',
                 'captured_on', 'sha256', 'response_sha256', 'edition_label')}
                for entry in previous['entries']}
    groups, gaps = [], []
    destination.mkdir(parents=True, exist_ok=True)
    for act in data['acts']:
        sections = []
        kind = sources[act['source_ids'][0]]['kind']
        for provision in act['provisions']:
            raw_path = download_dir / act['id'] / (provision + '.raw')
            meta_path = raw_path.with_suffix('.json')
            try:
                meta = json.loads(meta_path.read_text(encoding='utf-8'))
                if meta['status'] != 'body_received_unchecked':
                    raise ValueError('body_not_received')
                raw = raw_path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != meta['sha256']:
                    raise ValueError('response_hash_mismatch')
                _url(meta['url'], kind)
                if not meta['url'].startswith(sources[act['source_ids'][0]]['url']):
                    raise ValueError('source_act_mismatch')
                parser = NormativeHTML()
                html = raw.decode('utf-8')
                parser.feed(html)
                text = parser.text()
                heading_pattern = (r'(?m)^.*?Статья\s+' + re.escape(provision) + r'\.\s[^\n]+')
                regulatory = provision == 'requirements' or provision.startswith('appendix-')
                match = re.search(heading_pattern, text) if not regulatory and provision != 'преамбула' else None
                if regulatory:
                    lines = text.splitlines()
                    heading = next((line for line in lines if len(line) > 15), '')
                elif provision == 'преамбула':
                    heading = next((line for line in text.splitlines() if 'Настоящий Закон' in line), '')
                    if heading:
                        text = text[text.index(heading):]
                else:
                    if match is None:
                        raise ValueError('article_heading_missing')
                    heading = match.group(0)
                    text = text[match.start():]
                # A heading alone, paywall or index must not become a received article.
                if not heading or len(text) - len(heading) < 35 or len(text) > 200000:
                    raise ValueError('no_usable_normative_body')
                if not regulatory and provision != 'преамбула' and re.search(
                        r'(?m)^.*?Статья\s+(?!' + re.escape(provision) + r'\.\s)\d', text[len(heading):]):
                    raise ValueError('unexpected_other_article')
                edition = re.search(r'\(ред\. от \d{2}\.\d{2}\.\d{4}\)', html)
                card = cards[(act['id'], provision)]
                sections.append({'card_id': card['id'], 'act_id': act['id'], 'provision': provision,
                                 'title': act['title'] + ', ' + (card['scope'] if regulatory else 'преамбула' if provision == 'преамбула' else 'статья ' + provision),
                                 'heading': heading, 'text': text, 'url': meta['url'], 'kind': kind,
                                 'captured_on': meta['captured_on'],
                                 'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                                 'response_sha256': meta['sha256'],
                                 'edition_label': edition.group(0) if edition else None})
            except (OSError, ValueError, KeyError, UnicodeError) as exc:
                old = retained.get(cards[(act['id'], provision)]['id'])
                if old:
                    sections.append(old)
                gaps.append({'act_id': act['id'], 'provision': provision,
                             'status': 'retained_older_extract' if old else 'text_not_imported',
                             'reason': str(exc) if isinstance(exc, ValueError) else type(exc).__name__})
        if sections:
            name = act['id'] + '.json'
            raw = (json.dumps({'sections': sections}, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
            (destination / name).write_bytes(raw)
            groups.append({'act_id': act['id'], 'file': name, 'sha256': hashlib.sha256(raw).hexdigest(),
                           'article_extract_count': len(sections)})
    manifest = {'schema_version': 1, 'captured_on': '2026-11-08', 'groups': groups,
                'gaps': gaps, 'verified': False, 'history_complete': False,
                'raw_response_archived': False, 'original_publication': False,
                'transformation': 'NormativeHTML: document-page__content; editorial inserts excluded; whitespace normalized; table cells separated by tabs.'}
    dates = [section['captured_on'] for group in groups
             for section in json.loads((destination / group['file']).read_text(encoding='utf-8'))['sections']]
    manifest['captured_on'] = max(dates) if dates else None
    # A failed/offline refresh must not erase the quarantine audit trail.
    # Retain provenance warnings and the explicit commercial-use prohibition.
    previous_manifest_path = destination / 'manifest.json'
    if not groups and previous_manifest_path.is_file():
        previous_manifest = json.loads(previous_manifest_path.read_text(encoding='utf-8'))
        quarantined = [gap for gap in previous_manifest.get('gaps', [])
                       if gap.get('status') == 'quarantined_and_removed']
        if quarantined:
            manifest['gaps'] = quarantined + gaps
            manifest['quarantined_on'] = previous_manifest.get('quarantined_on')
            manifest['commercial_use_allowed'] = False
    (destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if destination.resolve() == (ROOT / 'fixtures/statute_corpus').resolve():
        by_act = {group['act_id']: group for group in groups}
        for act in data['acts']:
            group = by_act.get(act['id'])
            act['text_received_count'] = group['article_extract_count'] if group else 0
            if group:
                received = json.loads((destination / group['file']).read_text(encoding='utf-8'))['sections']
                captured = max(section['captured_on'] for section in received)
                for source_id in act['source_ids']:
                    sources[source_id].update(text_observed=True, sha256=group['sha256'],
                        captured_on=captured, source_date=captured,
                        capture_file='fixtures/statute_corpus/' + group['file'],
                        observed_scope=f"{len(received)} selected statutory extracts")
        (ROOT / 'fixtures/legal_coverage.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'groups': len(groups), 'received': sum(g['article_extract_count'] for g in groups), 'gaps': len(gaps)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('download_dir', type=Path)
    parser.add_argument('--destination', type=Path, default=ROOT / 'fixtures/statute_corpus')
    args = parser.parse_args()
    print(json.dumps(import_corpus(args.download_dir, args.destination), ensure_ascii=False))
