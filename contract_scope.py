"""Collect declared facts before specialist checklists; never decide legal classification."""
from legal_catalog import CONTRACT_TYPES

TYPE_FACTS = {
    'general': {},
    'supply': {'goods_nonpersonal_purpose': 'Товар приобретается для предпринимательских или иных целей, не связанных с личным, семейным или домашним использованием?'},
    'lease': {'temporary_use_for_fee': 'Предусмотрены временное владение или пользование имуществом за плату?'},
    'services': {'paid_activity': 'Предусмотрены оплачиваемые действия или деятельность исполнителя?'},
    'work': {'result_handover': 'Предусмотрены выполнение работы и сдача её результата заказчику?'},
}
REGIME_FACTS = {
    'mixed_contract': 'Есть ли в договоре элементы разных видов договоров?',
    'special_regime': 'Есть ли признаки специального режима, например госзакупки, лизинга или строительного подряда?',
}


def scope_options():
    return {kind: {'questions': {**TYPE_FACTS[kind], **(REGIME_FACTS if kind != 'general' else {})},
                   'expected_answers': {**dict.fromkeys(TYPE_FACTS[kind], True),
                                        **(dict.fromkeys(REGIME_FACTS, False) if kind != 'general' else {})}}
            for kind in CONTRACT_TYPES}


def contract_qualification(profile, contract_type, facts):
    if not isinstance(contract_type, str) or contract_type not in CONTRACT_TYPES:
        raise ValueError('Неизвестный contract_type.')
    if profile not in {'business', 'consumer', 'employment'} or not isinstance(facts, dict):
        raise ValueError('Неизвестный профиль или неверный объект facts.')
    if profile == 'employment' and contract_type != 'general':
        raise ValueError('Для трудового сценария используйте contract_type=general.')
    known = set(REGIME_FACTS) | {key for questions in TYPE_FACTS.values() for key in questions}
    for key in known:
        if key in facts and facts[key] is not None and not isinstance(facts[key], bool):
            raise ValueError(f'{key} должен быть true, false или null.')
    expected = scope_options()[contract_type]
    missing = [question for key, question in expected['questions'].items() if facts.get(key) is None]
    conflicts = [expected['questions'][key] for key, value in expected['expected_answers'].items()
                 if facts.get(key) is not None and facts[key] != value]
    if profile == 'consumer' and contract_type == 'supply' and facts.get('personal_use') is True:
        conflicts.append('Личная цель приобретения не согласуется с выбранной поставкой; уточните вид обязательств без автоматической переквалификации.')
    # The general mode is deliberately limited, not a way to confirm any special regime.
    if profile != 'employment' and contract_type == 'general':
        conflicts.extend(REGIME_FACTS[key] for key in REGIME_FACTS if facts.get(key) is True)
    return {
        'contract_type': contract_type,
        'status': ('needs_qualification' if conflicts else 'needs_facts' if missing else
                   'not_applicable' if profile == 'employment' else
                   'general_scope_only' if contract_type == 'general' else 'candidate_type'),
        'questions': missing, 'conflicting_facts': conflicts,
        'declared_facts': {key: facts.get(key) for key in expected['questions']},
        'verified': False, 'lawyer_reviewed': False,
        'limitation': 'Ответы пользователя не доказывают правовую квалификацию. Проверить содержание обязательств, '
                      'приложения, переписку, исполнение и специальные нормы. Общий режим не покрывает все специальные договоры.'}
