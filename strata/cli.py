"""Command line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from typing import Optional, Sequence

from . import __version__
from .analyze import DEFAULT_HALF_LIFE, analyse, to_dict
from .render_html import write_html
from .render_term import (
    Style,
    colour_enabled,
    print_coupling,
    print_cycles,
    print_fossils,
    print_hotspots,
    print_knowledge,
    print_report,
)

_EPILOG = """\
examples:
  strata dig                        analyse the current repository
  strata dig ~/code/api -o api.html --open
  strata hotspots . --top 20        just the ranked list
  strata coupling . --hidden        pairs that change together with no import
  strata dig . --json report.json   machine-readable, for CI
"""

COMMANDS = ("dig", "hotspots", "coupling", "fossils", "cycles", "knowledge")


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", nargs="?", default=".", help="repository or directory (default: .)")
    parser.add_argument("--since", metavar="WHEN",
                        help="only history after this point, e.g. '2 years ago'")
    parser.add_argument("--max-commits", type=int, metavar="N",
                        help="stop after N commits — useful on very large repositories")
    parser.add_argument("--half-life", type=float, default=DEFAULT_HALF_LIFE, metavar="DAYS",
                        help=f"age at which a change counts half (default: {DEFAULT_HALF_LIFE:.0f})")
    parser.add_argument("--exclude", action="append", default=[], metavar="FRAGMENT",
                        help="skip paths containing FRAGMENT (repeatable)")
    parser.add_argument("--include-generated", action="store_true",
                        help="do not skip minified, vendored and generated files")
    parser.add_argument("--no-history", action="store_true",
                        help="ignore git entirely and rank on structure alone")
    parser.add_argument("--min-co-changes", type=int, default=4, metavar="N",
                        help="commits two files must share to count as coupled (default: 4)")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colour")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="strata",
        description="Code archaeology: where the risk in a repository actually lives.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"strata {__version__}")
    subs = parser.add_subparsers(dest="command")

    dig = subs.add_parser("dig", help="full analysis: terminal summary and an HTML report")
    _add_common(dig)
    dig.add_argument("-o", "--output", metavar="FILE", default="strata-report.html",
                     help="where to write the HTML report (default: strata-report.html)")
    dig.add_argument("--open", dest="open_browser", action="store_true",
                     help="open the report when it is written")
    dig.add_argument("--json", metavar="FILE", help="also write the raw findings as JSON")
    dig.add_argument("--no-html", action="store_true", help="terminal output only")
    dig.add_argument("--top", type=int, default=10, metavar="N",
                     help="hotspots to print (default: 10)")

    hotspots = subs.add_parser("hotspots", help="ranked complexity × change")
    _add_common(hotspots)
    hotspots.add_argument("--top", type=int, default=20, metavar="N")

    coupling = subs.add_parser("coupling", help="files that change together")
    _add_common(coupling)
    coupling.add_argument("--top", type=int, default=20, metavar="N")
    coupling.add_argument("--hidden", action="store_true",
                          help="only pairs with no import between them")

    fossils = subs.add_parser("fossils", help="idle code that nothing imports")
    _add_common(fossils)
    fossils.add_argument("--top", type=int, default=20, metavar="N")

    cycles = subs.add_parser("cycles", help="import cycles")
    _add_common(cycles)

    knowledge = subs.add_parser("knowledge", help="who owns what, and what only one person knows")
    _add_common(knowledge)

    return parser


def normalise_argv(argv: Sequence[str]) -> list:
    """``strata``, ``strata PATH`` and ``strata --top 5`` all mean ``dig``."""
    argv = list(argv)
    if not argv:
        return ["dig"]
    if argv[0] in ("-h", "--help", "--version"):
        return argv
    if argv[0] in COMMANDS:
        return argv
    return ["dig"] + argv


def _run_analysis(args: argparse.Namespace):
    if not os.path.isdir(args.path):
        raise SystemExit(f"strata: not a directory: {args.path}")
    return analyse(
        args.path,
        since=args.since,
        max_commits=args.max_commits,
        half_life=args.half_life,
        include_generated=args.include_generated,
        exclude=tuple(args.exclude),
        no_history=args.no_history,
        min_co_changes=args.min_co_changes,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(normalise_argv(sys.argv[1:] if argv is None else argv))
    if args.command is None:
        parser.print_help()
        return 0

    style = Style(colour_enabled(False if args.no_color else None))
    report = _run_analysis(args)

    if args.command == "hotspots":
        print_hotspots(report, style, args.top)
    elif args.command == "coupling":
        print_coupling(report, style, args.top, args.hidden)
    elif args.command == "fossils":
        print_fossils(report, style, args.top)
    elif args.command == "cycles":
        print_cycles(report, style)
    elif args.command == "knowledge":
        print_knowledge(report, style)
    else:
        print_report(report, style, top=args.top)

        if args.json:
            with open(args.json, "w", encoding="utf-8") as handle:
                json.dump(to_dict(report), handle, indent=2)
            print(f"\n  json    {args.json}")

        if not args.no_html:
            destination = write_html(report, args.output)
            print(f"  report  {destination}")
            if args.open_browser:
                webbrowser.open(f"file://{destination}")

    return 0


def entrypoint() -> None:  # console_scripts target
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:  # e.g. `strata hotspots | head`
        os._exit(0)


if __name__ == "__main__":  # pragma: no cover
    entrypoint()
