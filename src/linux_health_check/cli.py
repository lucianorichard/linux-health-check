"""Command line interface.

Exit codes follow the Nagios/Icinga plugin convention so the tool drops into
cron, systemd timers, CI pipelines and existing monitoring:
0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN (including usage and config errors).
"""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .checks import CHECKS, run_checks
from .config import ConfigError, load_config, validate
from .report import build_report, render_json, render_quiet, render_text
from .sources import FixtureSource, LiveSource
from .status import Status

PROG = "linux-health-check"


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        # argparse exits with 2 by default, which would read as CRITICAL.
        self.print_usage(sys.stderr)
        self.exit(int(Status.UNKNOWN), f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog=PROG,
        description="Check the health of a Linux server: CPU, memory, disk space and "
        "inodes, systemd services, listening ports and last boot.",
        epilog="Exit codes: 0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN or usage/config error.",
    )
    parser.add_argument(
        "-c", "--config", metavar="FILE", help="JSON file with thresholds and expectations"
    )
    parser.add_argument(
        "--check",
        action="append",
        choices=list(CHECKS),
        metavar="NAME",
        help=f"run only this check (repeatable): {', '.join(CHECKS)}",
    )
    parser.add_argument(
        "-s",
        "--service",
        action="append",
        default=[],
        metavar="UNIT",
        help="systemd unit that must be active (repeatable, added to the config)",
    )
    parser.add_argument(
        "-p",
        "--expect-port",
        action="append",
        default=[],
        metavar="PORT[/PROTO]",
        help="port that must be listening, e.g. 22 or 53/udp (repeatable)",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--json", action="store_true", help="print the report as JSON instead of text"
    )
    output.add_argument("-q", "--quiet", action="store_true", help="print a single summary line")
    parser.add_argument("-o", "--output", metavar="FILE", help="also write the JSON report to FILE")
    parser.add_argument("--no-color", action="store_true", help="disable colored output")
    parser.add_argument(
        "--fixture",
        metavar="DIR",
        help="read a captured system from DIR instead of this host (demos and tests)",
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _use_color(args) -> bool:
    return not args.no_color and "NO_COLOR" not in os.environ and sys.stdout.isatty()


def _fail(message: str) -> int:
    print(f"{PROG}: error: {message}", file=sys.stderr)
    return int(Status.UNKNOWN)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        config["services"]["units"] += [
            u for u in args.service if u not in config["services"]["units"]
        ]
        config["ports"]["expected"] += args.expect_port
        validate(config)  # again, CLI values can be invalid too (e.g. --expect-port 99999)
    except ConfigError as exc:
        return _fail(f"invalid configuration: {exc}")

    if args.fixture:
        try:
            source = FixtureSource(args.fixture)
        except FileNotFoundError as exc:
            return _fail(str(exc))
    else:
        if not sys.platform.startswith("linux"):
            return _fail("this tool reads /proc and only runs on Linux (use --fixture to demo)")
        source = LiveSource()

    names = args.check or list(CHECKS)
    report = build_report(source, run_checks(source, config, names))

    if args.json:
        sys.stdout.write(render_json(report))
    elif args.quiet:
        sys.stdout.write(render_quiet(report))
    else:
        sys.stdout.write(render_text(report, color=_use_color(args)))

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(render_json(report))
        except OSError as exc:
            return _fail(f"cannot write {args.output}: {exc.strerror or exc}")
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
