# Индекс функций, классов и методов

## main.py

- File page: [../files/file-main-py.md](../files/file-main-py.md)
- Количество сущностей: 1

| name | entity_type | type | file | parent/container | signature | parameters | return_annotation | is_async | exported | docstring/jsdoc | confidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| run | function | function | main.py | main | def run() -> str | нет данных | str | false | нет данных | нет данных | high |

## python_pkg/core.py

- File page: [../files/file-python-pkg-core-py.md](../files/file-python-pkg-core-py.md)
- Количество сущностей: 5

| name | entity_type | type | file | parent/container | signature | parameters | return_annotation | is_async | exported | docstring/jsdoc | confidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Greeter | class | class | python_pkg/core.py | python_pkg.core | class Greeter | нет данных | нет данных | false | нет данных | Simple greeting service. | high |
| build_message | function | function | python_pkg/core.py | python_pkg.core | def build_message(name: str) -> str | name: str | str | false | нет данных | Build a user-facing greeting message. | high |
| compute_async | function | function | python_pkg/core.py | python_pkg.core | async def compute_async(value: int) -> int | value: int | int | true | нет данных | нет данных | high |
| __init__ | method | method | python_pkg/core.py | Greeter / Greeter | def __init__(self, name: str) -> None | self, name: str | None | false | нет данных | нет данных | high |
| greet | method | method | python_pkg/core.py | Greeter / Greeter | def greet(self) -> str | self | str | false | нет данных | нет данных | high |

## python_pkg/helpers.py

- File page: [../files/file-python-pkg-helpers-py.md](../files/file-python-pkg-helpers-py.md)
- Количество сущностей: 1

| name | entity_type | type | file | parent/container | signature | parameters | return_annotation | is_async | exported | docstring/jsdoc | confidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| normalize_name | function | function | python_pkg/helpers.py | python_pkg.helpers | def normalize_name(name: str) -> str | name: str | str | false | нет данных | нет данных | high |

## src/index.ts

- File page: [../files/file-src-index-ts.md](../files/file-src-index-ts.md)
- Количество сущностей: 3

| name | entity_type | type | file | parent/container | signature | parameters | return_annotation | is_async | exported | docstring/jsdoc | confidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Application | class | class | src/index.ts | нет данных | class Application | нет данных | нет данных | false | true | нет данных | medium |
| bootstrapApp | function | function | src/index.ts | нет данных | export function bootstrapApp(): number | нет данных | number | false | true | Boot the sample application. | medium |
| run | method | method | src/index.ts | Application / Application | run(): number | нет данных | number | false | нет данных | нет данных | medium |

## src/lib/math.ts

- File page: [../files/file-src-lib-math-ts.md](../files/file-src-lib-math-ts.md)
- Количество сущностей: 4

| name | entity_type | type | file | parent/container | signature | parameters | return_annotation | is_async | exported | docstring/jsdoc | confidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| InternalCalculator | class | class | src/lib/math.ts | нет данных | class InternalCalculator | нет данных | нет данных | false | true | нет данных | medium |
| add | function | function | src/lib/math.ts | нет данных | export function add(a: number, b: number): number | a: number, b: number | number | false | true | нет данных | medium |
| multiply | function | function | src/lib/math.ts | нет данных | export const multiply = (a: number, b: number): number => | a: number, b: number | number | false | true | нет данных | medium |
| square | method | method | src/lib/math.ts | InternalCalculator / InternalCalculator | square(value: number): number | value: number | number | false | нет данных | нет данных | medium |

## src/utils/logger.js

- File page: [../files/file-src-utils-logger-js.md](../files/file-src-utils-logger-js.md)
- Количество сущностей: 3

| name | entity_type | type | file | parent/container | signature | parameters | return_annotation | is_async | exported | docstring/jsdoc | confidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Logger | class | class | src/utils/logger.js | нет данных | class Logger | нет данных | нет данных | false | true | нет данных | medium |
| createLogger | function | function | src/utils/logger.js | нет данных | export const createLogger = () => | нет данных | нет данных | false | true | Create a logger instance. | medium |
| log | method | method | src/utils/logger.js | Logger / Logger | log(message) | message | нет данных | false | нет данных | нет данных | medium |

## tests/test_core.py

- File page: [../files/file-tests-test-core-py.md](../files/file-tests-test-core-py.md)
- Количество сущностей: 1

| name | entity_type | type | file | parent/container | signature | parameters | return_annotation | is_async | exported | docstring/jsdoc | confidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| test_greet | function | function | tests/test_core.py | tests.test_core | def test_greet() -> None | нет данных | None | false | нет данных | нет данных | high |
