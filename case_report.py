"""Local, bounded court evidence snapshots. No model calls or legal certification."""
import hashlib
import re
from datetime import datetime, timedelta, timezone

from reviewed_evidence import ReviewedEvidence, day
from workspace_store import WorkspaceError, canonical, now, text

MAX_CANDIDATES = 200
MAX_ACTS = 20
MAX_REPORT_BYTES = 512_000
SIDES = {'supporting': 'В поддержку довода', 'opposing': 'Против довода', 'distinguishing': 'Существенное отличие'}
RELATIONS = {'appeal': 'Обжалование', 'upheld': 'Оставлен без изменения', 'reversed': 'Отменён', 'remanded': 'Направлен на новое рассмотрение'}
REVIEWS = {'draft': 'Черновик', 'approved': 'Одобрение записано в приложении', 'rejected': 'Возвращён на доработку', 'revoked': 'Одобрение отозвано'}
WARNINGS = [
    'Отчёт содержит только загруженные карточки. Полнота судебной истории и комплект нижестоящих оригиналов не подтверждены.',
    'Последующее движение дела, в том числе результат нового рассмотрения, необходимо проверить отдельно.',
    'Позиции, применимость к договору и смысл связей требуют проверки юристом. Квалификация проверяющих приложением не удостоверяется.',
    'Направление на новое рассмотрение не означает окончательный выигрыш стороны.',
    'Дата среза ограничивает даты актов. Она не доказывает их историческую доступность и не восстанавливает прошлое состояние экспертных проверок.',
    'Отчёт — снимок на момент формирования; перед использованием проверьте изменения карточек, проверок и прав доступа.',
]


def limit_error():
    return WorkspaceError(413, 'case_report_limit', 'Превышен объём отчёта: до 200 судебных карточек с версиями в деле, 20 актов в отчёте и 512000 байт Markdown. Сократите набор материалов; доказательства не обрезаны.')


def evidence_error():
    return WorkspaceError(503, 'case_report_integrity', 'Материалы отчёта не прошли проверку цитат и целостности.')


def fence(value):
    """CommonMark literal block: user content cannot terminate its enclosing fence."""
    marker = '`' * max(3, max((len(run) for run in re.findall(r'`+', value)), default=0) + 1)
    return marker + 'text\n' + value + '\n' + marker


def plain(value):
    value = str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return re.sub(r'([\\`*_{}\[\]()#+.!|~>\-])', r'\\\1', value).replace('\r', ' ').replace('\n', ' ')


