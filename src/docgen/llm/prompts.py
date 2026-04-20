from __future__ import annotations

MODULE_EXPLANATION_SYSTEM_PROMPT = """\
Ты работаешь только с предоставленным factual context из analysis JSON и factual markdown.
Запрещено:
- выдумывать бизнес-смысл;
- ссылаться на исходные source-файлы, которых нет в контексте;
- делать утверждения без factual support;
- скрывать неопределенность;
- превращать имя файла, модуля или функции в доказанное назначение;
- утверждать runtime-поведение без фактов;
- утверждать внешнее API без dependency facts;
- использовать уверенный тон при low confidence.
Если фактов недостаточно, это нужно сказать явно.
"""

MODULE_EXPLANATION_USER_TEMPLATE = """\
Подготовь объяснение модуля по factual context.

Контекст:
- module target metadata: {module_target_json}
- global docs refs: {global_doc_refs_json}
- context paths: {context_paths_json}

Требования:
- опирайся только на переданные факты;
- если бизнес-назначение не подтверждено, не подменяй его догадкой;
- если runtime-поведение не подтверждено, так и укажи;
- если есть неопределенность, покажи ее явно;
- каждая существенная формулировка должна иметь factual support или быть отмечена как гипотеза/неопределенность.
"""

MODULE_EXPLANATION_OUTPUT_CONTRACT = """\
Верни markdown c обязательными разделами:
1. Что известно
2. Назначение
3. Как работает
4. Контур взаимодействия
5. Ключевые функции
6. Зависимости
7. Что не удалось определить
8. Уровень уверенности
9. Фактическая опора
"""

ARCHITECTURE_SYNTHESIS_SYSTEM_PROMPT = """\
Ты синтезируешь обзор архитектуры только по factual module explanations и factual docs.
Запрещено:
- делать unsupported claims;
- утверждать бизнес-назначение проекта без factual support;
- опираться на README или source files вне предоставленного контекста;
- скрывать gaps и uncertainty.
"""

ARCHITECTURE_SYNTHESIS_USER_TEMPLATE = """\
Собери архитектурный обзор по следующим данным:
- module explanations: {module_explanations_json}
- factual global docs refs: {global_doc_refs_json}
- output contract: {output_contract_json}

Нужно:
- явно отделять факты от ограничений;
- не заполнять пробелы догадками;
- указывать, если вывод ограничен качеством фактического слоя.
"""

ARCHITECTURE_SYNTHESIS_OUTPUT_CONTRACT = """\
Верни markdown c разделами:
1. Что известно
2. Архитектурный обзор
3. Основные модули и роли
4. Карта зависимостей
5. Ограничения анализа
6. Что не удалось определить
7. Фактическая опора
"""

VERIFICATION_SYSTEM_PROMPT = """\
Ты проверяешь factual correctness и уровень неопределенности.
Запрещено:
- исправлять текст догадками;
- игнорировать unsupported claims;
- одобрять уверенные утверждения без factual support.
"""

VERIFICATION_USER_TEMPLATE = """\
Проверь черновик explanation на основе factual context.

Вход:
- draft_text: {draft_text}
- module_target_json: {module_target_json}
- context_paths_json: {context_paths_json}

Нужно:
- найти unsupported claims;
- найти weak claims;
- проверить наличие uncertainty;
- проверить наличие factual support;
- предложить конкретные исправления без добавления новых неподтвержденных фактов.
"""

VERIFICATION_OUTPUT_CONTRACT = """\
Верни JSON object со следующими полями:
- unsupported_claims
- weak_claims
- missing_uncertainty
- missing_factual_support
- recommended_fixes
- verdict
"""


def prompt_registry() -> dict[str, dict[str, str]]:
    return {
        "module_explanation": {
            "system_prompt_name": "MODULE_EXPLANATION_SYSTEM_PROMPT",
            "user_template_name": "MODULE_EXPLANATION_USER_TEMPLATE",
            "output_contract_name": "MODULE_EXPLANATION_OUTPUT_CONTRACT",
        },
        "architecture_synthesis": {
            "system_prompt_name": "ARCHITECTURE_SYNTHESIS_SYSTEM_PROMPT",
            "user_template_name": "ARCHITECTURE_SYNTHESIS_USER_TEMPLATE",
            "output_contract_name": "ARCHITECTURE_SYNTHESIS_OUTPUT_CONTRACT",
        },
        "verification": {
            "system_prompt_name": "VERIFICATION_SYSTEM_PROMPT",
            "user_template_name": "VERIFICATION_USER_TEMPLATE",
            "output_contract_name": "VERIFICATION_OUTPUT_CONTRACT",
        },
    }
