from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analyzer import analyze_project
from .llm.config import build_openrouter_config
from .llm.explain_plan import write_explain_plan
from .llm.openrouter_provider import OpenRouterProvider
from .renderer import render_project


def parse_cli_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError("Expected one of: true, false, yes, no, on, off, 1, 0.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docgen",
        description="Deterministic source project analyzer.",
    )
    subparsers = parser.add_subparsers(dest="command")

    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Analyze a project and emit JSON artifacts.",
    )
    analyze_parser.add_argument(
        "project_path",
        help="Path to the project that should be analyzed.",
    )
    analyze_parser.add_argument(
        "--output",
        dest="output_path",
        help="Output directory for analysis artifacts. Defaults to <project>/.docgen-analysis.",
    )
    analyze_parser.add_argument(
        "--include-tests",
        nargs="?",
        const=True,
        default=True,
        type=parse_cli_bool,
        metavar="true|false",
        help="Include test files in the analysis. Default: true.",
    )
    analyze_parser.add_argument(
        "--include-fixtures",
        nargs="?",
        const=True,
        default=None,
        type=parse_cli_bool,
        metavar="true|false",
        help="Include fixture/sample files. Default: auto (disabled for normal live runs, enabled when the analyzed root itself is a fixture project).",
    )
    analyze_parser.add_argument(
        "--include-generated",
        nargs="?",
        const=True,
        default=False,
        type=parse_cli_bool,
        metavar="true|false",
        help="Include generated analysis outputs and packaging metadata. Default: false.",
    )
    analyze_parser.set_defaults(handler=run_analyze)

    render_parser = subparsers.add_parser(
        "render",
        help="Render Markdown documentation from analysis JSON artifacts.",
    )
    render_parser.add_argument(
        "--analysis",
        dest="analysis_path",
        required=True,
        help="Directory containing the required analysis JSON artifacts.",
    )
    render_parser.add_argument(
        "--output",
        dest="output_path",
        required=True,
        help="Output directory for generated Markdown documentation.",
    )
    render_parser.set_defaults(handler=run_render)

    explain_plan_parser = subparsers.add_parser(
        "explain-plan",
        help="Build an LLM explain-plan JSON from analysis artifacts and generated docs.",
    )
    explain_plan_parser.add_argument(
        "--analysis",
        dest="analysis_path",
        required=True,
        help="Directory containing the required analysis JSON artifacts.",
    )
    explain_plan_parser.add_argument(
        "--docs",
        dest="docs_path",
        required=True,
        help="Directory containing generated factual Markdown documentation.",
    )
    explain_plan_parser.add_argument(
        "--output",
        dest="output_path",
        required=True,
        help="Path to the explain-plan JSON file to create.",
    )
    explain_plan_parser.set_defaults(handler=run_explain_plan)

    llm_smoke_parser = subparsers.add_parser(
        "llm-smoke",
        help="Run a provider smoke check without generating documentation.",
    )
    llm_smoke_parser.add_argument(
        "--provider",
        choices=["openrouter"],
        required=True,
        help="LLM provider to smoke test.",
    )
    llm_smoke_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration without making a network call.",
    )
    llm_smoke_parser.add_argument(
        "--model",
        dest="model",
        help="Optional model override. Defaults to the provider default.",
    )
    llm_smoke_parser.add_argument(
        "--reasoning",
        nargs="?",
        const=True,
        default=True,
        type=parse_cli_bool,
        metavar="true|false",
        help="Enable provider reasoning when supported. Default: true.",
    )
    llm_smoke_parser.add_argument(
        "--max-tokens",
        dest="max_tokens",
        type=int,
        default=64,
        help="Max tokens for the smoke request. Default: 64.",
    )
    llm_smoke_parser.add_argument(
        "--temperature",
        dest="temperature",
        type=float,
        default=0.0,
        help="Sampling temperature for the smoke request. Default: 0.0.",
    )
    llm_smoke_parser.set_defaults(handler=run_llm_smoke)
    return parser


def run_analyze(args: argparse.Namespace) -> int:
    project_path = Path(args.project_path).expanduser()
    output_path = Path(args.output_path).expanduser() if args.output_path else None
    artifacts_dir = analyze_project(
        project_path,
        output_path,
        include_tests=args.include_tests,
        include_fixtures=args.include_fixtures,
        include_generated=args.include_generated,
    )
    print(f"Analysis artifacts saved to: {artifacts_dir}")
    return 0


def run_render(args: argparse.Namespace) -> int:
    analysis_path = Path(args.analysis_path).expanduser()
    output_path = Path(args.output_path).expanduser()
    rendered_dir = render_project(
        analysis_path,
        output_path,
        analysis_path_label=args.analysis_path,
        output_path_label=args.output_path,
    )
    print(f"Rendered documentation saved to: {rendered_dir}")
    return 0


def run_explain_plan(args: argparse.Namespace) -> int:
    analysis_path = Path(args.analysis_path).expanduser()
    docs_path = Path(args.docs_path).expanduser()
    output_path = Path(args.output_path).expanduser()
    explain_plan_path = write_explain_plan(
        analysis_path,
        docs_path,
        output_path,
        analysis_path_label=args.analysis_path,
        docs_path_label=args.docs_path,
    )
    print(f"Explain plan saved to: {explain_plan_path}")
    return 0


def run_llm_smoke(args: argparse.Namespace) -> int:
    if args.provider != "openrouter":
        raise ValueError(f"Unsupported provider: {args.provider}")

    config = build_openrouter_config(
        model=args.model,
        reasoning_enabled=args.reasoning,
    )
    provider = OpenRouterProvider(config=config)
    if args.dry_run:
        print(json.dumps(provider.dry_run_status(), ensure_ascii=False, indent=2))
        return 0

    result = provider.smoke(
        model=args.model,
        reasoning_enabled=args.reasoning,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
    )
    payload = {
        "provider": result.provider,
        "model": result.model,
        "status": "error" if result.error else "ok",
        "content_preview": build_content_preview(result.content),
        "usage": result.usage,
        "reasoning_present": result.reasoning is not None,
        "reasoning_details_present": result.reasoning_details_present,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_content_preview(content: str, limit: int = 160) -> str:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not hasattr(args, "handler"):
        parser.print_help()
        return 1

    try:
        return args.handler(args)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
