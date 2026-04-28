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

from docgen.ui_actions import ActionError, ActionRunner, CONFIRMATION_PHRASE
from docgen.ui_run_diff import RunDiffError, build_run_diff

REQUIRED_UI_DATA_FILES = {
    "current_state": "current-state.json",
    "modules_index": "modules-index.json",
    "history_index": "history-index.json",
    "history_runs": "history-runs.json",
    "ui_data_manifest": "ui-data-manifest.json",
}
OPTIONAL_UI_DATA_FILES = {
    "problems_index": "problems-index.json",
    "files_index": "files-index.json",
    "functions_index": "functions-index.json",
    "search_index": "search-index.json",
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
    history_runs: dict[str, Any]
    problems_index: dict[str, Any]
    files_index: dict[str, Any]
    functions_index: dict[str, Any]
    search_index: dict[str, Any]
    ui_data_manifest: dict[str, Any]
    warnings: list[str]


class UiNotFound(ValueError):
    pass


class DocgenUiServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        config: UiServerConfig,
        data: UiDataBundle,
        action_runner: ActionRunner | None = None,
    ):
        super().__init__(server_address, DocgenUiRequestHandler)
        self.config = config
        self.data = data
        self.action_runner = action_runner or ActionRunner(
            project_root=config.project_root,
            generated_root=config.generated_root,
            enhanced_root=config.enhanced_root,
            ui_data_root=config.ui_data_root,
        )


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
    action_runner: ActionRunner | None = None,
) -> DocgenUiServer:
    return DocgenUiServer((host, port), config, data or load_ui_data(config), action_runner=action_runner)


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
    for key, filename in OPTIONAL_UI_DATA_FILES.items():
        path = config.ui_data_root / filename
        payload = load_json(path)
        if payload is None:
            warnings.append(f"Missing optional UI data file: {normalize_path(path, config.project_root)}")
            payload = {}
        payloads[key] = payload
    return UiDataBundle(
        current_state=payloads["current_state"],
        modules_index=payloads["modules_index"],
        history_index=payloads["history_index"],
        history_runs=payloads["history_runs"],
        problems_index=payloads["problems_index"],
        files_index=payloads["files_index"],
        functions_index=payloads["functions_index"],
        search_index=payloads["search_index"],
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
        "routes": [
            "/",
            "/modules",
            "/module/{name}",
            "/problems",
            "/search",
            "/compare",
            "/actions",
            "/compare/generation",
            "/compare/verification",
            "/history",
            "/history/generation/{run_id}",
            "/history/verification/{run_id}",
            "/ui-data/current-state.json",
        ],
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
            elif path == "/problems":
                self.send_html(render_problems(parsed.query, self.server.config, self.server.data))
            elif path == "/search":
                self.send_html(render_search(parsed.query, self.server.config, self.server.data))
            elif path == "/compare":
                self.send_html(render_compare(self.server.config, self.server.data))
            elif path == "/compare/generation":
                self.send_html(render_compare_run("generation", parsed.query, self.server.config, self.server.data))
            elif path == "/compare/verification":
                self.send_html(render_compare_run("verification", parsed.query, self.server.config, self.server.data))
            elif path == "/actions":
                self.send_html(render_actions(self.server.config, self.server.data, self.server.action_runner))
            elif path == "/actions/build-ui-data":
                self.send_html(
                    render_action_preview(
                        "build_ui_data",
                        parsed.query,
                        self.server.config,
                        self.server.data,
                        self.server.action_runner,
                    )
                )
            elif path == "/actions/explain":
                self.send_html(
                    render_action_preview(
                        "explain_module",
                        parsed.query,
                        self.server.config,
                        self.server.data,
                        self.server.action_runner,
                    )
                )
            elif path == "/actions/verify":
                self.send_html(
                    render_action_preview(
                        "verify_module",
                        parsed.query,
                        self.server.config,
                        self.server.data,
                        self.server.action_runner,
                    )
                )
            elif path.startswith("/actions/runs/"):
                self.send_html(render_action_run(unquote(path.removeprefix("/actions/runs/")), self.server.config, self.server.data, self.server.action_runner))
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
        except UiNotFound as exc:
            self.send_html(render_error(str(exc)), status=HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self.send_html(render_error(str(exc)), status=HTTPStatus.BAD_REQUEST)
        except OSError as exc:
            self.send_html(render_error(str(exc)), status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            form = self.read_form()
            if path == "/actions/build-ui-data":
                entry = run_action_from_form("build_ui_data", form, self.server.data, self.server.action_runner)
                if entry.get("status") == "success":
                    self.server.data = load_ui_data(self.server.config)
                self.send_html(render_action_result(entry, self.server.config, self.server.data))
            elif path == "/actions/explain":
                entry = run_action_from_form("explain_module", form, self.server.data, self.server.action_runner)
                self.send_html(render_action_result(entry, self.server.config, self.server.data))
            elif path == "/actions/verify":
                entry = run_action_from_form("verify_module", form, self.server.data, self.server.action_runner)
                self.send_html(render_action_result(entry, self.server.config, self.server.data))
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        except UiNotFound as exc:
            self.send_html(render_error(str(exc)), status=HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self.send_html(render_error(str(exc)), status=HTTPStatus.BAD_REQUEST)
        except OSError as exc:
            self.send_html(render_error(str(exc)), status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def read_form(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        return parse_qs(body, keep_blank_values=True)

    def send_ui_data_file(self, request_path: str) -> None:
        relative = request_path.removeprefix("/ui-data/").strip("/")
        if not relative or "/" in relative or "\\" in relative:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        path = self.server.config.ui_data_root / relative
        allowed_files = set(REQUIRED_UI_DATA_FILES.values()) | set(OPTIONAL_UI_DATA_FILES.values())
        if path.name not in allowed_files or not path.is_file():
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
    warning_or_failed_modules = [
        module for module in modules if nested_text(module, "verification", "verdict") in {"warning", "fail"}
    ]
    missing_enhanced_modules = [module for module in modules if not nested_bool(module, "enhanced", "present")]
    missing_verification_modules = [module for module in modules if not nested_bool(module, "verification", "present")]
    content = [
        page_header("Project Inspector", "Operational read-only view of project documentation entities."),
        '<section class="panel home-overview"><h2>Current operational state</h2>',
        '<div class="quick-links">',
        '<a class="button" href="/modules">All modules</a>',
        '<a class="button" href="/problems">Problems</a>',
        '<a class="button" href="/history">Run history</a>',
        '<a class="button" href="#warning-modules">Warning or failed modules</a>',
        '<a class="button" href="#missing-enhanced">Missing enhanced</a>',
        '<a class="button" href="#missing-verification">Missing verification</a>',
        "</div>",
        "</section>",
        '<section class="metrics">',
        metric("Latest generation run", generation.get("run_id") or "no data"),
        metric("Latest verification run", verification.get("run_id") or "no data"),
        metric("Modules", counts.get("total_modules", 0)),
        metric("Verification pass", counts.get("verification_pass", 0)),
        metric("Verification warning", counts.get("verification_warning", 0)),
        metric("Verification fail", counts.get("verification_fail", 0)),
        metric("Missing enhanced", counts.get("missing_enhanced", 0)),
        metric("Missing verification", counts.get("missing_verification", 0)),
        "</section>",
        render_home_module_section("Warning or failed modules", "warning-modules", warning_or_failed_modules),
        render_home_module_section("Modules missing enhanced explanation", "missing-enhanced", missing_enhanced_modules),
        render_home_module_section("Modules missing verification", "missing-verification", missing_verification_modules),
        '<section class="panel"><h2>Latest live runs</h2>',
        render_run_line("Generation", generation),
        render_run_line("Verification", verification),
        '<p class="muted">History entries are immutable records. The home page shows only the latest live state.</p>',
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
        page_header(str(module.get("name") or "Module"), "Entity-first module view."),
        render_module_summary(module, enhanced, verification),
        render_module_problems_summary(str(module.get("name") or ""), data.problems_index),
        render_module_action_links(str(module.get("name") or "")),
        '<nav class="section-nav" aria-label="Module sections">',
        '<a href="#overview">Обзор</a>',
        '<a href="#facts">Факты</a>',
        '<a href="#enhanced">ИИ-объяснение</a>',
        '<a href="#verification">Проверка</a>',
        '<a href="#related-files">Связанные файлы</a>',
        '<a href="#history">История</a>',
        "</nav>",
        '<section id="overview" class="panel"><h2>Обзор / Summary</h2>',
        definition_list(
            [
                ("Name", module.get("name")),
                ("Type", module.get("type")),
                ("Role", module.get("module_page_role")),
                ("Explain mode", module.get("explain_mode")),
                ("Priority", module.get("priority")),
                ("Status", module_status(module)),
                ("Enhanced", "present" if enhanced.get("present") else "missing"),
                ("Verification", verification.get("verification_status") or "missing"),
            ]
        ),
        "</section>",
        '<section id="facts" class="panel layer factual"><h2>Факты / Factual layer</h2>',
        definition_list(
            [
                ("Present", "yes" if factual.get("present") else "no"),
                ("Module doc", artifact_link(factual.get("module_doc_path"), "Open factual artifact")),
            ],
            raw_labels={"Module doc"},
        ),
        render_artifact_text("Factual markdown", factual.get("module_doc_path"), config),
        "</section>",
        '<section id="enhanced" class="panel layer enhanced"><h2>ИИ-объяснение / Enhanced explanation</h2>',
        render_layer_presence("Enhanced explanation", enhanced.get("present")),
        definition_list(
            [
                ("Generation status", enhanced.get("generation_status")),
                ("Generation run", history_link("generation", enhanced.get("generation_run_id"), enhanced.get("generation_run_id"))),
                ("Metadata", artifact_link(enhanced.get("metadata_path"), "Open generation metadata")),
                ("Raw artifact", artifact_link(enhanced.get("markdown_path"), "Open raw enhanced artifact")),
            ],
            raw_labels={"Generation run", "Metadata", "Raw artifact"},
        ),
        render_artifact_text("Enhanced explanation content", enhanced.get("markdown_path"), config)
        if enhanced.get("present")
        else '<p class="empty-state">Enhanced explanation is missing for this module.</p>',
        "</section>",
        '<section id="verification" class="panel layer verification"><h2>Проверка / Verification</h2>',
        render_verification_structured_summary(verification),
        definition_list(
            [
                ("Present", "yes" if verification.get("present") else "no"),
                ("Verification status", verification.get("verification_status")),
                ("Verifier status", verification.get("verifier_status")),
                ("Verification run", history_link("verification", verification.get("verification_run_id"), verification.get("verification_run_id"))),
                ("Report JSON", artifact_link(verification.get("json_path"), "Open JSON")),
                ("Summary artifact", artifact_link(verification.get("summary_path"), "Open summary artifact")),
            ],
            raw_labels={"Verification run", "Report JSON", "Summary artifact"},
        ),
        render_artifact_text("Verification markdown summary", verification.get("summary_path"), config)
        if verification.get("present")
        else '<p class="empty-state">Verification is missing for this module.</p>',
        "</section>",
        '<section id="related-files" class="panel"><h2>Связанные файлы / Related files</h2>',
        render_source_files(factual.get("source_files")),
        render_file_links(factual.get("file_doc_paths")),
        "</section>",
        '<section id="history" class="panel"><h2>История / History</h2>',
        render_history_links(links),
        "</section>",
    ]
    return layout(str(module.get("name") or "Module"), "\n".join(content), data)


def render_problems(query: str, config: UiServerConfig, data: UiDataBundle) -> str:
    params = parse_qs(query)
    filters = {
        "severity": first_query_value(params, "severity"),
        "type": first_query_value(params, "type"),
        "module": first_query_value(params, "module"),
    }
    index = data.problems_index if isinstance(data.problems_index, dict) else {}
    summary = index.get("summary") if isinstance(index.get("summary"), dict) else {}
    module_problems = [
        problem
        for problem in list_value(index.get("module_problems"))
        if isinstance(problem, dict) and problem_matches_filters(problem, filters, module_level=True)
    ]
    issue_problems = [
        problem
        for problem in list_value(index.get("issue_problems"))
        if isinstance(problem, dict) and problem_matches_filters(problem, filters, module_level=False)
    ]
    content = [
        page_header("Problems / Проблемы", "Issue-first read-only view built from machine-readable verification data."),
        render_problem_state_message(index, module_problems, issue_problems, filters),
        render_problem_filters(filters),
        '<section class="metrics">',
        metric("Modules with warnings", summary.get("modules_with_warnings", 0)),
        metric("Modules with failures", summary.get("modules_with_failures", 0)),
        metric("Missing enhanced", summary.get("modules_missing_enhanced", 0)),
        metric("Missing verification", summary.get("modules_missing_verification", 0)),
        metric("Weak claims", summary.get("weak_claims_total", 0)),
        metric("Unsupported claims", summary.get("unsupported_claims_total", 0)),
        metric("Missing factual support", summary.get("missing_factual_support_total", 0)),
        metric("Missing uncertainty", summary.get("missing_uncertainty_total", 0)),
        "</section>",
        '<section class="panel"><h2>Module-level problems</h2>',
        render_module_problems_table(module_problems),
        "</section>",
        '<section class="panel"><h2>Issue-level problems</h2>',
        render_issue_problems_table(issue_problems),
        "</section>",
    ]
    return layout("Problems", "\n".join(content), data)


def render_search(query: str, config: UiServerConfig, data: UiDataBundle) -> str:
    params = parse_qs(query)
    filters = {
        "q": first_query_value(params, "q"),
        "kind": first_query_value(params, "kind"),
        "verdict": first_query_value(params, "verdict"),
        "type": first_query_value(params, "type"),
        "role": first_query_value(params, "role"),
        "severity": first_query_value(params, "severity"),
        "run_kind": first_query_value(params, "run_kind"),
        "run_id": first_query_value(params, "run_id"),
    }
    records = [
        record
        for record in list_value(data.search_index.get("records"))
        if isinstance(record, dict) and search_record_matches(record, filters)
    ]
    records = sorted(records, key=lambda record: search_rank(record, filters.get("q")))
    content = [
        page_header("Search / Поиск", "Deterministic search over prebuilt UI data indexes."),
        render_search_form(filters),
        render_search_state(data.search_index, records, filters),
        '<section class="panel"><h2>Results</h2>',
        render_search_results(records),
        "</section>",
    ]
    return layout("Search", "\n".join(content), data)


def render_compare(config: UiServerConfig, data: UiDataBundle) -> str:
    generation_runs = detailed_history_runs(data, "generation")
    verification_runs = detailed_history_runs(data, "verification")
    content = [
        page_header("Compare / Сравнение", "Structural run-to-run diff over immutable history manifests."),
        '<section class="panel"><h2>Generation runs</h2>',
        render_compare_picker("generation", generation_runs),
        "</section>",
        '<section class="panel"><h2>Verification runs</h2>',
        render_compare_picker("verification", verification_runs),
        "</section>",
    ]
    return layout("Compare", "\n".join(content), data)


def render_compare_run(kind: str, query: str, config: UiServerConfig, data: UiDataBundle) -> str:
    params = parse_qs(query)
    run_a_id = first_query_value(params, "run_a")
    run_b_id = first_query_value(params, "run_b")
    if not run_a_id or not run_b_id:
        return layout(
            "Compare",
            page_header(f"{kind.title()} Compare", "Select two run_id values on /compare before running a diff.")
            + '<div class="warning">Missing run_a or run_b.</div>',
            data,
        )
    runs = detailed_history_runs(data, kind)
    run_a = next((run for run in runs if run.get("run_id") == run_a_id), None)
    run_b = next((run for run in runs if run.get("run_id") == run_b_id), None)
    if run_a is None or run_b is None:
        missing = run_a_id if run_a is None else run_b_id
        raise UiNotFound(f"Unknown {kind} run: {missing}")
    try:
        diff = build_run_diff(kind, run_a, run_b)
    except RunDiffError as exc:
        raise ValueError(str(exc)) from exc
    content = [
        page_header(f"{kind.title()} Compare", f"{run_a_id} -> {run_b_id}"),
        render_compare_run_header(kind, diff),
        render_compare_summary(kind, diff),
        render_compare_module_table(kind, diff),
    ]
    return layout(f"{kind.title()} Compare", "\n".join(content), data)


def render_history(config: UiServerConfig, data: UiDataBundle) -> str:
    generation_runs = detailed_history_runs(data, "generation")
    verification_runs = detailed_history_runs(data, "verification")
    content = [
        page_header("History", "Immutable run records. Current live state is shown separately on the home page."),
        '<section class="panel history-kind"><h2>Generation Runs</h2>',
        render_history_table_v2("generation", generation_runs),
        "</section>",
        '<section class="panel history-kind"><h2>Verification Runs</h2>',
        render_history_table_v2("verification", verification_runs),
        "</section>",
    ]
    return layout("History", "\n".join(content), data)


def render_history_run(path: str, config: UiServerConfig, data: UiDataBundle) -> str:
    parts = [unquote(part) for part in path.strip("/").split("/")]
    if len(parts) != 3 or parts[1] not in {"generation", "verification"}:
        raise ValueError("Invalid history route.")
    kind = parts[1]
    run_id = parts[2]
    runs = detailed_history_runs(data, kind)
    run = next((item for item in runs if isinstance(item, dict) and item.get("run_id") == run_id), None)
    if run is None:
        raise UiNotFound(f"Unknown {kind} run: {run_id}")
    content = [
        page_header(f"{kind.title()} Run", f"Immutable history record: {run_id}"),
        render_current_history_notice(run),
        render_history_run_summary(kind, run),
        render_history_results(kind, run, data),
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


def render_actions(config: UiServerConfig, data: UiDataBundle, runner: ActionRunner) -> str:
    modules = module_records(data)
    content = [
        page_header("Actions / Действия", "Explicit local actions. Read-only pages stay separate from mutations."),
        '<div class="warning">Actions mutate local artifacts. Expensive LLM actions require preview, explicit module targets, and confirmation.</div>',
        '<section class="action-grid">',
        '<div class="panel action-card"><h2>Build UI data</h2>',
        "<p>Rebuilds docs/ui-data from existing machine-readable artifacts. Network call: false.</p>",
        '<a class="button" href="/actions/build-ui-data">Preview build-ui-data</a>',
        "</div>",
        '<div class="panel action-card"><h2>Targeted explain</h2>',
        "<p>Runs explain-batch only for selected modules. Network/API cost may occur.</p>",
        render_action_select_form("/actions/explain", modules, "Preview explain"),
        "</div>",
        '<div class="panel action-card"><h2>Targeted verify</h2>',
        "<p>Runs verify-batch only for selected modules. Network/API cost may occur.</p>",
        render_action_select_form("/actions/verify", modules, "Preview verify"),
        "</div>",
        "</section>",
        '<section class="panel"><h2>Latest action log</h2>',
        render_action_log_table(runner.load_action_log()),
        "</section>",
    ]
    return layout("Actions", "\n".join(content), data)


def render_action_preview(
    action_type: str,
    query: str,
    config: UiServerConfig,
    data: UiDataBundle,
    runner: ActionRunner,
) -> str:
    params = parse_qs(query)
    modules = action_modules_from_params(params)
    force = checkbox_value(params, "force")
    modules_required = action_type in {"explain_module", "verify_module"}
    if modules_required and not modules:
        content = [
            page_header(action_title(action_type), "Choose explicit module targets before previewing an LLM action."),
            '<div class="warning">At least one module is required. Empty module lists and wildcards are rejected.</div>',
            '<section class="panel">',
            render_action_select_form(action_route(action_type), module_records(data), "Preview"),
            "</section>",
        ]
        return layout(action_title(action_type), "\n".join(content), data)
    try:
        preview = runner.preview(
            action_type,
            modules=modules,
            force=force,
            allowed_modules=allowed_module_names(data),
        )
    except ActionError as exc:
        raise ValueError(str(exc)) from exc
    skipped_modules = explain_skip_modules(data, modules) if action_type == "explain_module" else []
    content = [
        page_header(action_title(action_type), "Preview / dry-run equivalent before mutation."),
        render_explain_skip_warning(skipped_modules, preview) if skipped_modules else "",
        render_action_preview_details(preview),
        render_action_confirm_form(action_type, modules, force, preview, disabled=bool(skipped_modules)),
    ]
    return layout(action_title(action_type), "\n".join(content), data)


def render_action_run(action_id: str, config: UiServerConfig, data: UiDataBundle, runner: ActionRunner) -> str:
    actions = list_value(runner.load_action_log().get("actions"))
    entry = next((item for item in actions if isinstance(item, dict) and item.get("action_id") == action_id), None)
    if entry is None:
        raise UiNotFound(f"Unknown action run: {action_id}")
    return render_action_result(entry, config, data)


def render_action_preview_details(preview: dict[str, Any]) -> str:
    command = preview.get("command") if isinstance(preview.get("command"), list) else []
    outputs = preview.get("expected_outputs") if isinstance(preview.get("expected_outputs"), list) else []
    warnings = preview.get("warnings") if isinstance(preview.get("warnings"), list) else []
    return (
        '<section class="panel action-preview"><h2>Preview</h2>'
        + definition_list(
            [
                ("Action type", preview.get("action_type")),
                ("Targets", ", ".join(str(item) for item in preview.get("targets") or []) or "none"),
                ("Network may be used", str(bool(preview.get("network_may_be_used"))).lower()),
                ("Risk class", preview.get("risk_class")),
                ("Confirmation required", str(bool(preview.get("confirmation_required"))).lower()),
            ]
        )
        + "<h3>Planned command</h3>"
        + f'<pre class="command">{" ".join(esc(part) for part in command)}</pre>'
        + "<h3>Expected outputs</h3>"
        + render_text_list(outputs)
        + "<h3>Warnings</h3>"
        + render_text_list(warnings)
        + "</section>"
    )


def render_action_confirm_form(
    action_type: str,
    modules: list[str],
    force: bool,
    preview: dict[str, Any],
    *,
    disabled: bool = False,
) -> str:
    route = action_route(action_type)
    hidden_modules = "".join(f'<input type="hidden" name="module" value="{esc(module)}">' for module in modules)
    force_input = '<input type="hidden" name="force" value="true">' if force else ""
    confirm_field = ""
    if preview.get("confirmation_required"):
        confirm_field = (
            f'<label>Type {CONFIRMATION_PHRASE} to confirm'
            f'<input name="confirm" autocomplete="off" placeholder="{CONFIRMATION_PHRASE}"></label>'
        )
    return (
        '<section class="panel action-confirm"><h2>Confirmed run</h2>'
        '<p class="muted">Mutation is only performed by this POST form. GET routes only preview.</p>'
        f'<form class="search-form" action="{esc(route)}" method="post">'
        f"{hidden_modules}{force_input}{confirm_field}"
        + (
            '<button type="submit" disabled>Run action disabled</button>'
            if disabled
            else '<button type="submit">Run action</button>'
        )
        + "</form></section>"
    )


def render_action_result(entry: dict[str, Any], config: UiServerConfig, data: UiDataBundle) -> str:
    status = str(entry.get("status") or "unknown")
    summary = entry.get("parsed_result_summary") if isinstance(entry.get("parsed_result_summary"), dict) else {}
    warnings = entry.get("warnings") if isinstance(entry.get("warnings"), list) else []
    content = [
        page_header("Action result", str(entry.get("action_id") or "unknown")),
        '<section class="panel action-result"><h2>Result</h2>',
        definition_list(
            [
                ("Action ID", action_run_link(entry.get("action_id"))),
                ("Action type", entry.get("action_type")),
                ("Targets", ", ".join(str(item) for item in entry.get("targets") or []) or "none"),
                ("Status", status_badge(status)),
                ("Domain status", status_badge(entry.get("domain_status") or status)),
                ("Process status", entry.get("process_status")),
                ("Network may be used", str(bool(entry.get("network_may_be_used"))).lower()),
                ("Network call", stringify_bool_or_unknown(entry.get("network_call"))),
                ("Exit code", entry.get("exit_code")),
                ("Duration seconds", entry.get("duration_seconds")),
                ("Stdout", artifact_link(entry.get("stdout_path"), "stdout log")),
                ("Stderr", artifact_link(entry.get("stderr_path"), "stderr log")),
                ("Error", entry.get("error")),
            ],
            raw_labels={"Action ID", "Status", "Domain status", "Stdout", "Stderr"},
        ),
        render_action_domain_summary(summary),
        "<h3>Command</h3>",
        f'<pre class="command">{" ".join(esc(part) for part in entry.get("command") or [])}</pre>',
        "<h3>Warnings</h3>",
        render_text_list(warnings),
        "</section>",
        '<section class="panel"><h2>Next steps</h2>',
        render_action_next_steps(entry),
        "</section>",
    ]
    return layout("Action result", "\n".join(content), data)


def render_action_log_table(log: dict[str, Any]) -> str:
    actions = [item for item in list_value(log.get("actions")) if isinstance(item, dict)]
    if not actions:
        return '<p class="muted">No actions have been recorded.</p>'
    rows = []
    for entry in actions[:25]:
        rows.append(
            "<tr>"
            f"<td>{action_run_link(entry.get('action_id'))}</td>"
            f"<td>{esc(entry.get('created_at'))}</td>"
            f"<td>{esc(entry.get('action_type'))}</td>"
            f"<td>{esc(', '.join(str(item) for item in entry.get('targets') or []))}</td>"
            f"<td>{status_badge(entry.get('status'))}</td>"
            f"<td>{esc(entry.get('domain_status') or entry.get('status'))}</td>"
            f"<td>{esc(entry.get('exit_code'))}</td>"
            f"<td>{artifact_link(entry.get('stdout_path'), 'stdout')}<br>{artifact_link(entry.get('stderr_path'), 'stderr')}</td>"
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr><th>Action</th><th>Created at</th>'
        "<th>Type</th><th>Targets</th><th>Status</th><th>Domain</th><th>Exit</th><th>Logs</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def render_action_select_form(route: str, modules: list[dict[str, Any]], button_label: str) -> str:
    options = "".join(
        f'<option value="{esc(module.get("name"))}">{esc(module.get("name"))}</option>'
        for module in modules
        if module.get("name")
    )
    return (
        f'<form class="search-form action-select-form" action="{esc(route)}" method="get">'
        f"<label>Module<select name=\"module\">{options}</select></label>"
        '<label class="checkbox-label"><input type="checkbox" name="force" value="true"> Force</label>'
        f'<button type="submit">{esc(button_label)}</button>'
        "</form>"
    )


def render_explain_skip_warning(modules: list[str], preview: dict[str, Any]) -> str:
    command = [str(part) for part in preview.get("command") or []]
    manual_command = " ".join(esc(part) for part in [*command, "--include-skip"])
    return (
        '<div class="warning action-skip-warning">'
        "Этот модуль имеет explain_mode=skip. Обычная генерация не будет выполнена, поэтому RUN отключен."
        f"<br>Modules: {esc(', '.join(modules))}"
        "<br>Если include-skip нужен сознательно, выполните команду вручную:"
        f'<pre class="command">{manual_command}</pre>'
        "</div>"
    )


def render_action_domain_summary(summary: dict[str, Any]) -> str:
    if not summary:
        return '<div class="notice muted">No parsed batch result was available in stdout.</div>'
    rows = [
        ("Kind", summary.get("kind")),
        ("Network call", stringify_bool_or_unknown(summary.get("network_call"))),
        ("Selected modules", ", ".join(str(item) for item in summary.get("selected_modules") or []) or "none"),
        ("Total modules selected", summary.get("total_modules_selected")),
        ("Generated count", summary.get("generated_count")),
        ("Verified count", summary.get("verified_count")),
        ("Skipped by plan count", summary.get("skipped_by_plan_count")),
        ("Skipped cached count", summary.get("skipped_cached_count")),
        ("Skipped missing enhanced count", summary.get("skipped_missing_enhanced_count")),
        ("Failed count", summary.get("failed_count")),
    ]
    module_statuses = summary.get("module_statuses") if isinstance(summary.get("module_statuses"), list) else []
    return (
        '<div class="structured-summary action-domain-summary"><h3>Parsed batch result</h3>'
        + definition_list(rows)
        + "<h3>Per-module result statuses</h3>"
        + render_action_module_statuses(module_statuses)
        + "</div>"
    )


def render_action_module_statuses(module_statuses: list[Any]) -> str:
    if not module_statuses:
        return '<p class="muted">No per-module result statuses.</p>'
    rows = []
    for item in module_statuses:
        if not isinstance(item, dict):
            continue
        rows.append(
            "<tr>"
            f"<td>{module_name_link(item.get('module'))}</td>"
            f"<td>{status_badge(item.get('status'))}</td>"
            f"<td>{esc(item.get('error'))}</td>"
            "</tr>"
        )
    if not rows:
        return '<p class="muted">No per-module result statuses.</p>'
    return (
        '<div class="table-wrap"><table><thead><tr><th>Module</th><th>Status</th><th>Error</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def render_action_next_steps(entry: dict[str, Any]) -> str:
    summary = entry.get("parsed_result_summary") if isinstance(entry.get("parsed_result_summary"), dict) else {}
    changed_count = int_value(summary.get("generated_count")) + int_value(summary.get("verified_count"))
    links = ['<a class="button" href="/actions">Actions</a>', '<a class="button" href="/modules">Modules</a>', '<a class="button" href="/history">History</a>']
    if entry.get("domain_status") == "success" and changed_count > 0:
        links.append('<a class="button" href="/actions/build-ui-data">Rebuild UI data</a>')
        note = '<p class="notice">This action produced generation/verification output. Rebuild UI data to refresh the inspector.</p>'
    elif entry.get("domain_status") == "no_op":
        note = '<p class="notice muted">This action was a no-op/skipped result. UI data rebuild is not required.</p>'
    else:
        note = ""
    return note + '<div class="quick-links">' + "".join(links) + "</div>"


def render_module_action_links(module_name: str) -> str:
    encoded = quote(module_name, safe="")
    return (
        '<section class="panel actions-entry"><h2>Actions</h2>'
        '<p class="muted">These links open preview forms. They do not run expensive actions immediately.</p>'
        '<div class="quick-links">'
        f'<a class="button" href="/actions/explain?module={encoded}">Re-explain this module</a>'
        f'<a class="button" href="/actions/verify?module={encoded}">Re-verify this module</a>'
        "</div></section>"
    )


def run_action_from_form(
    action_type: str,
    form: dict[str, list[str]],
    data: UiDataBundle,
    runner: ActionRunner,
) -> dict[str, Any]:
    modules = action_modules_from_params(form)
    force = checkbox_value(form, "force")
    confirmed = action_type == "build_ui_data" or first_query_value(form, "confirm") == CONFIRMATION_PHRASE
    return runner.run(
        action_type,
        modules=modules,
        force=force,
        confirmed=confirmed,
        allowed_modules=allowed_module_names(data),
    )


def action_modules_from_params(params: dict[str, list[str]]) -> list[str]:
    values = []
    values.extend(params.get("module") or [])
    values.extend(params.get("modules") or [])
    seen: set[str] = set()
    modules: list[str] = []
    for value in values:
        module = str(value or "").strip()
        if module and module not in seen:
            modules.append(module)
            seen.add(module)
    return modules


def allowed_module_names(data: UiDataBundle) -> set[str]:
    return {str(module.get("name")) for module in module_records(data) if module.get("name")}


def explain_skip_modules(data: UiDataBundle, modules: list[str]) -> list[str]:
    selected = set(modules)
    skipped = []
    for module in module_records(data):
        name = str(module.get("name") or "")
        if name in selected and module.get("explain_mode") == "skip":
            skipped.append(name)
    return skipped


def checkbox_value(params: dict[str, list[str]], key: str) -> bool:
    value = first_query_value(params, key)
    return str(value or "").lower() in {"1", "true", "on", "yes"}


def action_route(action_type: str) -> str:
    if action_type == "build_ui_data":
        return "/actions/build-ui-data"
    if action_type == "explain_module":
        return "/actions/explain"
    if action_type == "verify_module":
        return "/actions/verify"
    return "/actions"


def action_title(action_type: str) -> str:
    if action_type == "build_ui_data":
        return "Build UI Data"
    if action_type == "explain_module":
        return "Targeted Explain"
    if action_type == "verify_module":
        return "Targeted Verify"
    return "Action"


def action_run_link(action_id: Any) -> str:
    if not action_id:
        return '<span class="muted">no data</span>'
    return f'<a href="/actions/runs/{quote(str(action_id), safe="")}">{esc(action_id)}</a>'


def render_text_list(values: list[Any]) -> str:
    if not values:
        return '<p class="muted">none</p>'
    return "<ul>" + "".join(f"<li>{esc(item)}</li>" for item in values) + "</ul>"


def stringify_bool_or_unknown(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if value in (None, ""):
        return "unknown"
    return str(value)


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
        '<form class="global-search" action="/search" method="get">'
        '<input name="q" type="search" placeholder="Search modules, files, functions" aria-label="Search">'
        '<button type="submit">Search</button></form>'
        '<nav><a href="/">Home</a><a href="/modules">Modules</a><a href="/problems">Problems</a><a href="/search">Search</a><a href="/compare">Compare</a><a href="/history">History</a><a href="/actions">Actions</a>'
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


def render_home_module_section(title: str, anchor: str, modules: list[dict[str, Any]]) -> str:
    if not modules:
        body = '<p class="muted">no modules</p>'
    else:
        rows = []
        for module in modules:
            verification = module.get("verification") if isinstance(module.get("verification"), dict) else {}
            enhanced = module.get("enhanced") if isinstance(module.get("enhanced"), dict) else {}
            rows.append(
                "<tr>"
                f"<td>{module_link(module)}</td>"
                f"<td>{presence(enhanced.get('present'))}</td>"
                f"<td>{verdict_badge(verification.get('verdict'), verification.get('verification_status'))}</td>"
                f"<td>{issue_counts(verification)}</td>"
                "</tr>"
            )
        body = (
            '<div class="table-wrap"><table>'
            "<thead><tr><th>Module</th><th>Enhanced</th><th>Verification</th><th>Issues</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div>"
        )
    return f'<section id="{esc(anchor)}" class="panel"><h2>{esc(title)}</h2>{body}</section>'


def render_problem_state_message(
    index: dict[str, Any],
    module_problems: list[dict[str, Any]],
    issue_problems: list[dict[str, Any]],
    filters: dict[str, str | None],
) -> str:
    status = str(index.get("status") or "no_data")
    filtered = any(filters.values())
    if status == "no_data":
        return '<div class="warning">Недостаточно данных для problem analysis.</div>'
    if status == "partial":
        return '<div class="notice">Problem analysis is partial because some enhanced or verification artifacts are missing.</div>'
    if not filtered and not module_problems and not issue_problems:
        return '<div class="notice ok-state">Проблем не найдено.</div>'
    if filtered and not module_problems and not issue_problems:
        return '<div class="notice muted">No problems match the active filters.</div>'
    return ""


def render_problem_filters(filters: dict[str, str | None]) -> str:
    active = [f"{key}={value}" for key, value in filters.items() if value]
    active_text = ", ".join(active) if active else "none"
    links = [
        ("All", "/problems"),
        ("Warnings", "/problems?severity=warning"),
        ("Failures", "/problems?severity=fail"),
        ("Weak claims", "/problems?type=weak_claim"),
        ("Unsupported claims", "/problems?type=unsupported_claim"),
        ("Missing support", "/problems?type=missing_factual_support"),
        ("Missing uncertainty", "/problems?type=missing_uncertainty"),
        ("Missing enhanced", "/problems?type=missing_enhanced"),
        ("Missing verification", "/problems?type=missing_verification"),
    ]
    return (
        '<section class="panel problem-filters"><h2>Filters</h2>'
        f'<p class="muted">Active filters: {esc(active_text)}</p>'
        '<div class="quick-links">'
        + "".join(f'<a class="button" href="{esc(url)}">{esc(label)}</a>' for label, url in links)
        + "</div></section>"
    )


def render_search_form(filters: dict[str, str | None]) -> str:
    return (
        '<section class="panel search-controls"><h2>Search filters</h2>'
        '<form class="search-form" action="/search" method="get">'
        f'<label>Query<input name="q" value="{esc(filters.get("q") or "")}"></label>'
        f'<label>Kind<input name="kind" value="{esc(filters.get("kind") or "")}" placeholder="module|file|function|problem"></label>'
        f'<label>Verdict<input name="verdict" value="{esc(filters.get("verdict") or "")}" placeholder="pass|warning|fail|missing"></label>'
        f'<label>Type<input name="type" value="{esc(filters.get("type") or "")}" placeholder="package|weak_claim"></label>'
        f'<label>Role<input name="role" value="{esc(filters.get("role") or "")}" placeholder="detailed"></label>'
        f'<label>Severity<input name="severity" value="{esc(filters.get("severity") or "")}" placeholder="warning|fail|info"></label>'
        f'<label>Run kind<input name="run_kind" value="{esc(filters.get("run_kind") or "")}" placeholder="generation|verification"></label>'
        f'<label>Run ID<input name="run_id" value="{esc(filters.get("run_id") or "")}"></label>'
        '<button type="submit">Search</button>'
        "</form></section>"
    )


def render_search_state(search_index: dict[str, Any], records: list[dict[str, Any]], filters: dict[str, str | None]) -> str:
    if not search_index:
        return '<div class="warning">Search index is unavailable. Run build-ui-data to rebuild UI indexes.</div>'
    active = [f"{key}={value}" for key, value in filters.items() if value]
    active_text = ", ".join(active) if active else "none"
    if not records:
        return f'<div class="notice muted">No search results. Active filters: {esc(active_text)}</div>'
    return f'<div class="notice">Results count: {len(records)}. Active filters: {esc(active_text)}</div>'


def render_search_results(records: list[dict[str, Any]]) -> str:
    if not records:
        return '<p class="muted">No results.</p>'
    items = []
    for record in records:
        links = record.get("links") if isinstance(record.get("links"), dict) else {}
        ui_path = links.get("ui_path")
        artifact_path = links.get("artifact_path")
        meta = [
            f"kind: {record.get('entity_kind')}",
            f"type: {record.get('type')}" if record.get("type") else "",
            f"role: {record.get('role')}" if record.get("role") else "",
            f"verdict: {record.get('verification_verdict')}" if record.get("verification_verdict") else "",
            f"severity: {record.get('severity')}" if record.get("severity") else "",
            f"run: {record.get('run_kind')} {record.get('run_id')}" if record.get("run_id") else "",
        ]
        items.append(
            '<article class="search-result">'
            f'<h3>{search_result_link(record.get("title"), ui_path)}</h3>'
            f'<p>{esc(record.get("subtitle"))}</p>'
            f'<p class="muted">{esc(" | ".join(item for item in meta if item))}</p>'
            f'<p>{artifact_link(artifact_path, "artifact") if artifact_path else ""}</p>'
            "</article>"
        )
    return "".join(items)


def search_result_link(title: Any, ui_path: Any) -> str:
    if ui_path:
        return f'<a href="{esc(ui_path)}">{esc(title)}</a>'
    return esc(title)


def search_record_matches(record: dict[str, Any], filters: dict[str, str | None]) -> bool:
    query = (filters.get("q") or "").strip().lower()
    if query:
        haystack = " ".join(
            str(record.get(key) or "")
            for key in ("title", "subtitle", "entity_id", "module_name", "path", "search_text")
        ).lower()
        if query not in haystack:
            return False
    kind = filters.get("kind")
    if kind and str(record.get("entity_kind") or "") != kind:
        return False
    verdict = filters.get("verdict")
    if verdict and str(record.get("verification_verdict") or "") != verdict:
        return False
    type_filter = filters.get("type")
    if type_filter and type_filter not in {str(record.get("type") or ""), str(record.get("problem_type") or "")}:
        return False
    role = filters.get("role")
    if role and str(record.get("role") or "") != role:
        return False
    severity = filters.get("severity")
    if severity and str(record.get("severity") or "") != severity:
        return False
    run_kind = filters.get("run_kind")
    if run_kind and str(record.get("run_kind") or "") != run_kind:
        return False
    run_id = filters.get("run_id")
    if run_id and str(record.get("run_id") or "") != run_id:
        return False
    return True


def search_rank(record: dict[str, Any], query: str | None) -> tuple[int, str, str]:
    normalized_query = (query or "").strip().lower()
    title = str(record.get("title") or "").lower()
    if normalized_query and title == normalized_query:
        rank = 0
    elif normalized_query and normalized_query in title:
        rank = 1
    else:
        rank = 2
    return (rank, str(record.get("entity_kind") or ""), str(record.get("title") or ""))


def render_compare_picker(kind: str, runs: list[dict[str, Any]]) -> str:
    if not runs:
        return '<p class="muted">No history runs available.</p>'
    options = "".join(
        f'<option value="{esc(run.get("run_id"))}">{esc(run.get("run_id"))} - {esc(run.get("generated_at"))}</option>'
        for run in runs
    )
    quick_link = ""
    if len(runs) >= 2:
        quick_link = (
            f'<p><a class="button" href="/compare/{kind}?run_a={quote(str(runs[1].get("run_id") or ""), safe="")}'
            f'&run_b={quote(str(runs[0].get("run_id") or ""), safe="")}">Compare latest two {kind} runs</a></p>'
        )
    else:
        quick_link = '<p class="muted">At least two runs are required for comparison.</p>'
    return (
        f'<form class="search-form" action="/compare/{kind}" method="get">'
        f'<label>Run A<select name="run_a">{options}</select></label>'
        f'<label>Run B<select name="run_b">{options}</select></label>'
        '<button type="submit">Compare</button>'
        "</form>"
        + quick_link
        + render_compare_run_list(kind, runs)
    )


def render_compare_run_list(kind: str, runs: list[dict[str, Any]]) -> str:
    rows = []
    for run in runs:
        rows.append(
            "<tr>"
            f"<td>{history_link(kind, run.get('run_id'), run.get('run_id'))}</td>"
            f"<td>{esc(run.get('generated_at'))}</td>"
            f"<td>{esc(run.get('provider'))}<br><span class=\"muted\">{esc(run.get('model'))}</span></td>"
            f"<td>{esc(run.get('selected_modules_count') or len(run.get('selected_modules') or []))}</td>"
            f"<td>{esc(usage_text(run.get('usage_totals')))}</td>"
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr><th>Run</th><th>Generated at</th>'
        "<th>Provider / Model</th><th>Selected modules</th><th>Usage</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def render_compare_run_header(kind: str, diff: dict[str, Any]) -> str:
    run_a = diff.get("run_a") if isinstance(diff.get("run_a"), dict) else {}
    run_b = diff.get("run_b") if isinstance(diff.get("run_b"), dict) else {}
    return (
        '<section class="panel"><h2>Compared runs</h2>'
        '<div class="compare-runs">'
        + render_compare_run_card(kind, "Run A", run_a)
        + render_compare_run_card(kind, "Run B", run_b)
        + "</div></section>"
    )


def render_compare_run_card(kind: str, label: str, run: dict[str, Any]) -> str:
    latest = ' <span class="badge ok">matches current live run</span>' if run.get("latest_live_run") else ""
    return (
        '<div class="summary-item compare-run-card">'
        f"<span>{esc(label)}</span>"
        f"<strong>{history_link(kind, run.get('run_id'), run.get('run_id'))}{latest}</strong>"
        f"<p>{esc(run.get('generated_at'))}</p>"
        f"<p>{esc(run.get('provider'))} / {esc(run.get('model'))}</p>"
        f"<p>selected: {esc(run.get('selected_modules_count'))}</p>"
        f"<p>{esc(usage_text(run.get('usage_totals')))}</p>"
        f"<p>{artifact_link(run.get('manifest_path'), 'history manifest')}</p>"
        "</div>"
    )


def render_compare_summary(kind: str, diff: dict[str, Any]) -> str:
    summary = diff.get("summary") if isinstance(diff.get("summary"), dict) else {}
    items = [
        metric("Added", summary.get("modules_added_count", 0)),
        metric("Removed", summary.get("modules_removed_count", 0)),
        metric("Changed", summary.get("modules_changed_count", 0)),
        metric("Unchanged", summary.get("modules_unchanged_count", 0)),
        metric("Usage delta", usage_delta_text(summary.get("usage_total_delta"))),
    ]
    if kind == "generation":
        items.extend(
            [
                metric("Generated delta", summary.get("generated_count_delta", 0)),
                metric("Cached delta", summary.get("skipped_cached_count_delta", 0)),
                metric("Failed delta", summary.get("failed_count_delta", 0)),
            ]
        )
    else:
        items.extend(
            [
                metric("Verdict improved", summary.get("verdict_improved_count", 0)),
                metric("Verdict worsened", summary.get("verdict_worsened_count", 0)),
                metric("Verdict unchanged", summary.get("verdict_unchanged_count", 0)),
            ]
        )
    return '<section class="metrics compare-summary">' + "".join(items) + "</section>"


def render_compare_module_table(kind: str, diff: dict[str, Any]) -> str:
    module_diffs = diff.get("module_diffs") if isinstance(diff.get("module_diffs"), list) else []
    if not module_diffs:
        return '<section class="panel"><h2>Module diffs</h2><p class="muted">No module results to compare.</p></section>'
    rows = []
    for item in module_diffs:
        if not isinstance(item, dict):
            continue
        rows.append(render_generation_diff_row(item) if kind == "generation" else render_verification_diff_row(item))
    headers = (
        "<th>Module</th><th>Change</th><th>Status A</th><th>Status B</th><th>Usage delta</th><th>Changed fields</th>"
        if kind == "generation"
        else "<th>Module</th><th>Change</th><th>Verdict A</th><th>Verdict B</th><th>Direction</th><th>Issue delta</th><th>Usage delta</th><th>Changed fields</th>"
    )
    return (
        '<section class="panel"><h2>Module diffs</h2><div class="table-wrap"><table>'
        f"<thead><tr>{headers}</tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>"
    )


def render_generation_diff_row(item: dict[str, Any]) -> str:
    return (
        "<tr>"
        f"<td>{module_name_link(item.get('module'))}</td>"
        f"<td>{status_badge(item.get('change_status'))}</td>"
        f"<td>{esc(item.get('status_a'))}</td>"
        f"<td>{esc(item.get('status_b'))}</td>"
        f"<td>{esc(usage_delta_text(item.get('usage_delta')))}</td>"
        f"<td>{esc(', '.join(str(field) for field in list_value(item.get('changed_fields'))))}</td>"
        "</tr>"
    )


def render_verification_diff_row(item: dict[str, Any]) -> str:
    return (
        "<tr>"
        f"<td>{module_name_link(item.get('module'))}</td>"
        f"<td>{status_badge(item.get('change_status'))}</td>"
        f"<td>{verdict_badge(item.get('verdict_a'), None)}</td>"
        f"<td>{verdict_badge(item.get('verdict_b'), None)}</td>"
        f"<td>{status_badge(item.get('verdict_direction'))}</td>"
        f"<td>{esc(issue_delta_text(item.get('issue_count_delta')))}</td>"
        f"<td>{esc(usage_delta_text(item.get('usage_delta')))}</td>"
        f"<td>{esc(', '.join(str(field) for field in list_value(item.get('changed_fields'))))}</td>"
        "</tr>"
    )


def usage_delta_text(value: Any) -> str:
    if not isinstance(value, dict):
        return "no data"
    return (
        f"prompt {signed_int(value.get('prompt_tokens'))}, "
        f"completion {signed_int(value.get('completion_tokens'))}, "
        f"total {signed_int(value.get('total_tokens'))}"
    )


def issue_delta_text(value: Any) -> str:
    if not isinstance(value, dict):
        return "no data"
    return (
        f"weak {signed_int(value.get('weak_claims'))}, "
        f"unsupported {signed_int(value.get('unsupported_claims'))}, "
        f"support {signed_int(value.get('missing_factual_support'))}, "
        f"uncertainty {signed_int(value.get('missing_uncertainty'))}"
    )


def signed_int(value: Any) -> str:
    number = int_value(value)
    return f"+{number}" if number > 0 else str(number)


def render_module_problems_table(problems: list[dict[str, Any]]) -> str:
    if not problems:
        return '<p class="muted">No module-level problems.</p>'
    rows = []
    for problem in problems:
        module = problem.get("module")
        artifacts = [
            artifact_link(problem.get("verification_json_path"), "verification JSON"),
            artifact_link(problem.get("verification_summary_path"), "summary"),
        ]
        rows.append(
            "<tr>"
            f"<td>{module_name_link(module)}</td>"
            f"<td>{status_badge(problem.get('severity'))}</td>"
            f"<td>{esc(', '.join(str(item) for item in list_value(problem.get('problem_types'))))}</td>"
            f"<td>{verdict_badge(problem.get('verification_verdict'), None)}</td>"
            f"<td>{esc(problem_issue_counts(problem))}</td>"
            f"<td>{'<br>'.join(artifacts)}</td>"
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table>'
        "<thead><tr><th>Module</th><th>Severity</th><th>Problem types</th><th>Verdict</th><th>Issue counts</th><th>Details</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def render_issue_problems_table(problems: list[dict[str, Any]]) -> str:
    if not problems:
        return '<p class="muted">No issue-level problems.</p>'
    rows = []
    for problem in problems:
        artifacts = [
            artifact_link(problem.get("verification_json_path"), "verification JSON"),
            artifact_link(problem.get("verification_summary_path"), "summary"),
        ]
        rows.append(
            "<tr>"
            f"<td>{module_name_link(problem.get('module'))}</td>"
            f"<td>{status_badge(problem.get('severity'))}</td>"
            f"<td>{esc(problem.get('issue_type'))}</td>"
            f"<td>{esc(problem.get('section'))}</td>"
            f"<td>{esc(problem.get('claim_text'))}</td>"
            f"<td>{esc(problem.get('reason'))}</td>"
            f"<td>{esc(problem.get('suggested_rewrite'))}</td>"
            f"<td>{'<br>'.join(artifacts)}</td>"
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table>'
        "<thead><tr><th>Module</th><th>Severity</th><th>Type</th><th>Section</th><th>Claim</th><th>Reason</th><th>Suggested rewrite</th><th>Details</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def render_module_problems_summary(module_name: str, problems_index: dict[str, Any]) -> str:
    if not isinstance(problems_index, dict) or not problems_index:
        return (
            '<section class="panel module-problems"><h2>Problems</h2>'
            '<p class="muted">Недостаточно данных для problem analysis.</p></section>'
        )
    module_problems = [
        problem
        for problem in list_value(problems_index.get("module_problems"))
        if isinstance(problem, dict) and problem.get("module") == module_name
    ]
    issue_problems = [
        problem
        for problem in list_value(problems_index.get("issue_problems"))
        if isinstance(problem, dict) and problem.get("module") == module_name
    ]
    if problems_index.get("status") == "no_data":
        body = '<p class="muted">Недостаточно данных для problem analysis.</p>'
    elif not module_problems and not issue_problems:
        body = '<p class="muted">Проблем не найдено.</p>'
    else:
        summary = module_problems[0] if module_problems else {}
        counts = summary if summary else count_issue_problems(issue_problems)
        body = (
            '<div class="summary-grid compact">'
            + summary_item("Weak claims", counts.get("weak_claims_count", 0))
            + summary_item("Unsupported claims", counts.get("unsupported_claims_count", 0))
            + summary_item("Missing factual support", counts.get("missing_factual_support_count", 0))
            + summary_item("Missing uncertainty", counts.get("missing_uncertainty_count", 0))
            + "</div>"
            f'<p><a class="button" href="/problems?module={quote(module_name, safe="")}">Open module problems</a></p>'
        )
    return f'<section class="panel module-problems"><h2>Problems</h2>{body}</section>'


def problem_matches_filters(problem: dict[str, Any], filters: dict[str, str | None], *, module_level: bool) -> bool:
    if filters.get("module") and str(problem.get("module") or "") != filters["module"]:
        return False
    if filters.get("severity") and str(problem.get("severity") or "") != filters["severity"]:
        return False
    requested_type = filters.get("type")
    if requested_type:
        if module_level:
            return requested_type in {str(item) for item in list_value(problem.get("problem_types"))}
        return str(problem.get("issue_type") or "") == requested_type
    return True


def count_issue_problems(problems: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "weak_claims_count": 0,
        "unsupported_claims_count": 0,
        "missing_factual_support_count": 0,
        "missing_uncertainty_count": 0,
    }
    for problem in problems:
        issue_type = problem.get("issue_type")
        if issue_type == "weak_claim":
            counts["weak_claims_count"] += 1
        elif issue_type == "unsupported_claim":
            counts["unsupported_claims_count"] += 1
        elif issue_type == "missing_factual_support":
            counts["missing_factual_support_count"] += 1
        elif issue_type == "missing_uncertainty":
            counts["missing_uncertainty_count"] += 1
    return counts


def problem_issue_counts(problem: dict[str, Any]) -> str:
    return (
        f"weak: {int_value(problem.get('weak_claims_count'))}, "
        f"unsupported: {int_value(problem.get('unsupported_claims_count'))}, "
        f"support: {int_value(problem.get('missing_factual_support_count'))}, "
        f"uncertainty: {int_value(problem.get('missing_uncertainty_count'))}"
    )


def first_query_value(params: dict[str, list[str]], key: str) -> str | None:
    value = (params.get(key) or [None])[0]
    return value if value not in (None, "") else None


def render_module_summary(module: dict[str, Any], enhanced: dict[str, Any], verification: dict[str, Any]) -> str:
    return (
        '<section class="panel module-summary"><div class="summary-heading">'
        f"<div><h2>Module Summary</h2><p>{esc(module_status(module))}</p></div>"
        f"<div>{verdict_badge(verification.get('verdict'), verification.get('verification_status'))}</div>"
        "</div>"
        '<div class="summary-grid">'
        + summary_item("Type", module.get("type"))
        + summary_item("Role", module.get("module_page_role"))
        + summary_item("Explain mode", module.get("explain_mode"))
        + summary_item("Priority", module.get("priority"))
        + summary_item("Enhanced", "present" if enhanced.get("present") else "missing")
        + summary_item("Generation status", enhanced.get("generation_status"))
        + summary_item("Verification", "present" if verification.get("present") else "missing")
        + summary_item("Verifier status", verification.get("verifier_status"))
        + summary_item("Weak claims", int_value(verification.get("weak_claims_count")))
        + summary_item("Unsupported claims", int_value(verification.get("unsupported_claims_count")))
        + summary_item("Missing uncertainty", int_value(verification.get("missing_uncertainty_count")))
        + summary_item("Missing factual support", int_value(verification.get("missing_factual_support_count")))
        + summary_item("Generation run", enhanced.get("generation_run_id"))
        + summary_item("Verification run", verification.get("verification_run_id"))
        + "</div></section>"
    )


def summary_item(label: str, value: Any) -> str:
    return f'<div class="summary-item"><span>{esc(label)}</span><strong>{esc(value if value not in (None, "") else "no data")}</strong></div>'


def render_layer_presence(label: str, present: Any) -> str:
    state = "present" if present else "missing"
    return f'<p class="layer-state"><strong>{esc(label)}:</strong> {presence(bool(present))} <span class="muted">{esc(state)}</span></p>'


def render_verification_structured_summary(verification: dict[str, Any]) -> str:
    return (
        '<div class="structured-summary verification-structured-summary">'
        "<h3>Structured verification summary</h3>"
        '<div class="summary-grid compact">'
        + summary_item("Verdict", verification.get("verdict") or "missing")
        + summary_item("Verifier status", verification.get("verifier_status"))
        + summary_item("Structured output valid", str(bool(verification.get("structured_output_valid"))).lower())
        + summary_item("Weak claims", int_value(verification.get("weak_claims_count")))
        + summary_item("Unsupported claims", int_value(verification.get("unsupported_claims_count")))
        + summary_item("Missing uncertainty", int_value(verification.get("missing_uncertainty_count")))
        + summary_item("Missing factual support", int_value(verification.get("missing_factual_support_count")))
        + "</div></div>"
    )


def render_source_files(source_files: Any) -> str:
    files = source_files if isinstance(source_files, list) else []
    if not files:
        return '<p class="muted">No source file references in UI data.</p>'
    items = "".join(f"<li>{esc(path)}</li>" for path in files)
    return f"<h3>Source file references</h3><ul class=\"file-list\">{items}</ul>"


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


def render_history_table_v2(kind: str, runs: list[Any]) -> str:
    if not runs:
        return '<p class="muted">no history runs</p>'
    rows = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        latest = ' <span class="badge ok">latest live</span>' if run.get("latest_live_run") else ""
        rows.append(
            "<tr>"
            f'<td><a href="/history/{kind}/{quote(str(run.get("run_id") or ""), safe="")}">{esc(run.get("run_id"))}</a>{latest}</td>'
            f"<td>{esc(run.get('generated_at'))}</td>"
            f"<td>{esc(run.get('provider'))}<br><span class=\"muted\">{esc(run.get('model'))}</span></td>"
            f"<td>{esc(run.get('selected_modules_count') or len(run.get('selected_modules') or []))}</td>"
            f"<td>{esc(history_counts_text(kind, run))}</td>"
            f"<td>{esc(usage_text(run.get('usage_totals')))}</td>"
            f"<td>{format_rate(run.get('cache_hit_rate'))}</td>"
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr><th>History run</th><th>Generated at</th>'
        "<th>Provider / Model</th><th>Selected modules</th><th>Counts</th><th>Usage</th><th>Cache hit rate</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def render_current_history_notice(run: dict[str, Any]) -> str:
    if run.get("latest_live_run"):
        return '<div class="notice">This immutable history entry matches the current latest live run.</div>'
    return '<div class="notice muted">This is an immutable historical run entry, not the current live summary.</div>'


def render_history_run_summary(kind: str, run: dict[str, Any]) -> str:
    summary_items = [
        ("Run ID", run.get("run_id")),
        ("Kind", kind),
        ("Generated at", run.get("generated_at")),
        ("Provider", run.get("provider")),
        ("Model", run.get("model")),
        ("Dry run", str(bool(run.get("dry_run"))).lower()),
        ("Latest live run", str(bool(run.get("latest_live_run"))).lower()),
        ("Manifest", artifact_link(run.get("manifest_path"), "Open history manifest")),
        ("Selected modules", run.get("selected_modules_count") or len(run.get("selected_modules") or [])),
        ("Usage totals", usage_text(run.get("usage_totals"))),
        ("Estimated input tokens", run.get("estimated_input_tokens_total")),
        ("Cache hit rate", format_rate(run.get("cache_hit_rate"))),
        ("Result statuses", json.dumps(run.get("result_status_counts") or {}, ensure_ascii=False, sort_keys=True)),
    ]
    if kind == "generation":
        summary_items.extend(
            [
                ("Generated", run.get("generated_count")),
                ("Skipped cached", run.get("skipped_cached_count")),
                ("Skipped by plan", run.get("skipped_by_plan_count")),
                ("Failed", run.get("failed_count")),
            ]
        )
    else:
        summary_items.extend(
            [
                ("Verification mode", run.get("verification_mode")),
                ("Verified", run.get("verified_count")),
                ("Warnings", run.get("warning_count")),
                ("Failed", run.get("failed_count")),
                ("Skipped cached", run.get("skipped_cached_count")),
                ("Skipped missing enhanced", run.get("skipped_missing_enhanced_count")),
            ]
        )
    return (
        '<section class="panel history-record"><h2>Run Summary</h2>'
        + definition_list(summary_items, raw_labels={"Manifest"})
        + "</section>"
    )


def render_history_results(kind: str, run: dict[str, Any], data: UiDataBundle) -> str:
    results = run.get("results") if isinstance(run.get("results"), list) else []
    if not results:
        return '<section class="panel"><h2>Results</h2><p class="muted">no result rows</p></section>'
    rows = []
    for result in results:
        if not isinstance(result, dict):
            continue
        rows.append(render_generation_result_row(result) if kind == "generation" else render_verification_result_row(result))
    headers = (
        "<th>Module</th><th>Status</th><th>Priority / Mode</th><th>Cache</th><th>Usage</th><th>Duration</th><th>Artifacts</th><th>Error</th>"
        if kind == "generation"
        else "<th>Module</th><th>Status</th><th>Verifier</th><th>Verdict</th><th>Cache</th><th>Usage</th><th>Artifacts</th><th>Error</th>"
    )
    return (
        '<section class="panel"><h2>Results</h2><div class="table-wrap"><table>'
        f"<thead><tr>{headers}</tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>"
    )


def render_generation_result_row(result: dict[str, Any]) -> str:
    artifacts = [
        artifact_link(result.get("output_path"), "enhanced"),
        artifact_link(result.get("metadata_path"), "metadata"),
    ]
    return (
        "<tr>"
        f"<td>{module_name_link(result.get('module'))}</td>"
        f"<td>{status_badge(result.get('status'))}</td>"
        f"<td>{esc(result.get('priority'))}<br><span class=\"muted\">{esc(result.get('explain_mode'))}</span></td>"
        f"<td>{presence(result.get('cache_hit'))}</td>"
        f"<td>{esc(usage_text(result.get('usage')))}</td>"
        f"<td>{esc(result.get('duration_seconds'))}</td>"
        f"<td>{'<br>'.join(artifacts)}</td>"
        f"<td>{esc(result.get('error'))}</td>"
        "</tr>"
    )


def render_verification_result_row(result: dict[str, Any]) -> str:
    artifacts = [
        artifact_link(result.get("verification_json_path"), "json"),
        artifact_link(result.get("verification_summary_path"), "summary"),
        artifact_link(result.get("enhanced_markdown_path"), "enhanced"),
    ]
    return (
        "<tr>"
        f"<td>{module_name_link(result.get('module'))}</td>"
        f"<td>{status_badge(result.get('status'))}</td>"
        f"<td>{esc(result.get('verifier_status'))}<br><span class=\"muted\">structured: {esc(result.get('structured_output_valid'))}</span></td>"
        f"<td>{verdict_badge(result.get('verdict'), result.get('status'))}</td>"
        f"<td>{presence(result.get('cache_hit'))}</td>"
        f"<td>{esc(usage_text(result.get('usage')))}</td>"
        f"<td>{'<br>'.join(artifacts)}</td>"
        f"<td>{esc(result.get('error'))}</td>"
        "</tr>"
    )


def detailed_history_runs(data: UiDataBundle, kind: str) -> list[dict[str, Any]]:
    runs_key = "generation_runs" if kind == "generation" else "verification_runs"
    detailed = data.history_runs.get(runs_key)
    if isinstance(detailed, list):
        return [run for run in detailed if isinstance(run, dict)]
    fallback = data.history_index.get(runs_key)
    return [run for run in fallback if isinstance(run, dict)] if isinstance(fallback, list) else []


def history_counts_text(kind: str, run: dict[str, Any]) -> str:
    if kind == "generation":
        return (
            f"generated {int_value(run.get('generated_count'))}, "
            f"cached {int_value(run.get('skipped_cached_count'))}, "
            f"failed {int_value(run.get('failed_count'))}"
        )
    return (
        f"verified {int_value(run.get('verified_count'))}, "
        f"warning {int_value(run.get('warning_count'))}, "
        f"failed {int_value(run.get('failed_count'))}"
    )


def usage_text(value: Any) -> str:
    if not isinstance(value, dict):
        return "no data"
    return (
        f"prompt {int_value(value.get('prompt_tokens'))}, "
        f"completion {int_value(value.get('completion_tokens'))}, "
        f"total {int_value(value.get('total_tokens'))}"
    )


def format_rate(value: Any) -> str:
    if value is None:
        return "no data"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "no data"


def module_name_link(name: Any) -> str:
    if not name:
        return '<span class="muted">no data</span>'
    return f'<a href="/module/{quote(str(name), safe="")}">{esc(name)}</a>'


def status_badge(status: Any) -> str:
    text = str(status or "unknown")
    css = "fail" if text == "fail" or "failed" in text or text.endswith("_fail") else "warn" if text == "warning" or "warning" in text else "ok"
    if text in {"unknown", "missing", "info", "skipped", "no_op", "skipped_cached", "skipped_missing_enhanced"}:
        css = "neutral"
    return f'<span class="badge {css}">{esc(text)}</span>'


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
    return layout("Error", page_header("Error", message), UiDataBundle({}, {}, {}, {}, {}, {}, {}, {}, {}, []))


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


def list_value(value: Any) -> list[Any]:
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
.global-search {
  display: flex;
  gap: 6px;
  min-width: min(360px, 100%);
}
.global-search input, .search-form input, .search-form select {
  width: 100%;
  padding: 7px 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
}
.global-search button, .search-form button {
  padding: 7px 10px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
  color: var(--text);
}
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
.home-overview { margin-bottom: 12px; }
.quick-links, .section-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.search-form {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 10px;
  align-items: end;
}
.search-form label {
  display: grid;
  gap: 4px;
  color: var(--muted);
  font-size: 12px;
}
.search-form .checkbox-label {
  display: flex;
  align-items: center;
  gap: 8px;
}
.search-form .checkbox-label input { width: auto; }
.action-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
}
.action-card p { min-height: 42px; }
.command {
  overflow: auto;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fbfcfe;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.search-result {
  padding: 12px 0;
  border-bottom: 1px solid var(--line);
}
.search-result:last-child { border-bottom: 0; }
.search-result h3 { margin: 0 0 4px; }
.search-result p { margin: 4px 0; }
.section-nav {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 10px;
  margin: 0 0 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.96);
}
.section-nav a {
  padding: 5px 8px;
  border-radius: 6px;
  background: #edf2f7;
}
.module-summary {
  border-left: 4px solid var(--brand);
}
.summary-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.summary-heading h2 { margin: 0; }
.summary-heading p { margin: 4px 0 0; color: var(--muted); }
.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 10px;
}
.summary-grid.compact {
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
}
.compare-runs {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
}
.compare-run-card p {
  margin: 6px 0 0;
}
.summary-item {
  min-width: 0;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fbfcfe;
}
.summary-item span {
  display: block;
  color: var(--muted);
  font-size: 12px;
}
.summary-item strong {
  display: block;
  margin-top: 4px;
  overflow-wrap: anywhere;
}
.structured-summary {
  padding: 12px;
  margin-bottom: 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fffaf2;
}
.structured-summary h3 { margin-top: 0; }
.layer-state {
  margin: 0 0 12px;
}
.empty-state {
  padding: 12px;
  border: 1px dashed var(--line);
  border-radius: 6px;
  color: var(--muted);
  background: #fbfcfe;
}
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
.notice { padding: 10px 12px; border: 1px solid var(--line); background: #eef7ff; border-radius: 8px; margin-bottom: 14px; }
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