def render_markdown(report):
    lines = ['# Материалы судебного дела', '', 'Номер дела: ' + plain(report['case_number']),
             '', 'Дата среза: ' + plain(report['as_of']), '', 'Сформирован (Москва): ' + plain(report['generated_on']),
             '', 'Отпечаток снимка: ' + report['snapshot_sha256'], '', 'Актов: ' + str(report['act_count']),
             '', 'Исключено будущих актов: ' + str(report['excluded']['future_acts']),
             '', 'Исключено прежних версий: ' + str(report['excluded']['superseded_versions']),
             '', '## Ограничения и пробелы', '']
    lines.extend('- ' + plain(message) for message in report['warnings'])
    if not report['acts']:
        lines.extend(['', 'Нет загруженных актов на выбранную дату.'])

    def citation(row):
        lines.extend(['', fence(row['quote']), '', 'Документ: ' + plain(row['document_title']),
            '', 'ID документа: ' + row['document_id'], '', 'SHA-256 текста: ' + row['document_sha256'],
            '', 'Смещения: [' + str(row['start']) + ', ' + str(row['end']) + ') — индексы символов, начало с нуля.',
            '', 'Страницы: ' + (', '.join(map(str, row['pages'])) if row['pages'] else 'карта страниц отсутствует'),
            '', 'Источник: ' + (plain(row['source_url']) if row['source_url'] else 'URL не указан'),
            '', 'Получен: ' + plain(row['captured_on']), '', 'Подлинность источника автоматически не удостоверена.'])

    for index, act in enumerate(report['acts'], 1):
        lines.extend(['', '## Акт ' + str(index), '', plain(act['court']) + ' · ' + plain(act['decision_date']),
            '', 'Инстанция: ' + plain(act['instance']), '', 'ID материала: ' + act['artifact_id'],
            '', 'SHA-256 материала: ' + act['artifact_sha256'], '', 'Проверка: ' + REVIEWS[act['review_status']]])
        if act['review']:
            lines.extend(['', 'Запись проверки: ' + plain(act['review']['created_on']) + '; аккаунт ' + plain(act['review']['reviewer_id'])])
        lines.extend(['', '### Реквизиты в источнике']); citation(act['identity'])
        lines.extend(['', '### Результат — дословная цитата']); citation(act['outcome'])
        lines.extend(['', '### Позиции и применимость'])
        if not act['positions']:
            lines.extend(['', 'Позиции и применимость не заполнены.'])
        for position in act['positions']:
            lines.extend(['', SIDES[position['side']]]); citation(position['citation'])
            lines.extend(['', 'Ручное объяснение применимости:', '', fence(position['applicability_note'])])
        lines.extend(['', '### Связи с другими актами'])
        if not act['links']:
            lines.extend(['', 'Связи не заполнены.'])
        for link in act['links']:
            lines.extend(['', RELATIONS[link['relation']] + ' — ручная разметка, правовой смысл не удостоверен.',
                '', 'Целевая карточка: ' + link['target']['artifact_id'],
                '', plain(link['target']['court']) + ' · ' + plain(link['target']['decision_date']),
                '', 'Проверка цели: ' + REVIEWS[link['target']['review_status']]])
            if link['target']['superseded_by']:
                lines.extend(['', 'ВНИМАНИЕ: цель заменена версией ' + link['target']['superseded_by'] + '; связь требует повторной проверки.'])
            citation(link['citation'])
    return '\n'.join(lines) + '\n'


