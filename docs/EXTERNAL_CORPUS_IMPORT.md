# Импорт внешнего законодательного корпуса

Первый этап: локальный карантинный индекс, **не подключённый автоматически к /ask**.

## Пример

```bash
python scripts/import_external_law_corpus.py --input ./downloaded_laws --db ./data/external_law_quarantine.sqlite --source-id ruslawod --dry-run
python scripts/import_external_law_corpus.py --input ./downloaded_laws --db ./data/external_law_quarantine.sqlite --source-id ruslawod --max-files 1000
```

Файлы должны быть получены отдельно и законно, после проверки лицензии. Не помещайте массовые корпуса в Git-репозиторий. Для большой коллекции разбивайте файлы на пакеты по 1000 или изменяйте лимит осознанно.

## Текущие ограничения

- Поддерживается только общий формат XML/JSON/JSONL/TXT; формат конкретного поставщика ещё нужно сопоставить и протестировать.
- Для JSON принимаются поля `text`, `body` или `content`; JSONL-объекты объединяются в один документ на файл.
- Все записи имеют `verified=0`; даты действия редакций не выводятся из текста автоматически.
- Дубликаты обнаруживаются внутри одного источника по хешу нормализованного текста.
- Это отдельная SQLite-база, не связанная с рабочими маршрутами анализа.
- Требуются интеграционные тесты, адаптеры источников, проверка лицензий, отслеживание редакций и юридическая приёмка.
- Не запускайте импорт непроверенных огромных файлов без ограничения ресурсов и отдельной изолированной среды.

## Проверка содержимого

```bash
python - <<'PY'
import sqlite3
db = sqlite3.connect('data/external_law_quarantine.sqlite')
print(db.execute('select source_id,count(*),sum(verified) from documents group by source_id').fetchall())
print(db.execute("select title from documents_fts where documents_fts match 'договор' limit 5").fetchall())
PY
```

Проверка полноты должна сопоставлять набор кодексов и статьи с независимым реестром реквизитов; одного количества записей недостаточно.
