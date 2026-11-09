# Массовый импорт RusLawOD: Parquet

Источник: https://huggingface.co/datasets/irlspbru/RusLawOD
Лицензия набора обозначена как CC BY-NC 4.0 (некоммерческая). Перед использованием в коммерческом юридическом агенте необходимо получить соответствующее разрешение или выбрать иной источник.

**Состояние:** поддержка локальных Parquet добавлена; сами файлы в репозиторий не скачаны, количество реально импортированных документов не подтверждено.

## Использование

1. Получите разрешённую копию файлов отдельно от Git-репозитория.
2. `pip install pyarrow`
3. Проверьте реальные названия колонок:

```bash
python -c "import pyarrow.parquet as p; print(p.ParquetFile('data/raw/ruslawod_01.parquet').schema_arrow)"
```

4. Выполните пробный импорт, заменив `TEXT_FIELD` и `TITLE_FIELD` на реальные имена полей:

```bash
python scripts/import_parquet_law_corpus.py --input data/raw/ruslawod_01.parquet --db data/external_law_quarantine.sqlite --text-column TEXT_FIELD --title-column TITLE_FIELD --max-rows 1000
```

5. После проверки качества, лимитов хранения и лицензии уберите `--max-rows` и укажите директорию с файлами.

ВНИМАНИЕ: импортируются только строковые текстовые поля; вложенные структуры и конкретная схема RusLawOD пока не адаптированы. Все документы остаются `verified=0`. Даты действия редакций и полнота кодексов не устанавливаются. Для запуска необходимы локальные файлы и установленный `pyarrow`; этот код не скачивает корпус сам.

## Контроль

```bash
python -c "import sqlite3; c=sqlite3.connect('data/external_law_quarantine.sqlite'); print(c.execute('select count(*), sum(verified) from documents').fetchone())"
```

Не подключайте карантинный индекс к юридическим выводам как подтверждённый действующий закон.
