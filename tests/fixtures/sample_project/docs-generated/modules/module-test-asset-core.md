# Модуль: core

## Тип

test_asset

## Роль страницы

test

## Назначение

Назначение не определено автоматически. Ниже приведена структурная информация, извлеченная из JSON-артефактов анализа.

## Навигация

- [Индекс функций](../functions/function-index.md)
- [Индекс файлов](../files/index.md)

## Структурная сводка

Это тестовый/примерный артефакт, а не production-модуль.

- Test files: [tests/test_core.py](../files/file-tests-test-core-py.md)
- Related production/source files: нет данных
- Количество сущностей: 6
- Количество импортов: 3

## Границы ответственности

- `source_files`: python_pkg/core.py
- `test_files`: tests/test_core.py
- `config_files`: нет данных
- `doc_files`: нет данных
- `other_files`: нет данных
- `related_files`: нет данных

## Ключевые файлы

| category | file |
| --- | --- |
| source_files | [python_pkg/core.py](../files/file-python-pkg-core-py.md) |
| test_files | [tests/test_core.py](../files/file-tests-test-core-py.md) |

## Ключевые сущности

| name | entity_type | type | file | parent/container | signature | parameters | return_annotation | is_async | exported | docstring/jsdoc | confidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Greeter | class | class | python_pkg/core.py | python_pkg.core | class Greeter | нет данных | нет данных | false | нет данных | Simple greeting service. | high |
| build_message | function | function | python_pkg/core.py | python_pkg.core | def build_message(name: str) -> str | name: str | str | false | нет данных | Build a user-facing greeting message. | high |
| compute_async | function | function | python_pkg/core.py | python_pkg.core | async def compute_async(value: int) -> int | value: int | int | true | нет данных | нет данных | high |
| __init__ | method | method | python_pkg/core.py | Greeter / Greeter | def __init__(self, name: str) -> None | self, name: str | None | false | нет данных | нет данных | high |
| greet | method | method | python_pkg/core.py | Greeter / Greeter | def greet(self) -> str | self | str | false | нет данных | нет данных | high |
| test_greet | function | function | tests/test_core.py | tests.test_core | def test_greet() -> None | нет данных | None | false | нет данных | нет данных | high |

## Зависимости

| source_file | imported | dependency_type | resolved_file |
| --- | --- | --- | --- |
| python_pkg/core.py | os | stdlib | нет данных |
| python_pkg/core.py | python_pkg.helpers | internal | python_pkg/helpers.py |
| tests/test_core.py | python_pkg.core | internal | python_pkg/core.py |

## Связи с другими модулями

| source_file | resolved_file | target_modules | ambiguous |
| --- | --- | --- | --- |
| python_pkg/core.py | python_pkg/helpers.py | entry:main, python_pkg | true |
| tests/test_core.py | python_pkg/core.py | entry:main, python_pkg, tests | true |

## Предупреждения и ограничения

- Dynamic imports and runtime-generated dependencies are not resolved.
- Fixture/sample files were included in inventory.
- JavaScript/TypeScript entity extraction is regex-based and approximate.
- Unknown file types are indexed without deep semantic analysis.
- candidate is not suitable as a production module without fixture-aware filtering
- Это не production-модуль, а тестовый/примерный артефакт.

## Что требует ручного уточнения

- Бизнес-назначение модуля.
- Runtime-поведение.
- Внешние API.
- Побочные эффекты.
- Сценарии использования.
