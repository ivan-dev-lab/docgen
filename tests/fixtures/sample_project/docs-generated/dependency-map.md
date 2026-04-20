# Карта зависимостей

## Сводка по `dependency_type`

- `internal`: 6
- `stdlib`: 1
- `node_builtin`: 1
- `third_party`: 1
- `unresolved`: 0
- `unknown`: 0

## Все импорты

| source_file | imported | dependency_type | resolved_file |
| --- | --- | --- | --- |
| main.py | python_pkg.core | internal | python_pkg/core.py |
| python_pkg/__init__.py | python_pkg.core | internal | python_pkg/core.py |
| python_pkg/core.py | os | stdlib | нет данных |
| python_pkg/core.py | python_pkg.helpers | internal | python_pkg/helpers.py |
| src/index.ts | ./lib/math | internal | src/lib/math.ts |
| src/index.ts | ./utils/logger | internal | src/utils/logger.js |
| src/index.ts | react | third_party | нет данных |
| src/utils/logger.js | fs | node_builtin | нет данных |
| tests/test_core.py | python_pkg.core | internal | python_pkg/core.py |

## Third-party dependencies

| name | version | manifest_type | ecosystem | source_file |
| --- | --- | --- | --- | --- |
| react | ^18.2.0 | package.json:dependencies | node | package.json |
| requests | ==2.31.0 | requirements.txt | python | requirements.txt |
| rich | >=13.7.0 | requirements.txt | python | requirements.txt |
| vitest | ^1.6.0 | package.json:devDependencies | node | package.json |

## Unresolved and unknown imports

нет данных

> Динамические импорты и runtime-generated dependencies могут отсутствовать.
