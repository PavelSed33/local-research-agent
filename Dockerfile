FROM python:3.12-slim

WORKDIR /app

# Сторонних зависимостей нет: только стандартная библиотека Python.
COPY agent.py api.py review.py extract.py doctext.py registry.py redline.py penalty.py check.py timeline.py draft.py triage.py duty.py export.py protocol.py scoring.py audit.py source_admission.py legal_quarantine_search.py legal_history.py pravo_portal.py pravo_ips.py ./

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
