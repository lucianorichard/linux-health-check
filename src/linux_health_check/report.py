"""Report assembly and rendering (console text and JSON)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from . import __version__
from .models import CheckResult
from .status import Status, worst

SCHEMA_VERSION = 1

_COLORS = {
    Status.OK: "\033[32m",
    Status.WARNING: "\033[33m",
    Status.CRITICAL: "\033[31m",
    Status.UNKNOWN: "\033[35m",
}
_RESET = "\033[0m"


@dataclass
class Report:
    hostname: str
    kernel: str
    generated_at: datetime
    checks: List[CheckResult]

    @property
    def overall(self) -> Status:
        return worst(c.status for c in self.checks)

    @property
    def exit_code(self) -> int:
        return int(self.overall)


def _first_line(source, path: str) -> str:
    try:
        return source.read_text(path).strip() or "unknown"
    except OSError:
        return "unknown"


def build_report(source, checks: List[CheckResult], now: Optional[datetime] = None) -> Report:
    return Report(
        hostname=_first_line(source, "/proc/sys/kernel/hostname"),
        kernel=_first_line(source, "/proc/sys/kernel/osrelease"),
        generated_at=now or datetime.now(timezone.utc),
        checks=checks,
    )


def to_dict(report: Report) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": "linux-health-check", "version": __version__},
        "host": {"hostname": report.hostname, "kernel": report.kernel},
        "generated_at": report.generated_at.isoformat(timespec="seconds"),
        "overall": report.overall.name,
        "exit_code": report.exit_code,
        "checks": [c.to_dict() for c in report.checks],
    }


def render_json(report: Report) -> str:
    return json.dumps(to_dict(report), indent=2, ensure_ascii=False) + "\n"


def _paint(status: Status, text: str, color: bool) -> str:
    return f"{_COLORS[status]}{text}{_RESET}" if color else text


def render_quiet(report: Report) -> str:
    """One line, monitoring-plugin style: 'WARNING: disk: ...; services: ...'."""
    problems = [f"{c.name}: {c.summary}" for c in report.checks if c.status is not Status.OK]
    return f"{report.overall.name}: " + ("; ".join(problems) if problems else "all checks passed") + "\n"


def render_text(report: Report, color: bool = False) -> str:
    width = max((len(c.name) for c in report.checks), default=0)
    lines = [
        f"Linux Health Check {__version__}",
        f"Host: {report.hostname} (kernel {report.kernel})"
        f"  Generated: {report.generated_at:%Y-%m-%d %H:%M:%S %Z}",
        "",
    ]
    notes = []
    for check in report.checks:
        tag = _paint(check.status, f"[{check.status.tag}]", color)
        lines.append(f"{tag} {check.name:<{width}}  {check.summary}")
        lines += [f"{'':{width + 9}}{detail}" for detail in check.details]
        notes += [f"{check.name}: {note}" for note in check.notes]
    lines.append("")
    if notes:
        lines.append("Notes:")
        lines += [f"  - {note}" for note in notes]
        lines.append("")
    overall = _paint(report.overall, report.overall.name, color)
    lines.append(f"Overall: {overall} (exit code {report.exit_code})")
    return "\n".join(lines) + "\n"
