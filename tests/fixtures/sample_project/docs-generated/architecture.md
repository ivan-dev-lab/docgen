# Архитектура проекта

## Источник данных

- `analysis_dir`: `tests\fixtures\sample_project\.docgen-analysis`
- `schema_version`: `1.0`
- `generated_at`: `2026-04-20T04:44:15.085855+00:00`

## Общая структура проекта

### Основные директории и пакеты

нет данных

### Entry point candidates

нет данных

### Test, fixture и test asset candidates

- `entry:index` (`fixture`)
- `entry:main` (`fixture`)
- `python_pkg` (`fixture`)
- `src` (`fixture`)
- `core` (`test_asset`)
- `tests` (`test_asset`)

## Границы анализа

- Поддержанные языки: javascript, python, typescript
- Обнаруженные поддержанные языки: javascript, python, typescript
- Глубоко проанализированных файлов: 8
- Поверхностно проиндексированных файлов: 20
- `unsupported_deep_extensions`: .bin, .custom, .json, .md, .txt, .yaml

### Ограничения текущего анализа

- Dynamic imports and runtime-generated dependencies are not resolved.
- Fixture/sample files were included in inventory.
- JavaScript/TypeScript entity extraction is regex-based and approximate.
- Unknown file types are indexed without deep semantic analysis.

## Наблюдения по данным inventory

- `README.md` не обнаружен в inventory.
- Render-слой строит документацию только по JSON-артефактам анализа и не перечитывает исходные файлы.

## Что не удалось определить автоматически

- Бизнес-назначение проекта.
- Runtime-поведение.
- Внешние API.
- Побочные эффекты.
- Динамические импорты.
- Сценарии запуска, если они не представлены в JSON.
