FROM python:3.12-slim

WORKDIR /app

# Python использует стандартную библиотеку; PDF/OCR — локальные системные утилиты.
ARG INSTALL_PDF_OCR=1
RUN if [ "$INSTALL_PDF_OCR" = "1" ]; then apt-get update && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*; fi
COPY agent.py api.py api_worker.py request_jobs.py review.py extract.py doctext.py registry.py redline.py legal_history.py pravo_portal.py pravo_ips.py legal_review.py case_law.py preflight.py legal_catalog.py legal_coverage.py privacy_review.py legal_evidence.py contract_scope.py ./

COPY web ./web
COPY legal_research.py statute_corpus.py ./
COPY legal_skills.py ./
COPY local_extract.py ./
COPY document_tools.py workspace_store.py reviewed_evidence.py workspace_api.py matter_research.py ./
COPY primary_sources.py case_report.py docx_redline.py workspace_backup.py service_pilot.py services_retrieval.py ./
COPY benchmark_tasks.py ./
COPY fixtures/legal_benchmarks.json ./fixtures/
COPY fixtures/services_pilot_cases.json ./fixtures/
COPY scripts/manage_workspace.py ./scripts/
COPY fixtures/primary_sources ./fixtures/primary_sources
COPY skills ./skills
COPY third_party/lawve ./third_party/lawve
COPY third_party/selected-skills ./third_party/selected-skills
COPY fixtures/legal_workflows.json ./fixtures/
COPY fixtures/statute_corpus ./fixtures/statute_corpus
COPY fixtures/contract_rule_catalog.json ./fixtures/
COPY fixtures/legal_coverage.json ./fixtures/
COPY fixtures/legal_evidence ./fixtures/legal_evidence

ENV API_HOST=0.0.0.0 \
    API_PORT=8080 \
    API_OUTPUT_DIR=/data/results

# LLM_API_KEY, LLM_MODEL, LLM_BASE_URL, TAVILY_API_KEY и API_TOKEN
# передаются при запуске контейнера через -e или --env-file.
RUN mkdir -p /data/results && chown -R 10001:10001 /data
VOLUME ["/data"]

USER 10001:10001

EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"

CMD ["python", "api.py"]


