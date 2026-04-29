"""Central display rendering helpers for Docgen UI artifacts.

Markdown is display-only. Semantic UI state must come from JSON/ui-data/manifests.
This module renders artifact content to safe HTML and must not parse markdown to
infer verdicts, statuses, counts, history state, problem state, or action state.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlsplit

import markdown
import nh3


DEFAULT_TEXT_LIMIT = 200_000
ALLOWED_DOC_ROOT_NAMES = ("generated", "enhanced", "ui-data")
ALLOWED_UI_ROUTES = frozenset({"/artifact", "/file"})
MARKDOWN_EXTENSIONS = ("tables", "fenced_code", "sane_lists")
ALLOWED_TAGS = frozenset(
    {
        "a",
        "blockquote",
        "br",
        "code",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "hr",
        "li",
        "ol",
        "p",
        "pre",
        "strong",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "ul",
    }
)
DROP_CONTENT_TAGS = frozenset({"script", "style", "iframe", "object", "embed", "svg", "math"})
ALLOWED_ATTRIBUTES = {
    "a": {"href", "rel", "title"},
    "code": {"class"},
    "pre": {"class"},
}
ALLOWED_URL_SCHEMES = frozenset({"http", "https", "mailto"})
TEXT_SUFFIXES = frozenset(
    {
        ".css",
        ".csv",
        ".html",
        ".js",
        ".json",
        ".log",
        ".md",
        ".py",
        ".toml",
        ".ts",
        ".txt",
        ".yaml",
        ".yml",
    }
)


@dataclass(frozen=True)
class RenderedArtifact:
    path: str
    content_type: str
    raw_text: str
    rendered_html: str
    view: str
    warnings: list[str]


def render_markdown(markdown_text: str, *, base_artifact_path: str | None = None) -> str:
    """Render markdown display content to safe HTML without deriving UI semantics."""
    # Use a fresh parser per call. The local UI server is threaded, and
    # markdown.Markdown instances are mutable during conversion.
    normalized_markdown = _close_trailing_unclosed_fence(markdown_text)
    rendered = markdown.markdown(
        normalized_markdown,
        extensions=list(MARKDOWN_EXTENSIONS),
        output_format="html",
    )
    rewritten = rewrite_internal_links(rendered, base_artifact_path=base_artifact_path)
    return sanitize_html(rewritten)


def sanitize_html(html_text: str) -> str:
    return nh3.clean(
        html_text,
        tags=ALLOWED_TAGS,
        clean_content_tags=DROP_CONTENT_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel=None,
        strip_comments=True,
    )


def rewrite_internal_links(html_text: str, *, base_artifact_path: str | None = None) -> str:
    parser = _InternalLinkRewriter(base_artifact_path)
    parser.feed(html_text)
    parser.close()
    return parser.output()


def render_artifact_content(path: Path, *, view: str = "rendered") -> RenderedArtifact:
    if view not in {"rendered", "raw"}:
        raise ValueError(f"Unsupported artifact view: {view}")

    warnings: list[str] = []
    raw_text, truncated = _read_display_text(path)
    if truncated:
        warnings.append(f"Artifact text truncated to {DEFAULT_TEXT_LIMIT} characters.")

    suffix = path.suffix.lower()
    if _looks_binary(raw_text) and suffix not in TEXT_SUFFIXES:
        warnings.append("Artifact appears to be binary or unsupported for text rendering.")
        return RenderedArtifact(
            path=_display_path(path),
            content_type="application/octet-stream",
            raw_text="",
            rendered_html=raw_pre("Binary or unsupported artifact cannot be displayed as text."),
            view=view,
            warnings=warnings,
        )

    if suffix == ".md" and view == "rendered":
        rendered_html = render_markdown(raw_text, base_artifact_path=str(path))
        content_type = "text/markdown"
    elif suffix == ".json":
        raw_text = _pretty_json(raw_text, warnings)
        rendered_html = raw_pre(raw_text)
        content_type = "application/json"
    else:
        rendered_html = raw_pre(raw_text)
        content_type = "text/markdown" if suffix == ".md" else "text/plain"

    return RenderedArtifact(
        path=_display_path(path),
        content_type=content_type,
        raw_text=raw_text,
        rendered_html=rendered_html,
        view=view,
        warnings=warnings,
    )


def raw_pre(text: str) -> str:
    return f'<pre class="artifact">{html.escape(text, quote=False)}</pre>'


class _InternalLinkRewriter(HTMLParser):
    def __init__(self, base_artifact_path: str | None) -> None:
        super().__init__(convert_charrefs=True)
        self.base_artifact_path = _resolve_optional_path(base_artifact_path)
        self.parts: list[str] = []
        self.code_depth = 0

    def output(self) -> str:
        return "".join(self.parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        self.parts.append(self._format_starttag(normalized_tag, attrs, closed=False))
        if normalized_tag in {"pre", "code"}:
            self.code_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(self._format_starttag(tag.lower(), attrs, closed=True))

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        self.parts.append(f"</{html.escape(tag, quote=True)}>")
        if normalized_tag in {"pre", "code"} and self.code_depth > 0:
            self.code_depth -= 1

    def handle_data(self, data: str) -> None:
        self.parts.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def _format_starttag(self, tag: str, attrs: list[tuple[str, str | None]], *, closed: bool) -> str:
        normalized_attrs = self._rewrite_attrs(tag.lower(), attrs)
        rendered_attrs = "".join(
            f' {html.escape(name, quote=True)}="{html.escape(value, quote=True)}"'
            for name, value in normalized_attrs
            if value is not None
        )
        slash = " /" if closed else ""
        return f"<{html.escape(tag, quote=True)}{rendered_attrs}{slash}>"

    def _rewrite_attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
        if self.code_depth > 0:
            return attrs
        if tag != "a":
            return attrs

        rewritten: list[tuple[str, str | None]] = []
        rel_present = False
        external = False
        for name, value in attrs:
            lowered = name.lower()
            if lowered == "href" and value is not None:
                href = _rewrite_href(value, self.base_artifact_path)
                if href is None:
                    continue
                external = _is_external_http_href(href)
                rewritten.append((name, href))
            elif lowered == "rel":
                rel_present = True
                rewritten.append((name, value))
            else:
                rewritten.append((name, value))
        if external and not rel_present:
            rewritten.append(("rel", "noopener noreferrer"))
        return rewritten


def _rewrite_href(href: str, base_artifact_path: Path | None) -> str | None:
    stripped = href.strip()
    if not stripped:
        return href
    if _is_windows_drive_path(stripped) or _is_unc_path(stripped):
        return None

    parsed = urlsplit(stripped)
    if parsed.scheme:
        return stripped if parsed.scheme.lower() in ALLOWED_URL_SCHEMES else None
    if parsed.netloc:
        return None
    if stripped.startswith("#"):
        return stripped
    if parsed.path.startswith("/"):
        return _safe_existing_ui_href(stripped)
    if base_artifact_path is None:
        return stripped

    link_path = _normalized_relative_link_path(parsed.path)
    if link_path is None:
        return None
    target_path = (base_artifact_path.parent / unquote(link_path)).resolve()
    rewritten = _artifact_ui_route(target_path, base_artifact_path)
    if rewritten is None:
        return None

    if parsed.fragment:
        rewritten += f"#{quote(parsed.fragment, safe='')}"
    return rewritten


def _close_trailing_unclosed_fence(markdown_text: str) -> str:
    active_marker: str | None = None
    active_length = 0
    for line in markdown_text.splitlines():
        stripped = line.lstrip(" ")
        if len(line) - len(stripped) > 3:
            continue
        marker = _fence_marker(stripped)
        if marker is None:
            continue
        marker_char, marker_length, rest = marker
        if active_marker is None:
            active_marker = marker_char
            active_length = marker_length
        elif marker_char == active_marker and marker_length >= active_length and rest.strip() == "":
            active_marker = None
            active_length = 0
    if active_marker is None:
        return markdown_text
    separator = "" if markdown_text.endswith(("\n", "\r")) else "\n"
    return f"{markdown_text}{separator}{active_marker * active_length}"


def _fence_marker(stripped_line: str) -> tuple[str, int, str] | None:
    if not stripped_line or stripped_line[0] not in {"`", "~"}:
        return None
    marker_char = stripped_line[0]
    marker_length = 0
    for character in stripped_line:
        if character != marker_char:
            break
        marker_length += 1
    if marker_length < 3:
        return None
    return marker_char, marker_length, stripped_line[marker_length:]


def _artifact_ui_route(path: Path, base_artifact_path: Path | None) -> str | None:
    relative_artifact_path = _allowed_relative_artifact_path(path, base_artifact_path)
    if relative_artifact_path is None:
        return None
    encoded_path = quote(relative_artifact_path, safe="")
    if relative_artifact_path.startswith("docs/generated/files/") and path.suffix.lower() == ".md":
        return f"/file?path={encoded_path}"
    return f"/artifact?path={encoded_path}"


def _normalized_relative_link_path(path_value: str) -> str | None:
    if not path_value:
        return path_value
    if _is_windows_drive_path(path_value) or _is_unc_path(path_value):
        return None
    normalized = path_value.replace("\\", "/")
    if normalized.startswith("/"):
        return None
    return normalized


def _safe_existing_ui_href(href: str) -> str | None:
    parsed = urlsplit(href)
    if parsed.path not in ALLOWED_UI_ROUTES:
        return None
    path_values = [value for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key == "path"]
    if not path_values:
        return None
    normalized_path = _normalized_route_path(path_values[0])
    if normalized_path is None:
        return None
    target_path = (Path.cwd() / normalized_path).resolve()
    if _allowed_relative_artifact_path(target_path, None) is None:
        return None
    return href


def _normalized_route_path(path_value: str) -> str | None:
    decoded = unquote(path_value)
    if _is_windows_drive_path(decoded) or _is_unc_path(decoded):
        return None
    normalized = decoded.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized or normalized.endswith("/.."):
        return None
    return normalized


def _is_windows_drive_path(value: str) -> bool:
    return len(value) >= 2 and value[0].isalpha() and value[1] == ":"


def _is_unc_path(value: str) -> bool:
    return value.startswith("\\\\") or value.startswith("//")


def _allowed_relative_artifact_path(path: Path, base_artifact_path: Path | None) -> str | None:
    for root, project_root in _allowed_doc_roots(base_artifact_path):
        if _is_relative_to(path, root):
            return path.relative_to(project_root).as_posix()
    return None


def _allowed_doc_roots(base_artifact_path: Path | None) -> list[tuple[Path, Path]]:
    project_roots = [Path.cwd().resolve()]
    if base_artifact_path is not None:
        parts = base_artifact_path.resolve().parts
        for index, part in enumerate(parts[:-1]):
            if part == "docs" and index + 1 < len(parts) and parts[index + 1] in ALLOWED_DOC_ROOT_NAMES:
                project_roots.append(Path(*parts[:index]).resolve())
                break

    roots: list[tuple[Path, Path]] = []
    seen: set[tuple[str, str]] = set()
    for project_root in project_roots:
        for root_name in ALLOWED_DOC_ROOT_NAMES:
            root = (project_root / "docs" / root_name).resolve()
            key = (root.as_posix(), project_root.as_posix())
            if key not in seen:
                roots.append((root, project_root))
                seen.add(key)
    return roots


def _resolve_optional_path(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    return path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()


def _read_display_text(path: Path) -> tuple[str, bool]:
    data = path.read_bytes()
    text = data[: DEFAULT_TEXT_LIMIT + 1].decode("utf-8", errors="replace")
    if len(text) > DEFAULT_TEXT_LIMIT:
        return text[:DEFAULT_TEXT_LIMIT] + "\n\n[truncated]", True
    return text, False


def _pretty_json(raw_text: str, warnings: list[str]) -> str:
    try:
        payload: Any = json.loads(raw_text)
    except json.JSONDecodeError:
        warnings.append("JSON artifact is invalid; showing raw text.")
        return raw_text
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _looks_binary(text: str) -> bool:
    if not text:
        return False
    return "\x00" in text


def _is_external_http_href(href: str) -> bool:
    parsed = urlsplit(href)
    return parsed.scheme.lower() in {"http", "https"}


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix().replace("\\", "/")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
