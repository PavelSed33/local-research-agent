FROM python:3.12-slim

WORKDIR /app

# Сторонних зависимостей нет: только стандартная библиотека Python.
COPY agent.py api.py review.py extract.py doctext.py registry.py redline.py legal_history.py pravo_portal.py pravo_ips.py legal_review.py case_law.py preflight.py legal_catalog.py legal_coverage.py privacy_review.py legal_evidence.py contract_scope.py ./

COPY web ./web
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

