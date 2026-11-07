"""Local fact collection for LAW-01; never a compliance certificate."""
from agent import current_check_date, validate_as_of
from legal_coverage import coverage_report
from legal_evidence import evidence_report

COMMON = {
    'scope_established': 'Установлены оператор, субъекты данных и применимость 152-ФЗ?',
    'purpose_defined': 'Указаны конкретные цели и состав обрабатываемых данных?',
    'legal_basis_identified': 'Определено и документировано основание каждой операции?',
}
SCENARIOS = {
    'consent': (['6', '9'], {'consent_evidence': 'Есть доказательства содержания, получения и формы согласия?'}),
    'withdrawal': (['6', '9', '21'], {'withdrawal_received': 'Установлены дата и содержание отзыва?',
        'other_basis_checked': 'Проверены иные основания и обязанности продолжить обработку?'}),
    'delegation': (['6', '7', '19'], {'processor_identified': 'Определены лицо по поручению и его операции?',
        'delegation_terms_checked': 'Проверены поручение, обязанности и меры защиты?'}),
    'distribution': (['6', '10.1'], {'distribution_scope_known': 'Определены публикация, круг получателей и ограничения субъекта?',
        'distribution_basis_checked': 'Отдельно исследовано основание распространения?'}),
    'special': (['10'], {'category_established': 'Определена категория данных и исследованы исключения?'}),
    'biometric': (['11'], {'identification_purpose_known': 'Установлены цель идентификации и фактическое использование данных?'}),
    'cross_border': (['12', '18'], {'recipients_known': 'Известны все получатели, страны и места баз данных?',
        'transfer_requirements_checked': 'Исследованы требования к передаче, уведомлению и ограничениям на дату ситуации?'}),
    'storage': (['5', '18', '19', '21'], {'storage_locations_known': 'Известны места хранения, резервные копии и доступ?',
        'retention_defined': 'Определены основания и сроки хранения для каждой цели?'}),
    'deletion': (['5', '21'], {'deletion_trigger_known': 'Установлены основание и дата прекращения обработки?',
        'copies_accounted_for': 'Учтены копии, журналы и внешние обработчики?'}),
    'notification': (['22'], {'notification_scope_checked': 'Исследованы обязанность уведомления, исключения и изменения сведений?'}),
    'incident': (['19', '21'], {'incident_facts_known': 'Установлены событие, состав данных, последствия и время обнаружения?',
        'incident_duties_checked': 'Исследованы уведомления, расследование и меры на дату события?'}),
}


def privacy_options():
    return {'scenarios': {key: {'provisions': provisions, 'questions': {**COMMON, **questions}}
                          for key, (provisions, questions) in SCENARIOS.items()},
            'verified': False}


def handle_privacy_check(payload):
    scenario = payload.get('scenario')
    if not isinstance(scenario, str) or scenario not in SCENARIOS:
        raise ValueError('Выберите scenario из GET /privacy/options.')
    # Do not accept documents/provider addresses or suggest that a deployment flag is evidence.
    allowed = {'scenario', 'facts', 'as_of', 'demo'}
    if set(payload) - allowed:
        raise ValueError('Допустимы только scenario, facts, as_of и demo; персональные данные и документы не нужны.')
    date = validate_as_of(payload.get('as_of')) or current_check_date()
    provisions, questions = SCENARIOS[scenario]
    questions = {**COMMON, **questions}
    facts = payload.get('facts', {})
    if not isinstance(facts, dict) or set(facts) - set(questions):
        raise ValueError('facts должен содержать только ключи выбранного сценария.')
    if any(value is not None and not isinstance(value, bool) for value in facts.values()):
        raise ValueError('Факты: true, false или null.')
    missing = [{'key': key, 'question': question} for key, question in questions.items() if facts.get(key) is None]
    unresolved = [{'key': key, 'question': question} for key, question in questions.items() if facts.get(key) is False]
    coverage = coverage_report(['LAW-01'], date)
    cards = [card for card in coverage['modules'][0]['cards'] if card['provision'] in provisions]
    return {'scenario': scenario, 'as_of': date, 'checked_on': current_check_date(),
            'status': 'needs_facts' if missing else 'needs_legal_review',
            'summary': 'Уточнение обстоятельств обработки; правомерность не установлена.',
            'questions': missing, 'unresolved_items': unresolved,
            'research_cards': cards, 'sources': coverage['sources'],
            'local_evidence': evidence_report([card['id'] for card in cards], date),
            'verified': False, 'lawyer_reviewed': False,
            'limitation': 'Ответы на анкету — заявления пользователя, не доказательства. '
                          'Согласие и ALLOW_CLOUD_DOCUMENTS не подтверждают законность всей обработки. '
                          'Редакции, переходные правила и исключения требуют отдельной проверки.'}
