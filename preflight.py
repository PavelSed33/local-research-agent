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


def inspect_runtime() -> dict:
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
    ui = all((Path(__file__).parent / 'web' / name).is_file() for name in ('index.html', 'app.js', 'style.css'))
    corpus = evidence_report()
    statutes = corpus_report()
    return {
        'python_supported': sys.version_info >= (3, 10),
        'ui_files_present': ui,
        'local_captures': {'checked_count': len(corpus['entries']), 'errors': corpus['errors'],
                           'original_publication_count': 0, 'legal_acceptance': False},
        'statutory_corpus': {'checked_count': len(statutes['entries']), 'errors': statutes['errors'],
                             'period_supported': False, 'legal_acceptance': False},
        'missing_configuration': missing,
        'invalid_endpoint_configuration': invalid,
        'research_configuration_present': not missing and not invalid,
        'document_transfer_permitted_by_configuration': document_transfer,
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

