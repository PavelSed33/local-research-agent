FROM python:3.12-slim

WORKDIR /app

# Сторонних зависимостей нет: только стандартная библиотека Python.
COPY *.py ./
COPY scripts/ ./scripts/
COPY skills/ ./skills/
COPY third_party/ ./third_party/
COPY web/ ./web/
# Metadata only: never bundle quarantined legal article extracts or PDFs.
COPY fixtures/legal_coverage.json fixtures/legal_workflows.json fixtures/legal_benchmarks.json ./fixtures/

ENV API_HOST=0.0.0.0 \
    API_PORT=8080 \
    API_OUTPUT_DIR=/data/results

# API_TOKEN обязателен при запуске контейнера на 0.0.0.0.
# LLM_API_KEY, LLM_MODEL, LLM_BASE_URL и TAVILY_API_KEY
# передаются при запуске контейнера через -e или --env-file.
RUN mkdir -p /data/results \
    && python -m compileall -q /app \
    && python -c "import api; import agent; import pravo_portal; import pravo_ips; import source_admission"
VOLUME ["/data"]

EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"

CMD ["python", "api.py"]
