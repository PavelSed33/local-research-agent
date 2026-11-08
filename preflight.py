"""Inspect local prerequisites without sending documents, keys or network requests."""
import json
import os
from pathlib import Path
import shutil
import sys

from agent import AgentError, load_dotenv, validate_service_url
from doctext import ensure_document_transfer_allowed
from legal_evidence import evidence_report
from statute_corpus import corpus_report
from legal_skills import load_skills


def inspect_configuration() -> dict:
    """Report presence/validity only; never return setting values or probe hosts."""
    required = ['LLM_API_KEY', 'LLM_MODEL', 'TAVILY_API_KEY']
    placeholders = {'замените_на_ключ_модели', 'замените_на_название_модели', 'замените_на_ключ_tavily',
                    'your_api_key', 'your_model', 'your_tavily_api_key', 'changeme'}
    missing = [name for name in required if not os.getenv(name, '').strip()
               or os.getenv(name, '').strip().casefold() in placeholders]
    invalid = []
    for name, default in [('LLM_BASE_URL', 'https://api.openai.com/v1'),
                          ('TAVILY_API_URL', 'https://api.tavily.com/search')]:
        try:
            validate_service_url(os.getenv(name, default))
        except AgentError:
            invalid.append(name)
    try:
        ensure_document_transfer_allowed()
        document_transfer = True
    except ValueError:
        document_transfer = False
    return {
        'missing_configuration': missing,
        'invalid_endpoint_configuration': invalid,
        'document_transfer_permitted_by_configuration': document_transfer,
        'provider_configuration': {
            'llm': not any(name in missing for name in ('LLM_API_KEY', 'LLM_MODEL')) and 'LLM_BASE_URL' not in invalid,
            'search': 'TAVILY_API_KEY' not in missing and 'TAVILY_API_URL' not in invalid},
    }


def workflow_readiness(configuration, *, statutes_ok=True, evidence_ok=True, skills_ok=True):
    """Configuration readiness is distinct from a successful provider request."""
    workflows = [
        ('/ask', 'Правовой вопрос', ('llm', 'search')),
        ('/case-law/check', 'Судебные акты', ('llm', 'search')),
        ('/review', 'Анализ договора', ('llm', 'document_transfer')),
        ('/extract', 'Извлечение реквизитов', ('llm', 'document_transfer')),
        ('/review/legal', 'Правовая проверка договора', ('llm', 'search', 'document_transfer')),
        ('/diff', 'Сравнение редакций', ()),
        ('/extract/local', 'Локальные реквизиты', ()),
        ('/privacy/check', 'Анкеты персональных данных', ('evidence',)),
        ('/legal/research', 'Правовые сценарии', ('statutes', 'evidence')),
        ('/legal/corpus', 'Нормативные извлечения', ('statutes',)),
        ('/legal/coverage', 'Матрица покрытия', ('statutes', 'evidence')),
        ('contract-playbook', 'Договор по правилам компании', ('llm', 'document_transfer', 'skills')),
        ('contract-risk', 'Пять групп рисков', ('llm', 'document_transfer', 'skills')),
        ('claim-audit', 'Аудит утверждений', ('llm', 'search', 'document_transfer', 'skills')),
    ]
    available = {**configuration['provider_configuration'],
                 'document_transfer': configuration['document_transfer_permitted_by_configuration'],
                 'statutes': statutes_ok, 'evidence': evidence_ok, 'skills': skills_ok}
    result = []
    for identity, title, requirements in workflows:
        blockers = [name for name in requirements if not available[name]]
        external = 'llm' in requirements or 'search' in requirements
        result.append({'id': identity, 'title': title, 'requirements': list(requirements),
                       'blockers': blockers, 'can_attempt': not blockers,
                       'local_fallback_can_attempt': identity == '/review/legal' and blockers == ['search']
                                                     and (statutes_ok or evidence_ok),
                       'status': 'blocked' if blockers else 'configured_unchecked' if external else 'ready_locally',
                       'external_services_tested': False, 'legal_acceptance': False})
    return result


def inspect_runtime() -> dict:
    configuration = inspect_configuration()
    ui = all((Path(__file__).parent / 'web' / name).is_file() for name in ('index.html', 'app.js', 'style.css'))
    corpus = evidence_report()
    statutes = corpus_report()
    try:
        skills_count = len(load_skills())
        skills_ok = skills_count > 0
    except (OSError, ValueError, KeyError, TypeError):
        skills_count, skills_ok = 0, False
    return {
        'python_supported': sys.version_info >= (3, 10),
        'ui_files_present': ui,
        'local_captures': {'checked_count': len(corpus['entries']), 'errors': corpus['errors'],
                           'original_publication_count': 0, 'legal_acceptance': False},
        'statutory_corpus': {'checked_count': len(statutes['entries']), 'errors': statutes['errors'],
                             'period_supported': False, 'legal_acceptance': False},
        **configuration,
        'research_configuration_present': all(configuration['provider_configuration'].values()),
        'legal_skills': {'checked_count': skills_count, 'integrity_ok': skills_ok},
        'workflows': workflow_readiness(configuration, statutes_ok=bool(statutes['entries']) and not statutes['errors'],
                                        evidence_ok=bool(corpus['entries']) and not corpus['errors'], skills_ok=skills_ok),
        'docker_binary_present': shutil.which('docker') is not None,
        'external_services_tested': False,
        'docker_daemon_tested': False,
        'legal_acceptance': False,
        'remaining_acceptance': ['successful live provider requests', 'Docker build and smoke',
                                 'original official legal texts and transition rules',
                                 'lawyer-reviewed real evaluation cases', 'browser visual QA'],
        'limitation': 'Configuration presence is not service availability, valid credentials or legal compliance. No secrets are included in this report.',
    }


if __name__ == '__main__':
    load_dotenv()
    print(json.dumps(inspect_runtime(), ensure_ascii=False, indent=2))

