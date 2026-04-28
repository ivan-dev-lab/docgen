from __future__ import annotations

import html
import json
import mimetypes
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

REQUIRED_UI_DATA_FILES = {
    "current_state": "current-state.json",
    "modules_index": "modules-index.json",
    "history_index": "history-index.json",
    "ui_data_manifest": "ui-data-manifest.json",
}
DISPLAY_TEXT_LIMIT = 200_000


@dataclass(frozen=True)
class UiServerConfig:
    generated_root: Path
    enhanced_root: Path
    ui_data_root: Path
    project_root: Path
    strict: bool = False


@dataclass
class UiDataBundle:
    current_state: dict[str, Any]
    modules_index: dict[str, Any]
    history_index: dict[str, Any]
    ui_data_manifest: dict[str, Any]
    warnings: list[str]


class DocgenUiServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], config: UiServerConfig, data: UiDataBundle):
        super().__init__(server_address, DocgenUiRequestHandler)
        self.config = config
        self.data = data


def serve_ui(
    generated_root: Path,
    enhanced_root: Path,
    ui_data_root: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    open_browser: bool = False,
    strict: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    config = build_server_config(generated_root, enhanced_root, ui_data_root, strict=strict)
    data = load_ui_data(config)
    summary = server_summary(host, port, config, data, dry_run=dry_run)
    if dry_run:
        return summary

    server = create_ui_server(config, host=host, port=port, data=data)
    url = f"http://{host}:{server.server_port}/"
    print(f"Docgen UI serving at {url}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return summary


def create_ui_server(
    config: UiServerConfig,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    data: UiDataBundle | None = None,
) -> DocgenUiServer:
    return DocgenUiServer((host, port), config, data or load_ui_data(config))


def build_server_config(
    generated_root: Path,
    enhanced_root: Path,
    ui_data_root: Path,
    *,
    strict: bool = False,
) -> UiServerConfig:
    generated_root = generated_root.expanduser()
    enhanced_root = enhanced_root.expanduser()
    ui_data_root = ui_data_root.expanduser()
    return UiServerConfig(
        generated_root=generated_root,
        enhanced_root=enhanced_root,
        ui_data_root=ui_data_root,
        project_root=infer_project_root(generated_root, enhanced_root, ui_data_root),
        strict=strict,
    )


def infer_project_root(generated_root: Path, enhanced_root: Path, ui_data_root: Path) -> Path:
    for root in (generated_root, enhanced_root, ui_data_root):
        if root.name in {"generated", "enhanced", "ui-data"} and root.parent.name == "docs":
            return root.parent.parent
    return Path.cwd()


def load_ui_data(config: UiServerConfig) -> UiDataBundle:
    warnings: list[str] = []
    payloads: dict[str, dict[str, Any]] = {}
    for key, filename in REQUIRED_UI_DATA_FILES.items():
        path = config.ui_data_root / filename
        payload = load_json(path)
        if payload is None:
            message = f"Missing or invalid UI data file: {normalize_path(path, config.project_root)}"
            if config.strict:
                raise ValueError(message)
            warnings.append(message)
            payload = {}
        payloads[key] = payload
    return UiDataBundle(
        current_state=payloads["current_state"],
        modules_index=payloads["modules_index"],
        history_index=payloads["history_index"],
        ui_data_manifest=payloads["ui_data_manifest"],
        warnings=warnings,
    )


def server_summary(
    host: str,
    port: int,
    config: UiServerConfig,
    data: UiDataBundle,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    modules = data.modules_index.get("modules") if isinstance(data.modules_index.get("modules"), list) else []
    return {
        "schema_version": "1.0",
        "dry_run": dry_run,
        "host": host,
        "port": port,
        "generated_root": normalize_path(config.generated_root, config.project_root),
        "enhanced_root": normalize_path(config.enhanced_root, config.project_root),
        "ui_data_root": normalize_path(config.ui_data_root, config.project_root),
        "module_count": len(modules),
        "routes": ["/", "/modules", "/module/{name}", "/history", "/ui-data/current-state.json"],
        "warnings": data.warnings,
        "network_call": False,
    }


class DocgenUiRequestHandler(BaseHTTPRequestHandler):
    server: DocgenUiServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/":
                self.send_html(render_home(self.server.config, self.server.data))
            elif path == "/modules":
                self.send_html(render_modules(self.server.config, self.server.data))
            elif path.startswith("/module/"):
                self.send_html(render_module(unquote(path.removeprefix("/module/")), self.server.config, self.server.data))
            elif path == "/history":
                self.send_html(render_history(self.server.config, self.server.data))
            elif path.startswith("/history/"):
                self.send_html(render_history_run(path, self.server.config, self.server.data))
            elif path == "/file":
                self.send_html(render_file_page(parsed.query, self.server.config, self.server.data))
            elif path == "/artifact":
                self.send_html(render_artifact_page(parsed.query, self.server.config, self.server.data))
            elif path.startswith("/ui-data/"):
                self.send_ui_data_file(path)
            elif path == "/static/style.css":
                self.send_bytes(STYLE_CSS.encode("utf-8"), "text/css; charset=utf-8")
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        except ValueError as exc:
            self.send_html(render_error(str(exc)), status=HTTPStatus.BAD_REQUEST)
        except OSError as exc:
            self.send_html(render_error(str(exc)), status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def send_ui_data_file(self, request_path: str) -> None:
        relative = request_path.removeprefix("/ui-data/").strip("/")
        if not relative or "/" in relative or "\\" in relative:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        path = self.server.config.ui_data_root / relative
        if path.name not in set(REQUIRED_UI_DATA_FILES.values()) or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/json"
        self.send_bytes(path.read_bytes(), content_type)

    def send_html(self, body: str, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_bytes(body.encode("utf-8"), "text/html; charset=utf-8", status=status)

    def send_bytes(
        self,
        payload: bytes,
        content_type: str,
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def render_home(config: UiServerConfig, data: UiDataBundle) -> str:
    state = data.current_state
    counts = state.get("module_counts") if isinstance(state.get("module_counts"), dict) else {}
    generation = state.get("latest_generation_run") if isinstance(state.get("latest_generation_run"), dict) else {}
    verification = state.get("latest_verification_run") if isinstance(state.get("latest_verification_run"), dict) else {}
    modules = module_records(data)
    problem_modules = [
        module
        for module in modules
        if not nested_bool(module, "enhanced", "present")
        or not nested_bool(module, "verification", "present")
        or nested_text(module, "verification", "verdict") in {"warning", "fail"}
    ]
    content = [
        page_header("Project Inspector", "Read-only local view of generated documentation artifacts."),
        '<section class="metrics">',
        metric("Latest generation run", generation.get("run_id") or "нет данных"),
        metric("Latest verification run", verification.get("run_id") or "нет данных"),
        metric("Modules", counts.get("total_modules", 0)),
        metric("Verification pass", counts.get("verification_pass", 0)),
        metric("Verification warning", counts.get("verification_warning", 0)),
        metric("Verification fail", counts.get("verification_fail", 0)),
        metric("Missing enhanced", counts.get("missing_enhanced", 0)),
        metric("Missing verification", counts.get("missing_verification", 0)),
        "</section>",
        '<section class="panel"><h2>Problem Modules</h2>',
        render_module_link_list(problem_modules),
        "</section>",
        '<section class="panel"><h2>History</h2>',
        f'<p><a class="button" href="/history">Open history</a></p>',
        render_run_line("Generation", generation),
        render_run_line("Verification", verification),
        "</section>",
    ]
    return layout("Home", "\n".join(content), data)


def render_modules(config: UiServerConfig, data: UiDataBundle) -> str:
    rows = []
    for module in module_records(data):
        verification = module.get("verification") if isinstance(module.get("verification"), dict) else {}
        enhanced = module.get("enhanced") if isinstance(module.get("enhanced"), dict) else {}
        rows.append(
            "<tr>"
            f"<td>{module_link(module)}</td>"
            f"<td>{esc(module.get('type'))}</td>"
            f"<td>{esc(module.get('module_page_role'))}</td>"
            f"<td>{badge(module.get('explain_mode'))}</td>"
            f"<td>{badge(module.get('priority'))}</td>"
            f"<td>{presence(enhanced.get('present'))}</td>"
            f"<td>{verdict_badge(verification.get('verdict'), verification.get('verification_status'))}</td>"
            f"<td>{issue_counts(verification)}</td>"
            "</tr>"
        )
    content = (
        f"{page_header('Modules', 'Deterministic module index from UI data.')}"
        '<section class="panel table-wrap"><table>'
        "<thead><tr><th>Name</th><th>Type</th><th>Role</th><th>Mode</th><th>Priority</th>"
        "<th>Enhanced</th><th>Verification</th><th>Issues</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )
    return layout("Modules", content, data)


def render_module(module_name: str, config: UiServerConfig, data: UiDataBundle) -> str:
    module = find_module(data, module_name)
    if module is None:
        raise ValueError(f"Unknown module: {module_name}")
    factual = module.get("factual") if isinstance(module.get("factual"), dict) else {}
    enhanced = module.get("enhanced") if isinstance(module.get("enhanced"), dict) else {}
    verification = module.get("verification") if isinstance(module.get("verification"), dict) else {}
    links = module.get("links") if isinstance(module.get("links"), dict) else {}
    content = [
        page_header(str(module.get("name") or "Module"), "Module detail"),
        '<section class="panel"><h2>Metadata</h2>',
        definition_list(
            [
                ("Name", module.get("name")),
                ("Type", module.get("type")),
                ("Role", module.get("module_page_role")),
                ("Explain mode", module.get("explain_mode")),
                ("Priority", module.get("priority")),
                ("Status", module_status(module)),
            ]
        ),
        "</section>",
        '<section class="panel layer factual"><h2>Factual</h2>',
        definition_list(
            [
                ("Present", "yes" if factual.get("present") else "no"),
                ("Module doc", artifact_link(factual.get("module_doc_path"), "Open factual artifact")),
            ],
            raw_labels={"Module doc"},
        ),
        render_file_links(factual.get("file_doc_paths")),
        render_artifact_text("Factual markdown", factual.get("module_doc_path"), config),
        "</section>",
        '<section class="panel layer enhanced"><h2>Enhanced</h2>',
        definition_list(
            [
                ("Present", "yes" if enhanced.get("present") else "no"),
                ("Generation status", enhanced.get("generation_status")),
                ("Generation run", history_link("generation", enhanced.get("generation_run_id"), enhanced.get("generation_run_id"))),
                ("Metadata path", artifact_link(enhanced.get("metadata_path"), "Open metadata")),
            ],
            raw_labels={"Generation run", "Metadata path"},
        ),
        render_artifact_text("Enhanced markdown", enhanced.get("markdown_path"), config),
        "</section>",
        '<section class="panel layer verification"><h2>Verification</h2>',
        definition_list(
            [
                ("Present", "yes" if verification.get("present") else "no"),
                ("Verification status", verification.get("verification_status")),
                ("Verifier status", verification.get("verifier_status")),
                ("Verdict", verification.get("verdict")),
                ("Issues", issue_counts(verification)),
                ("Verification run", history_link("verification", verification.get("verification_run_id"), verification.get("verification_run_id"))),
                ("Report JSON", artifact_link(verification.get("json_path"), "Open JSON")),
                ("Summary", artifact_link(verification.get("summary_path"), "Open summary")),
            ],
            raw_labels={"Issues", "Verification run", "Report JSON", "Summary"},
        ),
        render_artifact_text("Verification summary", verification.get("summary_path"), config),
        "</section>",
        '<section class="panel"><h2>Navigation</h2>',
        render_history_links(links),
        "</section>",
    ]
    return layout(str(module.get("name") or "Module"), "\n".join(content), data)


def render_history(config: UiServerConfig, data: UiDataBundle) -> str:
    history = data.history_index
    generation_runs = history.get("generation_runs") if isinstance(history.get("generation_runs"), list) else []
    verification_runs = history.get("verification_runs") if isinstance(history.get("verification_runs"), list) else []
    content = [
        page_header("History", "Run entry points"),
        '<section class="panel"><h2>Generation Runs</h2>',
        render_history_table("generation", generation_runs),
        "</section>",
        '<section class="panel"><h2>Verification Runs</h2>',
        render_history_table("verification", verification_runs),
        "</section>",
    ]
    return layout("History", "\n".join(content), data)


def render_history_run(path: str, config: UiServerConfig, data: UiDataBundle) -> str:
    parts = [unquote(part) for part in path.strip("/").split("/")]
    if len(parts) != 3 or parts[1] not in {"generation", "verification"}:
        raise ValueError("Invalid history route.")
    kind = parts[1]
    run_id = parts[2]
    runs_key = "generation_runs" if kind == "generation" else "verification_runs"
    runs = data.history_index.get(runs_key) if isinstance(data.history_index.get(runs_key), list) else []
    run = next((item for item in runs if isinstance(item, dict) and item.get("run_id") == run_id), None)
    if run is None:
        raise ValueError(f"Unknown {kind} run: {run_id}")
    selected = [module for name in run.get("selected_modules") or [] for module in [find_module(data, str(name))] if module]
    content = [
        page_header(f"{kind.title()} Run", str(run_id)),
        '<section class="panel"><h2>Summary</h2>',
        definition_list(
            [
                ("Run ID", run.get("run_id")),
                ("Generated at", run.get("generated_at")),
                ("Provider", run.get("provider")),
                ("Model", run.get("model")),
                ("Manifest", artifact_link(run.get("manifest_path"), "Open manifest")),
                ("Usage", json.dumps(run.get("usage_totals") or {}, ensure_ascii=False, sort_keys=True)),
            ],
            raw_labels={"Manifest"},
        ),
        "</section>",
        '<section class="panel"><h2>Selected Modules</h2>',
        render_module_link_list(selected),
        "</section>",
    ]
    return layout(f"{kind.title()} Run", "\n".join(content), data)


def render_file_page(query: str, config: UiServerConfig, data: UiDataBundle) -> str:
    params = parse_qs(query)
    path = (params.get("path") or [""])[0]
    if not path:
        raise ValueError("Missing file path.")
    related_modules = [
        module
        for module in module_records(data)
        for file_doc in nested_list(module, "factual", "file_doc_paths")
        if isinstance(file_doc, dict) and file_doc.get("doc_path") == path
    ]
    content = [
        page_header("File", path),
        '<section class="panel"><h2>Related Modules</h2>',
        render_module_link_list(related_modules),
        "</section>",
        '<section class="panel"><h2>Artifact Content</h2>',
        render_artifact_pre(path, config),
        "</section>",
    ]
    return layout("File", "\n".join(content), data)


def render_artifact_page(query: str, config: UiServerConfig, data: UiDataBundle) -> str:
    params = parse_qs(query)
    path = (params.get("path") or [""])[0]
    if not path:
        raise ValueError("Missing artifact path.")
    content = [
        page_header("Artifact", path),
        '<section class="panel"><h2>Content</h2>',
        render_artifact_pre(path, config),
        "</section>",
    ]
    return layout("Artifact", "\n".join(content), data)


def layout(title: str, content: str, data: UiDataBundle) -> str:
    warnings = data.warnings
    warning_html = ""
    if warnings:
        warning_html = '<div class="warning">' + "<br>".join(esc(item) for item in warnings) + "</div>"
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{esc(title)} - Docgen UI</title>"
        '<link rel="stylesheet" href="/static/style.css">'
        "</head><body>"
        '<header class="topbar"><a class="brand" href="/">Docgen UI</a>'
        '<nav><a href="/">Home</a><a href="/modules">Modules</a><a href="/history">History</a>'
        '<a href="/ui-data/current-state.json">current-state.json</a></nav></header>'
        f"<main>{warning_html}{content}</main>"
        "</body></html>"
    )


def page_header(title: str, subtitle: str) -> str:
    return f'<section class="page-title"><h1>{esc(title)}</h1><p>{esc(subtitle)}</p></section>'


def metric(label: str, value: Any) -> str:
    return f'<div class="metric"><span>{esc(label)}</span><strong>{esc(value)}</strong></div>'


def render_run_line(label: str, run: dict[str, Any]) -> str:
    run_id = run.get("run_id") if isinstance(run, dict) else None
    history_path = run.get("history_manifest_path") if isinstance(run, dict) else None
    return (
        '<div class="run-line">'
        f"<strong>{esc(label)}</strong>"
        f"<span>{esc(run_id or 'нет данных')}</span>"
        f"{artifact_link(history_path, 'manifest') if history_path else ''}"
        "</div>"
    )


def render_module_link_list(modules: list[dict[str, Any]]) -> str:
    if not modules:
        return '<p class="muted">нет данных</p>'
    return '<ul class="link-list">' + "".join(f"<li>{module_link(module)}</li>" for module in modules) + "</ul>"


def render_file_links(file_docs: Any) -> str:
    docs = file_docs if isinstance(file_docs, list) else []
    if not docs:
        return '<p class="muted">No related file docs.</p>'
    items = []
    for item in docs:
        if not isinstance(item, dict):
            continue
        source = item.get("source_file") or item.get("doc_path")
        doc_path = item.get("doc_path")
        items.append(
            f'<li><a href="/file?path={quote(str(doc_path or ""), safe="")}">{esc(source)}</a>'
            f'<span class="muted">{esc(doc_path)}</span></li>'
        )
    return '<h3>Related Files</h3><ul class="file-list">' + "".join(items) + "</ul>"


def render_history_links(links: dict[str, Any]) -> str:
    items = []
    for label, path in (
        ("Generation history manifest", links.get("generation_history_manifest_path")),
        ("Verification history manifest", links.get("verification_history_manifest_path")),
    ):
        if path:
            items.append(f"<li>{artifact_link(path, label)}</li>")
    if not items:
        return '<p class="muted">нет данных</p>'
    return '<ul class="link-list">' + "".join(items) + "</ul>"


def render_history_table(kind: str, runs: list[Any]) -> str:
    if not runs:
        return '<p class="muted">нет данных</p>'
    rows = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        rows.append(
            "<tr>"
            f'<td><a href="/history/{kind}/{quote(str(run.get("run_id") or ""), safe="")}">{esc(run.get("run_id"))}</a></td>'
            f"<td>{esc(run.get('generated_at'))}</td>"
            f"<td>{esc(run.get('model'))}</td>"
            f"<td>{esc(', '.join(str(item) for item in run.get('selected_modules') or []))}</td>"
            f"<td>{esc(run.get('failed_count'))}</td>"
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr><th>Run</th><th>Generated at</th>'
        "<th>Model</th><th>Modules</th><th>Failed</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def render_artifact_text(title: str, path: Any, config: UiServerConfig) -> str:
    if not path:
        return f"<h3>{esc(title)}</h3><p class=\"muted\">нет данных</p>"
    return f"<h3>{esc(title)}</h3>{render_artifact_pre(str(path), config)}"


def render_artifact_pre(path: str, config: UiServerConfig) -> str:
    try:
        resolved = resolve_artifact_path(path, config)
    except ValueError as exc:
        return f'<p class="muted">{esc(str(exc))}</p>'
    if not resolved.exists() or not resolved.is_file():
        return f'<p class="muted">Missing artifact: {esc(path)}</p>'
    text = resolved.read_text(encoding="utf-8", errors="replace")
    if len(text) > DISPLAY_TEXT_LIMIT:
        text = text[:DISPLAY_TEXT_LIMIT] + "\n\n[truncated]"
    return f'<pre class="artifact">{esc(text)}</pre>'


def render_error(message: str) -> str:
    return layout("Error", page_header("Error", message), UiDataBundle({}, {}, {}, {}, []))


def definition_list(items: list[tuple[str, Any]], *, raw_labels: set[str] | None = None) -> str:
    raw_labels = raw_labels or set()
    rows = []
    for label, value in items:
        rendered = str(value) if label in raw_labels else esc(value)
        rows.append(f"<dt>{esc(label)}</dt><dd>{rendered if rendered else 'нет данных'}</dd>")
    return '<dl class="defs">' + "".join(rows) + "</dl>"


def module_link(module: dict[str, Any]) -> str:
    name = str(module.get("name") or "")
    return f'<a href="/module/{quote(name, safe="")}">{esc(name)}</a>'


def artifact_link(path: Any, label: str) -> str:
    if not path:
        return '<span class="muted">нет данных</span>'
    return f'<a href="/artifact?path={quote(str(path), safe="")}">{esc(label)}</a>'


def history_link(kind: str, run_id: Any, label: Any) -> str:
    if not run_id:
        return '<span class="muted">нет данных</span>'
    return f'<a href="/history/{kind}/{quote(str(run_id), safe="")}">{esc(label)}</a>'


def badge(value: Any) -> str:
    return f'<span class="badge">{esc(value or "unknown")}</span>'


def verdict_badge(verdict: Any, status: Any) -> str:
    value = str(verdict or status or "missing")
    css = "warn" if value == "warning" or "warning" in value else "fail" if value == "fail" or "fail" in value else "ok"
    if value in {"missing", "unknown"}:
        css = "neutral"
    return f'<span class="badge {css}">{esc(value)}</span>'


def presence(value: Any) -> str:
    return '<span class="badge ok">present</span>' if value else '<span class="badge neutral">missing</span>'


def issue_counts(verification: dict[str, Any]) -> str:
    keys = [
        ("unsupported", "unsupported_claims_count"),
        ("weak", "weak_claims_count"),
        ("uncertainty", "missing_uncertainty_count"),
        ("support", "missing_factual_support_count"),
    ]
    return ", ".join(f"{label}: {int_value(verification.get(key))}" for label, key in keys)


def module_status(module: dict[str, Any]) -> str:
    verification = module.get("verification") if isinstance(module.get("verification"), dict) else {}
    enhanced = module.get("enhanced") if isinstance(module.get("enhanced"), dict) else {}
    if not enhanced.get("present"):
        return "no enhanced"
    verdict = verification.get("verdict")
    if verdict in {"pass", "warning", "fail"}:
        return f"verification {verdict}"
    return "enhanced exists"


def module_records(data: UiDataBundle) -> list[dict[str, Any]]:
    modules = data.modules_index.get("modules")
    return [module for module in modules if isinstance(module, dict)] if isinstance(modules, list) else []


def find_module(data: UiDataBundle, name: str) -> dict[str, Any] | None:
    return next((module for module in module_records(data) if module.get("name") == name), None)


def resolve_artifact_path(value: str, config: UiServerConfig) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = config.project_root / path
    resolved = path.resolve()
    allowed_roots = [config.generated_root.resolve(), config.enhanced_root.resolve(), config.ui_data_root.resolve()]
    if not any(is_relative_to(resolved, root) for root in allowed_roots):
        raise ValueError(f"Artifact path is outside allowed roots: {value}")
    return resolved


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def normalize_path(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except (OSError, ValueError):
        return path.as_posix().replace("\\", "/")


def nested_bool(payload: dict[str, Any], section: str, key: str) -> bool:
    section_payload = payload.get(section) if isinstance(payload.get(section), dict) else {}
    return bool(section_payload.get(key))


def nested_text(payload: dict[str, Any], section: str, key: str) -> str:
    section_payload = payload.get(section) if isinstance(payload.get(section), dict) else {}
    return str(section_payload.get(key) or "")


def nested_list(payload: dict[str, Any], section: str, key: str) -> list[Any]:
    section_payload = payload.get(section) if isinstance(payload.get(section), dict) else {}
    value = section_payload.get(key)
    return value if isinstance(value, list) else []


def int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


STYLE_CSS = """
:root {
  color-scheme: light;
  --bg: #f6f7f9;
  --panel: #ffffff;
  --text: #20242b;
  --muted: #667085;
  --line: #d8dde6;
  --brand: #1f5f8b;
  --ok: #237a57;
  --warn: #a15c00;
  --fail: #b42318;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
a { color: var(--brand); text-decoration: none; }
a:hover { text-decoration: underline; }
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 20px;
  background: #17212b;
  color: #fff;
}
.brand { color: #fff; font-weight: 700; }
.topbar nav { display: flex; gap: 14px; flex-wrap: wrap; }
.topbar nav a { color: #dbe8f3; }
main { width: min(1180px, calc(100% - 32px)); margin: 24px auto 48px; }
.page-title { margin: 0 0 18px; }
.page-title h1 { margin: 0; font-size: 28px; line-height: 1.15; }
.page-title p { margin: 6px 0 0; color: var(--muted); }
.metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}
.metric, .panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.metric { padding: 14px; }
.metric span { display: block; color: var(--muted); font-size: 12px; }
.metric strong { display: block; margin-top: 6px; font-size: 18px; overflow-wrap: anywhere; }
.panel { padding: 16px; margin: 14px 0; }
.panel h2 { margin: 0 0 12px; font-size: 20px; }
.panel h3 { margin: 18px 0 8px; font-size: 15px; }
.layer { border-left: 4px solid var(--line); }
.factual { border-left-color: #1f5f8b; }
.enhanced { border-left-color: #237a57; }
.verification { border-left-color: #a15c00; }
.defs {
  display: grid;
  grid-template-columns: minmax(130px, 220px) 1fr;
  gap: 8px 16px;
  margin: 0;
}
.defs dt { color: var(--muted); }
.defs dd { margin: 0; overflow-wrap: anywhere; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 9px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
th { color: var(--muted); font-size: 12px; font-weight: 650; }
.badge {
  display: inline-block;
  min-width: 32px;
  padding: 2px 7px;
  border-radius: 999px;
  background: #edf2f7;
  color: #344054;
  font-size: 12px;
}
.badge.ok { background: #e7f6ee; color: var(--ok); }
.badge.warn { background: #fff2dd; color: var(--warn); }
.badge.fail { background: #fde8e7; color: var(--fail); }
.badge.neutral { background: #eef1f5; color: var(--muted); }
.muted { color: var(--muted); }
.warning { padding: 12px 14px; border: 1px solid #f1c16b; background: #fff8e8; border-radius: 8px; margin-bottom: 16px; }
.button { display: inline-block; padding: 7px 10px; border: 1px solid var(--line); border-radius: 6px; background: #fff; }
.run-line { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; margin: 8px 0; }
.link-list, .file-list { padding-left: 18px; }
.file-list li { margin: 6px 0; }
.file-list span { display: block; font-size: 12px; }
pre.artifact {
  overflow: auto;
  max-height: 560px;
  padding: 14px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: #fbfcfe;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
@media (max-width: 720px) {
  .topbar { align-items: flex-start; flex-direction: column; }
  .defs { grid-template-columns: 1fr; }
  main { width: min(100% - 20px, 1180px); }
}
"""
