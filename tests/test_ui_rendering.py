from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import docgen.ui_rendering as ui_rendering  # noqa: E402
from docgen.ui_rendering import (  # noqa: E402
    MARKDOWN_EXTENSIONS,
    render_artifact_content,
    render_markdown,
    sanitize_html,
    rewrite_internal_links,
)


class UiRenderingTests(unittest.TestCase):
    def make_temp_dir(self) -> Path:
        temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temp_directory.cleanup)
        return Path(temp_directory.name)

    def test_headings_render_to_heading_tags(self) -> None:
        html = render_markdown("# One\n\n## Two\n\n### Three")

        self.assertIn("<h1>One</h1>", html)
        self.assertIn("<h2>Two</h2>", html)
        self.assertIn("<h3>Three</h3>", html)

    def test_markdown_table_renders_to_table_cells(self) -> None:
        html = render_markdown("| A | B |\n|---|---|\n| 1 | 2 |")

        self.assertIn("<table>", html)
        self.assertIn("<th>A</th>", html)
        self.assertIn("<td>1</td>", html)

    def test_fenced_code_renders_to_pre_code(self) -> None:
        html = render_markdown("```python\nprint(1)\n```")

        self.assertIn("<pre><code", html)
        self.assertIn("language-python", html)
        self.assertIn("print(1)", html)

    def test_fenced_code_with_markdown_link_stays_plain_text(self) -> None:
        html = render_markdown(
            '```python\nprint("[file](../files/file-src-docgen-ui-server-py.md)")\n```',
            base_artifact_path="docs/generated/modules/module-package-docgen.md",
        )

        self.assertIn("<pre><code", html)
        self.assertIn('[file](../files/file-src-docgen-ui-server-py.md)', html)
        self.assertNotIn("<a ", html)
        self.assertNotIn("/file?path=", html)

    def test_trailing_unclosed_fenced_code_with_markdown_link_stays_plain_text(self) -> None:
        html = render_markdown(
            '```python\nprint("[file](../files/file-src-docgen-ui-server-py.md)")',
            base_artifact_path="docs/generated/modules/module-package-docgen.md",
        )

        self.assertIn("<pre><code", html)
        self.assertIn('[file](../files/file-src-docgen-ui-server-py.md)', html)
        self.assertNotIn("<a ", html)
        self.assertNotIn("/file?path=", html)

    def test_inline_code_renders_to_code(self) -> None:
        html = render_markdown("Use `docgen` now.")

        self.assertIn("<code>docgen</code>", html)

    def test_bullet_list_renders_to_ul_li(self) -> None:
        html = render_markdown("- alpha\n- beta")

        self.assertIn("<ul>", html)
        self.assertIn("<li>alpha</li>", html)
        self.assertIn("<li>beta</li>", html)

    def test_ordered_list_renders_to_ol_li(self) -> None:
        html = render_markdown("1. alpha\n2. beta")

        self.assertIn("<ol>", html)
        self.assertIn("<li>alpha</li>", html)
        self.assertIn("<li>beta</li>", html)

    def test_blockquote_renders_to_blockquote(self) -> None:
        html = render_markdown("> quoted text")

        self.assertIn("<blockquote>", html)
        self.assertIn("<p>quoted text</p>", html)

    def test_horizontal_rule_renders_to_hr(self) -> None:
        html = render_markdown("before\n\n---\n\nafter")

        self.assertIn("<hr", html)
        self.assertIn("<p>before</p>", html)
        self.assertIn("<p>after</p>", html)

    def test_markdown_link_renders_to_safe_anchor(self) -> None:
        html = render_markdown('[Docs](https://example.com/docs "External docs")')

        self.assertIn('<a href="https://example.com/docs"', html)
        self.assertIn('title="External docs"', html)
        self.assertIn('rel="noopener noreferrer"', html)

    def test_script_tag_is_removed(self) -> None:
        html = render_markdown("<script>alert(1)</script>\n\nSafe text.")

        self.assertNotIn("<script", html.lower())
        self.assertNotIn("</script", html.lower())
        self.assertIn("Safe text.", html)

    def test_script_content_is_not_preserved_as_active_content(self) -> None:
        html = render_markdown("<script>alert(1)</script>")

        self.assertNotIn("script", html.lower())
        self.assertNotIn("alert(1)", html)

    def test_style_tag_and_content_are_removed(self) -> None:
        html = render_markdown("<style>body{display:none}</style><p>visible</p>")

        self.assertNotIn("<style", html.lower())
        self.assertNotIn("display:none", html)
        self.assertIn("<p>visible</p>", html)

    def test_embedded_forbidden_tags_and_content_are_removed(self) -> None:
        html = render_markdown(
            '<iframe src="x">frame</iframe>'
            '<object>object</object>'
            '<embed src="x"></embed>'
            '<svg><text>svg</text></svg>'
            '<math>math</math>'
        )

        lowered = html.lower()
        for value in ("iframe", "object", "embed", "svg", "math", "frame", "object", "embed"):
            self.assertNotIn(value, lowered)

    def test_javascript_href_is_removed(self) -> None:
        html = render_markdown("[bad](javascript:alert(1))")

        self.assertNotIn("javascript", html.lower())
        self.assertNotIn("href=", html.lower())

    def test_data_and_vbscript_hrefs_are_removed(self) -> None:
        html = render_markdown("[data](data:text/html,evil) [vb](vbscript:evil)")

        lowered = html.lower()
        self.assertNotIn("data:text", lowered)
        self.assertNotIn("vbscript", lowered)
        self.assertNotIn("href=", lowered)

    def test_event_handler_attributes_are_removed(self) -> None:
        html = render_markdown('<a href="https://example.com" onclick="evil()">x</a><img src=x onerror="evil()">')

        self.assertNotIn("onclick", html.lower())
        self.assertNotIn("onerror", html.lower())
        self.assertNotIn("<img", html.lower())
        self.assertIn('href="https://example.com"', html)

    def test_sanitizer_keeps_table_tags(self) -> None:
        html = sanitize_html("<table><thead><tr><th>A</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table>")

        self.assertIn("<table>", html)
        self.assertIn("<thead>", html)
        self.assertIn("<tbody>", html)
        self.assertIn("<th>A</th>", html)
        self.assertIn("<td>1</td>", html)

    def test_sanitizer_keeps_pre_and_code_tags(self) -> None:
        html = sanitize_html('<pre class="x"><code class="language-python">print(1)</code></pre>')

        self.assertIn("<pre", html)
        self.assertIn("<code", html)
        self.assertIn("language-python", html)

    def test_code_block_survives_sanitizer_after_render_markdown(self) -> None:
        html = render_markdown("```python\nprint(1)\n```")

        self.assertIn("<pre><code", html)
        self.assertIn("print(1)", html)

    def test_relative_markdown_link_is_rewritten_to_artifact_route(self) -> None:
        root = self.make_temp_dir()
        base = root / "docs" / "generated" / "modules" / "current.md"
        base.parent.mkdir(parents=True)
        html = render_markdown("[Other](other.md)", base_artifact_path=str(base))

        self.assertIn('/artifact?path=docs%2Fgenerated%2Fmodules%2Fother.md', html)

    def test_relative_generated_file_doc_link_is_rewritten_to_file_route(self) -> None:
        root = self.make_temp_dir()
        base = root / "docs" / "generated" / "modules" / "module-package-docgen.md"
        base.parent.mkdir(parents=True)
        html = render_markdown("[UI server](../files/file-src-docgen-ui-server-py.md)", base_artifact_path=str(base))

        self.assertIn('/file?path=docs%2Fgenerated%2Ffiles%2Ffile-src-docgen-ui-server-py.md', html)
        self.assertNotIn("/artifact?path=", html)

    def test_ordinary_paragraph_link_still_rewrites_after_code_regression(self) -> None:
        html = render_markdown(
            "See [file](../files/file-src-docgen-ui-server-py.md).",
            base_artifact_path="docs/generated/modules/module-package-docgen.md",
        )

        self.assertIn('/file?path=docs%2Fgenerated%2Ffiles%2Ffile-src-docgen-ui-server-py.md', html)

    def test_relative_generated_non_file_markdown_link_is_rewritten_to_artifact_route(self) -> None:
        root = self.make_temp_dir()
        base = root / "docs" / "generated" / "modules" / "module-package-docgen.md"
        base.parent.mkdir(parents=True)
        html = render_markdown("[Architecture](../architecture.md)", base_artifact_path=str(base))

        self.assertIn('/artifact?path=docs%2Fgenerated%2Farchitecture.md', html)

    def test_relative_enhanced_markdown_link_is_rewritten_to_artifact_route(self) -> None:
        root = self.make_temp_dir()
        base = root / "docs" / "enhanced" / "modules" / "module-package-llm.md"
        base.parent.mkdir(parents=True)
        html = render_markdown("[Verification](../verification/module-package-llm.verification.md)", base_artifact_path=str(base))

        self.assertIn('/artifact?path=docs%2Fenhanced%2Fverification%2Fmodule-package-llm.verification.md', html)

    def test_relative_ui_data_json_link_is_rewritten_to_artifact_route(self) -> None:
        root = self.make_temp_dir()
        base = root / "docs" / "generated" / "modules" / "module-package-docgen.md"
        base.parent.mkdir(parents=True)
        html = render_markdown("[Current state](../../ui-data/current-state.json)", base_artifact_path=str(base))

        self.assertIn('/artifact?path=docs%2Fui-data%2Fcurrent-state.json', html)

    def test_mailto_link_remains_safe(self) -> None:
        html = render_markdown("[Mail](mailto:docs@example.com)")

        self.assertIn('href="mailto:docs@example.com"', html)

    def test_file_scheme_href_is_removed(self) -> None:
        html = render_markdown("[file](file:///C:/secret.md)")

        self.assertNotIn("file:", html.lower())
        self.assertNotIn("href=", html.lower())

    def test_path_traversal_link_does_not_escape_allowed_roots(self) -> None:
        root = self.make_temp_dir()
        base = root / "docs" / "generated" / "modules" / "current.md"
        base.parent.mkdir(parents=True)
        html = render_markdown("[bad](../../../secret.md)", base_artifact_path=str(base))

        self.assertNotIn("secret.md", html)
        self.assertNotIn("href=", html.lower())

    def test_path_traversal_to_env_does_not_become_artifact_link(self) -> None:
        root = self.make_temp_dir()
        base = root / "docs" / "generated" / "modules" / "current.md"
        base.parent.mkdir(parents=True)
        html = render_markdown("[env](../../../../.env)", base_artifact_path=str(base))

        self.assertNotIn(".env", html)
        self.assertNotIn("/artifact?path=", html)
        self.assertNotIn("href=", html.lower())

    def test_windows_style_relative_path_is_normalized_to_ui_route(self) -> None:
        root = self.make_temp_dir()
        base = root / "docs" / "generated" / "modules" / "current.md"
        base.parent.mkdir(parents=True)
        html = rewrite_internal_links(
            '<a href="..\\files\\file-src-docgen-ui-server-py.md">UI server</a>',
            base_artifact_path=str(base),
        )

        self.assertIn('/file?path=docs%2Fgenerated%2Ffiles%2Ffile-src-docgen-ui-server-py.md', html)

    def test_absolute_filesystem_path_is_rejected(self) -> None:
        root = self.make_temp_dir()
        base = root / "docs" / "generated" / "modules" / "current.md"
        base.parent.mkdir(parents=True)
        html = rewrite_internal_links('<a href="C:\\Users\\secret.md">abs</a>', base_artifact_path=str(base))

        self.assertNotIn("href=", html.lower())

    def test_unc_path_is_rejected(self) -> None:
        root = self.make_temp_dir()
        base = root / "docs" / "generated" / "modules" / "current.md"
        base.parent.mkdir(parents=True)
        html = rewrite_internal_links('<a href="\\\\server\\share\\secret.md">unc</a>', base_artifact_path=str(base))

        self.assertNotIn("href=", html.lower())

    def test_anchor_fragment_is_preserved_for_internal_markdown_link(self) -> None:
        root = self.make_temp_dir()
        base = root / "docs" / "generated" / "modules" / "current.md"
        base.parent.mkdir(parents=True)
        html = render_markdown("[Architecture](../architecture.md#runtime-flow)", base_artifact_path=str(base))

        self.assertIn('/artifact?path=docs%2Fgenerated%2Farchitecture.md#runtime-flow', html)

    def test_spaces_and_special_characters_in_internal_path_are_encoded(self) -> None:
        root = self.make_temp_dir()
        base = root / "docs" / "generated" / "modules" / "current.md"
        base.parent.mkdir(parents=True)
        html = rewrite_internal_links(
            '<a href="../files/file with spaces &amp; symbols.md">File</a>',
            base_artifact_path=str(base),
        )

        self.assertIn("/file?path=docs%2Fgenerated%2Ffiles%2Ffile%20with%20spaces%20%26%20symbols.md", html)

    def test_already_rewritten_artifact_link_is_not_double_rewritten(self) -> None:
        html = render_markdown("[Architecture](/artifact?path=docs%2Fgenerated%2Farchitecture.md)")

        self.assertEqual(html.count("/artifact?path="), 1)
        self.assertIn('href="/artifact?path=docs%2Fgenerated%2Farchitecture.md"', html)

    def test_rewriting_preserves_markdown_table_structure(self) -> None:
        root = self.make_temp_dir()
        base = root / "docs" / "generated" / "modules" / "current.md"
        base.parent.mkdir(parents=True)
        html = render_markdown("| Link |\n|---|\n| [File](../files/x.md) |", base_artifact_path=str(base))

        self.assertIn("<table>", html)
        self.assertIn("<td>", html)
        self.assertIn('/file?path=docs%2Fgenerated%2Ffiles%2Fx.md', html)

    def test_table_link_still_rewrites_after_code_regression(self) -> None:
        html = render_markdown(
            "| Link |\n|---|\n| [file](../files/file-src-docgen-ui-server-py.md) |",
            base_artifact_path="docs/generated/modules/module-package-docgen.md",
        )

        self.assertIn("<table>", html)
        self.assertIn('/file?path=docs%2Fgenerated%2Ffiles%2Ffile-src-docgen-ui-server-py.md', html)

    def test_rewriting_preserves_code_block_structure(self) -> None:
        root = self.make_temp_dir()
        base = root / "docs" / "generated" / "modules" / "current.md"
        base.parent.mkdir(parents=True)
        html = render_markdown("```md\n[File](../files/x.md)\n```", base_artifact_path=str(base))

        self.assertIn("<pre><code", html)
        self.assertIn("[File](../files/x.md)", html)
        self.assertNotIn("/file?path=", html)

    def test_rewrite_without_base_does_not_guess_relative_markdown_links(self) -> None:
        html = rewrite_internal_links('<a href="other.md">Other</a>')

        self.assertIn('href="other.md"', html)
        self.assertNotIn("/artifact?path=", html)

    def test_render_artifact_content_renders_markdown_in_rendered_mode(self) -> None:
        root = self.make_temp_dir()
        artifact = root / "docs" / "generated" / "modules" / "sample.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("# Title\n\n[File](../files/file-src-docgen-ui-server-py.md)", encoding="utf-8")

        rendered = render_artifact_content(artifact, view="rendered")

        self.assertEqual(rendered.content_type, "text/markdown")
        self.assertEqual(rendered.view, "rendered")
        self.assertIn("# Title", rendered.raw_text)
        self.assertIn("<h1>Title</h1>", rendered.rendered_html)
        self.assertIn("/file?path=docs%2Fgenerated%2Ffiles%2Ffile-src-docgen-ui-server-py.md", rendered.rendered_html)

    def test_render_artifact_content_supports_raw_mode(self) -> None:
        root = self.make_temp_dir()
        artifact = root / "docs" / "generated" / "modules" / "sample.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("# Title\n\nText", encoding="utf-8")

        rendered = render_artifact_content(artifact, view="raw")

        self.assertEqual(rendered.view, "raw")
        self.assertIn("<pre", rendered.rendered_html)
        self.assertIn("# Title", rendered.rendered_html)
        self.assertNotIn("<h1>Title</h1>", rendered.rendered_html)

    def test_json_artifact_is_pretty_text_not_markdown(self) -> None:
        root = self.make_temp_dir()
        artifact = root / "docs" / "ui-data" / "sample.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_text('{"markdown":"# Title","html":"<script>alert(1)</script>"}', encoding="utf-8")

        rendered = render_artifact_content(artifact, view="rendered")

        self.assertEqual(rendered.content_type, "application/json")
        self.assertIn('"markdown": "# Title"', rendered.raw_text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered.rendered_html)
        self.assertNotIn("<h1>Title</h1>", rendered.rendered_html)
        self.assertNotIn("<script>", rendered.rendered_html)

    def test_render_markdown_returns_html_string_not_semantic_payload(self) -> None:
        html = render_markdown("# Title\n\nverdict: fail\nweak claims: 999")

        self.assertIsInstance(html, str)
        self.assertNotIsInstance(html, dict)
        self.assertNotIn("weak_claims_count", html)
        self.assertNotIn("verification_status", html)

    def test_renderer_exposes_no_semantic_verdict_status_count_helpers(self) -> None:
        public_names = {name for name in dir(ui_rendering) if not name.startswith("_")}
        forbidden_names = {
            "get_verdict_from_markdown",
            "count_weak_claims_from_markdown",
            "detect_status_from_markdown",
        }
        forbidden_fragments = ("verdict", "weak_claim", "unsupported_claim", "status")

        self.assertTrue(forbidden_names.isdisjoint(public_names))
        self.assertFalse(
            [
                name
                for name in public_names
                for fragment in forbidden_fragments
                if fragment in name.lower()
            ]
        )

    def test_parser_config_uses_expected_extensions_and_no_shared_markdown_instance(self) -> None:
        self.assertEqual(MARKDOWN_EXTENSIONS, ("tables", "fenced_code", "sane_lists"))
        shared_instances = [
            name
            for name, value in vars(ui_rendering).items()
            if isinstance(value, markdown.Markdown)
        ]

        self.assertEqual(shared_instances, [])


if __name__ == "__main__":
    unittest.main()
