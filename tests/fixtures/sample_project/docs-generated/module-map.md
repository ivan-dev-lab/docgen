# Карта модулей

## Сводка

| name | type | module_page_role | confidence | doc | source_files | test_files |
| --- | --- | --- | --- | --- | --- | --- |
| entry:index | fixture | test | high | [modules/module-fixture-entry-index.md](modules/module-fixture-entry-index.md) | 3 | 0 |
| entry:main | fixture | test | high | [modules/module-fixture-entry-main.md](modules/module-fixture-entry-main.md) | 3 | 0 |
| python_pkg | fixture | test | high | [modules/module-fixture-python-pkg.md](modules/module-fixture-python-pkg.md) | 3 | 0 |
| src | fixture | test | medium | [modules/module-fixture-src.md](modules/module-fixture-src.md) | 3 | 0 |
| core | test_asset | test | high | [modules/module-test-asset-core.md](modules/module-test-asset-core.md) | 1 | 1 |
| tests | test_asset | test | medium | [modules/module-test-asset-tests.md](modules/module-test-asset-tests.md) | 0 | 1 |

## entry:index

- `type`: `fixture`
- `module_page_role`: `test`
- `confidence`: `high`
- Документ: [modules/module-fixture-entry-index.md](modules/module-fixture-entry-index.md)
- Это тестовый/примерный артефакт, а не production-модуль.

### Файлы по категориям

- `source_files`: src/index.ts, src/lib/math.ts, src/utils/logger.js
- `test_files`: нет данных
- `config_files`: нет данных
- `doc_files`: нет данных
- `other_files`: нет данных
- `related_files`: нет данных

### Related files

нет данных

### Relations

нет данных

### Reasons

- candidate includes 3 source file(s) reachable via internal imports
- candidate is dominated by fixture/sample files (100%)
- seeded from possible entry point 'src/index.ts'

### Warnings

- candidate is not suitable as a production module without fixture-aware filtering

## entry:main

- `type`: `fixture`
- `module_page_role`: `test`
- `confidence`: `high`
- Документ: [modules/module-fixture-entry-main.md](modules/module-fixture-entry-main.md)
- Это тестовый/примерный артефакт, а не production-модуль.

### Файлы по категориям

- `source_files`: main.py, python_pkg/core.py, python_pkg/helpers.py
- `test_files`: нет данных
- `config_files`: нет данных
- `doc_files`: нет данных
- `other_files`: нет данных
- `related_files`: tests/test_core.py

### Related files

- tests/test_core.py

### Relations

| relation_type | source | target | confidence | reason |
| --- | --- | --- | --- | --- |
| tests | tests/test_core.py | python_pkg/core.py | high | test file imports the source file directly |

### Reasons

- candidate includes 3 source file(s) reachable via internal imports
- candidate is dominated by fixture/sample files (100%)
- seeded from possible entry point 'main.py'

### Warnings

- candidate is not suitable as a production module without fixture-aware filtering

## python_pkg

- `type`: `fixture`
- `module_page_role`: `test`
- `confidence`: `high`
- Документ: [modules/module-fixture-python-pkg.md](modules/module-fixture-python-pkg.md)
- Это тестовый/примерный артефакт, а не production-модуль.

### Файлы по категориям

- `source_files`: python_pkg/__init__.py, python_pkg/core.py, python_pkg/helpers.py
- `test_files`: нет данных
- `config_files`: нет данных
- `doc_files`: нет данных
- `other_files`: нет данных
- `related_files`: tests/test_core.py

### Related files

- tests/test_core.py

### Relations

| relation_type | source | target | confidence | reason |
| --- | --- | --- | --- | --- |
| tests | tests/test_core.py | python_pkg/core.py | high | test file imports the source file directly |

### Reasons

- candidate contains 2 internal import edge(s) among included files
- candidate includes 3 source file(s) under the package subtree
- candidate is dominated by fixture/sample files (100%)
- package directory 'python_pkg' contains __init__.py

### Warnings

- candidate is not suitable as a production module without fixture-aware filtering

## src

- `type`: `fixture`
- `module_page_role`: `test`
- `confidence`: `medium`
- Документ: [modules/module-fixture-src.md](modules/module-fixture-src.md)
- Это тестовый/примерный артефакт, а не production-модуль.

### Файлы по категориям

- `source_files`: src/index.ts, src/lib/math.ts, src/utils/logger.js
- `test_files`: нет данных
- `config_files`: нет данных
- `doc_files`: нет данных
- `other_files`: нет данных
- `related_files`: нет данных

### Related files

нет данных

### Relations

нет данных

### Reasons

- candidate contains 2 internal import edge(s) among included files
- candidate covers subtree 'src'
- candidate includes 3 source file(s) and 0 test file(s)
- candidate includes at least one possible entry point
- candidate is dominated by fixture/sample files (100%)

### Warnings

- candidate is not suitable as a production module without fixture-aware filtering

## core

- `type`: `test_asset`
- `module_page_role`: `test`
- `confidence`: `high`
- Документ: [modules/module-test-asset-core.md](modules/module-test-asset-core.md)
- Это тестовый/примерный артефакт, а не production-модуль.

### Файлы по категориям

- `source_files`: python_pkg/core.py
- `test_files`: tests/test_core.py
- `config_files`: нет данных
- `doc_files`: нет данных
- `other_files`: нет данных
- `related_files`: нет данных

### Related files

нет данных

### Relations

| relation_type | source | target | confidence | reason |
| --- | --- | --- | --- | --- |
| tests | tests/test_core.py | python_pkg/core.py | high | test file imports the source file directly |

### Reasons

- candidate includes 1 source file(s) and 1 test file(s)
- candidate is dominated by fixture/sample files (100%)
- candidate links 1 test file(s) to the production target 'python_pkg/core.py'

### Warnings

- candidate is not suitable as a production module without fixture-aware filtering

## tests

- `type`: `test_asset`
- `module_page_role`: `test`
- `confidence`: `medium`
- Документ: [modules/module-test-asset-tests.md](modules/module-test-asset-tests.md)
- Это тестовый/примерный артефакт, а не production-модуль.

### Файлы по категориям

- `source_files`: нет данных
- `test_files`: tests/test_core.py
- `config_files`: нет данных
- `doc_files`: нет данных
- `other_files`: нет данных
- `related_files`: python_pkg/core.py

### Related files

- python_pkg/core.py

### Relations

| relation_type | source | target | confidence | reason |
| --- | --- | --- | --- | --- |
| tests | tests/test_core.py | python_pkg/core.py | high | test file imports the source file directly |

### Reasons

- candidate covers subtree 'tests'
- candidate includes 0 source file(s) and 1 test file(s)
- candidate is dominated by fixture/sample files (100%)

### Warnings

- candidate is not suitable as a production module without fixture-aware filtering
- directory candidate is shallow and mostly rooted in layout
