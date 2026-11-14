FROM python:3.12-slim

WORKDIR /app

# Сторонних зависимостей нет: только стандартная библиотека Python.
COPY agent.py api.py review.py extract.py doctext.py registry.py redline.py penalty.py check.py timeline.py draft.py triage.py duty.py export.py protocol.py scoring.py audit.py source_admission.py legal_quarantine_search.py legal_history.py pravo_portal.py pravo_ips.py legal_review.py legal_catalog.py legal_coverage.py legal_evidence.py statute_corpus.py contract_scope.py legal_research.py legal_skills.py local_extract.py workspace_api.py workspace_store.py workspace_backup.py reviewed_evidence.py case_report.py docx_redline.py document_tools.py primary_sources.py request_jobs.py api_worker.py matter_research.py case_law.py preflight.py ./
COPY fixtures/contract_rule_catalog.json fixtures/legal_workflows.json fixtures/legal_coverage.json ./fixtures/
COPY fixtures/legal_evidence ./fixtures/legal_evidence
COPY fixtures/statute_corpus ./fixtures/statute_corpus
COPY skills ./skills
COPY third_party ./third_party
COPY web ./web

ENV API_HOST=0.0.0.0 \
    API_PORT=8080 \
    API_OUTPUT_DIR=/data/results

# Для локального карантинного поиска монтируйте корпус в /data/legal_corpus и задавайте\n# ENABLE_QUARANTINE_SEARCH=true LEGAL_CORPUS_DIR=/data/legal_corpus.\n# По умолчанию поиск выключен, а /ask к нему не обращается.\n# LLM_API_KEY, LLM_MODEL, LLM_BASE_URL, TAVILY_API_KEY и API_TOKEN
# передаются при запуске контейнера через -e или --env-file.
RUN mkdir -p /data/results
VOLUME ["/data"]

EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"

CMD ["python", "api.py"]
