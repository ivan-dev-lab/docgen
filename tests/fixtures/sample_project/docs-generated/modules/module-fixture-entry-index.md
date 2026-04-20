# Модуль: entry:index

## Тип

fixture

## Роль страницы

test

## Назначение

Назначение не определено автоматически. Ниже приведена структурная информация, извлеченная из JSON-артефактов анализа.

## Навигация

- [Индекс функций](../functions/function-index.md)
- [Индекс файлов](../files/index.md)

## Структурная сводка

Это тестовый/примерный артефакт, а не production-модуль.

- Test files: нет данных
- Related production/source files: нет данных
- Количество сущностей: 10
- Количество импортов: 4

## Границы ответственности

- `source_files`: src/index.ts, src/lib/math.ts, src/utils/logger.js
- `test_files`: нет данных
- `config_files`: нет данных
- `doc_files`: нет данных
- `other_files`: нет данных
- `related_files`: нет данных

## Ключевые файлы

| category | file |
| --- | --- |
| source_files | [src/index.ts](../files/file-src-index-ts.md) |
| source_files | [src/lib/math.ts](../files/file-src-lib-math-ts.md) |
| source_files | [src/utils/logger.js](../files/file-src-utils-logger-js.md) |

## Ключевые сущности

| name | entity_type | type | file | parent/container | signature | parameters | return_annotation | is_async | exported | docstring/jsdoc | confidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Application | class | class | src/index.ts | нет данных | class Application | нет данных | нет данных | false | true | нет данных | medium |
| InternalCalculator | class | class | src/lib/math.ts | нет данных | class InternalCalculator | нет данных | нет данных | false | true | нет данных | medium |
| Logger | class | class | src/utils/logger.js | нет данных | class Logger | нет данных | нет данных | false | true | нет данных | medium |
| bootstrapApp | function | function | src/index.ts | нет данных | export function bootstrapApp(): number | нет данных | number | false | true | Boot the sample application. | medium |
| add | function | function | src/lib/math.ts | нет данных | export function add(a: number, b: number): number | a: number, b: number | number | false | true | нет данных | medium |
| multiply | function | function | src/lib/math.ts | нет данных | export const multiply = (a: number, b: number): number => | a: number, b: number | number | false | true | нет данных | medium |
| createLogger | function | function | src/utils/logger.js | нет данных | export const createLogger = () => | нет данных | нет данных | false | true | Create a logger instance. | medium |
| run | method | method | src/index.ts | Application / Application | run(): number | нет данных | number | false | нет данных | нет данных | medium |
| square | method | method | src/lib/math.ts | InternalCalculator / InternalCalculator | square(value: number): number | value: number | number | false | нет данных | нет данных | medium |
| log | method | method | src/utils/logger.js | Logger / Logger | log(message) | message | нет данных | false | нет данных | нет данных | medium |

## Зависимости

| source_file | imported | dependency_type | resolved_file |
| --- | --- | --- | --- |
| src/index.ts | ./lib/math | internal | src/lib/math.ts |
| src/index.ts | ./utils/logger | internal | src/utils/logger.js |
| src/index.ts | react | third_party | нет данных |
| src/utils/logger.js | fs | node_builtin | нет данных |

## Связи с другими модулями

| source_file | resolved_file | target_modules | ambiguous |
| --- | --- | --- | --- |
| src/index.ts | src/lib/math.ts | src | true |
| src/index.ts | src/utils/logger.js | src | true |

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
