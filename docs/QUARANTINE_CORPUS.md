# Локальный поиск по карантинному корпусу ГК/НК/ТК и другим кодексам

Это **экспериментальный исследовательский поиск**, а не база официально подтверждённых редакций. Результаты нельзя автоматически считать действующим правом. Endpoint `/ask` не использует этот корпус.

## Подготовка

1. Используйте архив `legal_corpus_eleven_codes_checked_v2_unverified.zip`, полученный отдельно от исходников; **не коммитьте тексты в GitHub**.
2. Распакуйте его в локальный каталог так, чтобы в корне каталога были подпапки `gk/`, `nk/`, `tk/`, `koap/`, `kas/`, `grk/`, `uk/`, `upk/`, `uik/`, `gpk/`, `apk/`. В каждой обязательны `manifest.json` и `articles.jsonl`. Если после распаковки файлы оказались на уровень глубже, перенесите **подпапки кодексов**, а не содержимое манифестов.
3. Запустите API с токеном и монтированным каталогом: 

```bash
docker build -t legal-agent:local .
docker run --rm -p 127.0.0.1:18080:8080 \\
  -e API_TOKEN='replace-with-long-random-secret' \\
  -e ENABLE_QUARANTINE_SEARCH=true \\
  -e LEGAL_CORPUS_DIR=/data/legal_corpus \\
  -v '/absolute/path/to/corpus:/data/legal_corpus:ro' \\
  legal-agent:local
```

4. Запрос с авторизацией:

```bash
curl --fail-with-body -sS http://127.0.0.1:18080/corpus/search \\
  -H 'Authorization: Bearer replace-with-long-random-secret' \\
  -H 'Content-Type: application/json' \\
  -d '{"code":"uk","query":"105","limit":5}'
```

Обязательные предупреждения в ответе: `status: quarantine_unverified`, `verified: false`, `results[*].verified: false`. Поиск выключен по умолчанию и требует сконфигурированного `API_TOKEN`, даже если общий API допускает работу без него.

Для юридической верификации отдельно потребуются источник конкретной редакции, дата её действия и пословное сравнение с официальной публикацией. Массовая загрузка и дальнейшее распространение сторонних копий должны проходить проверку правомерности источника.
