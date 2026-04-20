# Файл: python_pkg/core.py

## Сущности

| name | entity_type | type | parent/container | signature | parameters | return_annotation | is_async | exported | docstring/jsdoc | confidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Greeter | class | class | python_pkg.core | class Greeter | нет данных | нет данных | false | нет данных | Simple greeting service. | high |
| __init__ | method | method | Greeter / Greeter | def __init__(self, name: str) -> None | self, name: str | None | false | нет данных | нет данных | high |
| build_message | function | function | python_pkg.core | def build_message(name: str) -> str | name: str | str | false | нет данных | Build a user-facing greeting message. | high |
| compute_async | function | function | python_pkg.core | async def compute_async(value: int) -> int | value: int | int | true | нет данных | нет данных | high |
| greet | method | method | Greeter / Greeter | def greet(self) -> str | self | str | false | нет данных | нет данных | high |

## Импорты

| imported | dependency_type | resolved_file |
| --- | --- | --- |
| os | stdlib | нет данных |
| python_pkg.helpers | internal | python_pkg/helpers.py |

## Участвует в модулях

- [entry:main](../modules/module-fixture-entry-main.md) (`fixture`, `test`)
- [python_pkg](../modules/module-fixture-python-pkg.md) (`fixture`, `test`)
- [core](../modules/module-test-asset-core.md) (`test_asset`, `test`)
- [tests](../modules/module-test-asset-tests.md) (`test_asset`, `test`)