class CaseReport:
    def __init__(self, store):
        self.store, self.evidence = store, ReviewedEvidence(store)

    def build(self, principal, matter, case_number, as_of):
        matter, case_number, as_of = text(matter, 'matter_id', 64), text(case_number, 'case_number', 200), day(as_of, 'as_of')
        # One short bounded write transaction serializes snapshot, ACL, reviews and audit.
        # There are no provider calls or asynchronous computations in this route.
        with self.store.connect(True) as db:
            self.store._access(db, principal, matter)
            candidates = list(db.execute("SELECT id FROM artifacts WHERE matter_id=? AND kind='decision' ORDER BY id LIMIT ?", (matter, MAX_CANDIDATES + 1)))
            if len(candidates) > MAX_CANDIDATES:
                raise limit_error()
            selected, excluded = [], {'future_acts': 0, 'superseded_versions': 0}
            for candidate in candidates:
                row = self.evidence._artifact(db, principal, candidate['id'])
                if row['payload']['case_number'] != case_number:
                    continue
                if row['version']['superseded_by']:
                    excluded['superseded_versions'] += 1
                elif row['payload']['decision_date'] > as_of:
                    excluded['future_acts'] += 1
                else:
                    selected.append(row)
                    if len(selected) > MAX_ACTS:
                        raise limit_error()
            selected.sort(key=lambda row: (row['payload']['decision_date'], row['payload']['court'], row['id']))
            documents = {}

            def citation(value, document_id):
                if (not isinstance(value, dict) or value.get('document_id') != document_id
                        or not isinstance(value.get('quote'), str)
                        or type(value.get('start')) is not int or type(value.get('end')) is not int):
                    raise evidence_error()
                if document_id not in documents:
                    document = self.store._document(db, principal, document_id)
                    if document['matter_id'] != matter:
                        raise evidence_error()
                    documents[document_id] = document
                document = documents[document_id]
                start, end, quote = value['start'], value['end'], value['quote']
                if (not 0 <= start < end <= len(document['text']) or not 10 <= len(quote) <= 10000
                        or value.get('document_sha256') != document['sha256'] or document['text'][start:end] != quote):
                    raise evidence_error()
                return {'document_id': document_id, 'document_title': document['title'],
                    'document_sha256': document['sha256'], 'quote': quote, 'start': start, 'end': end,
                    'pages': [page['page'] for page in document['pages'] if page['start'] < end and page['end'] > start],
                    'source_url': document['source_url'], 'captured_on': document['captured_on'],
                    'origin': document['provenance'].get('origin', 'unspecified'),
                    'quote_checked': True, 'source_authenticity_automatically_verified': False}

            acts, stale_targets = [], 0
            for row in selected:
                p = row['payload']; document_id = p['identity']['document_id']
                act = {'artifact_id': row['id'], 'artifact_sha256': row['sha256'],
                    'case_number': p['case_number'], 'court': p['court'], 'decision_date': p['decision_date'],
                    'instance': p['instance'], 'version': row['version'], 'review_status': row['review_status'],
                    'review': row['review'], 'identity': citation(p['identity'], document_id),
                    'outcome': citation(p['outcome'], document_id), 'positions': [], 'links': []}
                for position in p['positions']:
                    if position['side'] not in SIDES:
                        raise evidence_error()
                    act['positions'].append({'side': position['side'], 'citation': citation(position['citation'], document_id),
                        'applicability_note': position['applicability_note'], 'semantic_comparability_verified': False})
                for link in p['links']:
                    target = self.evidence._artifact(db, principal, link['target_id'])
                    if (target['kind'] != 'decision' or target['matter_id'] != matter
                            or target['payload']['case_number'] != case_number
                            or target['payload']['decision_date'] > p['decision_date'] or link['relation'] not in RELATIONS):
                        raise evidence_error()
                    stale = target['version']['superseded_by']
                    stale_targets += bool(stale)
                    act['links'].append({'relation': link['relation'], 'citation': citation(link['citation'], document_id),
                        'target': {'artifact_id': target['id'], 'artifact_sha256': target['sha256'],
                                   'court': target['payload']['court'], 'decision_date': target['payload']['decision_date'],
                                   'review_status': target['review_status'], 'superseded_by': stale},
                        'requires_expert_review': True, 'legal_effect_verified': False})
                acts.append(act)
            warnings = list(WARNINGS)
            if stale_targets:
                warnings.append('Есть ссылки на прежние версии карточек: ' + str(stale_targets) + '. Проверьте их заново.')
            report = {'schema_version': 1, 'matter_id': matter, 'case_number': case_number, 'as_of': as_of,
                'act_count': len(acts), 'acts': acts, 'excluded': excluded, 'warnings': warnings,
                'complete_history': False, 'legal_acceptance': False, 'reviewer_qualification_verified': False,
                'model_called': False, 'external_requests_attempted': False,
                'status': 'draft' if acts else 'no_evidence'}
            report['snapshot_sha256'] = hashlib.sha256(canonical(report).encode()).hexdigest()
            report['generated_on'] = datetime.fromisoformat(now()).astimezone(timezone(timedelta(hours=3))).isoformat()
            markdown = render_markdown(report)
            if len(markdown.encode('utf-8')) > MAX_REPORT_BYTES:
                raise limit_error()
            self.store._access(db, principal, matter)
            self.store._audit(db, principal, 'case_report_generated', report['snapshot_sha256'], matter)
        return {'report': report, 'markdown': markdown, 'filename': 'case-report-' + report['snapshot_sha256'][:16] + '.md',
                'content_type': 'text/markdown; charset=utf-8', 'markdown_sha256': hashlib.sha256(markdown.encode('utf-8')).hexdigest(),
                'limits': {'max_candidates': MAX_CANDIDATES, 'max_acts': MAX_ACTS, 'max_markdown_bytes': MAX_REPORT_BYTES}}
