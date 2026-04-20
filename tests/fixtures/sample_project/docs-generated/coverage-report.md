# Покрытие анализа

| field | value |
| --- | --- |
| indexed_file_count | 28 |
| deep_analyzed_file_count | 8 |
| shallow_indexed_file_count | 20 |
| unsupported_deep_extensions | .bin, .custom, .json, .md, .txt, .yaml |
| supported_languages | javascript, python, typescript |
| detected_supported_languages | javascript, python, typescript |
| unresolved_import_count | 0 |
| low_confidence_entity_count | 0 |
| limitations | Dynamic imports and runtime-generated dependencies are not resolved., Fixture/sample files were included in inventory., JavaScript/TypeScript entity extraction is regex-based and approximate., Unknown file types are indexed without deep semantic analysis. |

## Что эта документация не гарантирует

- Dynamic imports не гарантируются.
- Runtime-generated dependencies не гарантируются.
- Regex-based JS/TS extraction может быть приблизительным.
- Unknown file types индексируются поверхностно.
- Бизнес-смысл не выводится надежно без ручного описания или LLM-слоя.
