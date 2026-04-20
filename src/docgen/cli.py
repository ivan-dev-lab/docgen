from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analyzer import analyze_project


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
