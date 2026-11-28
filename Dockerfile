FROM python:3.12-slim

WORKDIR /app

ARG INSTALL_PDF_OCR=1
RUN if [ "$INSTALL_PDF_OCR" = "1" ]; then \
        apt-get update && apt-get install -y --no-install-recommends \
            poppler-utils tesseract-ocr tesseract-ocr-eng tesseract-ocr-rus && \
        rm -rf /var/lib/apt/lists/*; \
    fi

COPY agent.py api.py api_worker.py request_jobs.py preflight.py ./
COPY audit.py check.py doctext.py draft.py duty.py export.py extract.py penalty.py protocol.py redline.py registry.py review.py scoring.py timeline.py triage.py ./
COPY case_law.py contract_scope.py legal_catalog.py legal_coverage.py legal_evidence.py legal_history.py legal_research.py legal_review.py legal_skills.py privacy_review.py ./
COPY pravo_ips.py pravo_portal.py primary_sources.py source_admission.py statute_corpus.py ./
COPY benchmark_tasks.py case_report.py document_tools.py docx_redline.py local_extract.py matter_research.py reviewed_evidence.py workspace_api.py workspace_backup.py workspace_store.py ./
COPY scripts/manage_workspace.py ./scripts/
COPY web ./web
COPY skills ./skills
COPY third_party ./third_party
COPY fixtures/legal_benchmarks.json fixtures/legal_coverage.json fixtures/legal_workflows.json fixtures/contract_rule_catalog.json ./fixtures/
COPY fixtures/legal_evidence ./fixtures/legal_evidence
COPY fixtures/statute_corpus/manifest.json ./fixtures/statute_corpus/
COPY fixtures/primary_sources/manifest.json fixtures/primary_sources/vsrf-*.txt ./fixtures/primary_sources/

ENV API_HOST=0.0.0.0 \
    API_PORT=8080 \
    API_OUTPUT_DIR=/data/results \
    PYTHONDONTWRITEBYTECODE=1

RUN mkdir -p /data/results && chown -R 10001:10001 /data && chmod 700 /data
VOLUME ["/data"]
USER 10001:10001
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('API_PORT', '8080') + '/health', timeout=4)"

CMD ["python", "api.py"]
