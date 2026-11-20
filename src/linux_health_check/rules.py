"""Alert rules: pure functions that turn measurements into statuses.

No I/O happens here, so every rule is unit-tested in isolation.
"""

from __future__ import annotations

from collections.abc import Iterable

from .status import Status

PortSpec = tuple[int, str]  # (port, "tcp" | "udp")


def threshold_status(value: float | None, warn: float | None, crit: float | None) -> Status:
    """Higher is worse. Reaching a threshold triggers it (value >= threshold).

    A threshold set to None is disabled. A missing value is UNKNOWN.
    """
    if value is None:
        return Status.UNKNOWN
    if crit is not None and value >= crit:
        return Status.CRITICAL
    if warn is not None and value >= warn:
        return Status.WARNING
    return Status.OK


def service_status(
    load_state: str, active_state: str, not_found: Status = Status.WARNING
) -> tuple[Status, str]:
    """Classify a systemd unit from its LoadState and ActiveState."""
    if load_state == "not-found":
        return not_found, "unit not found"
    if load_state == "masked":
        return Status.CRITICAL, "unit is masked"
    if active_state == "active":
        return Status.OK, "active"
    if active_state in ("activating", "reloading"):
        return Status.WARNING, active_state
    if active_state in ("failed", "inactive", "deactivating"):
        return Status.CRITICAL, active_state
    return Status.UNKNOWN, f"unexpected state '{active_state or 'empty'}'"


def parse_port_spec(spec: str) -> PortSpec:
    """Parse "22", "22/tcp" or "53/udp". Raises ValueError when invalid."""
    port_text, _, proto = str(spec).strip().partition("/")
    proto = (proto or "tcp").lower()
    if proto not in ("tcp", "udp"):
        raise ValueError(f"invalid protocol in port '{spec}' (use tcp or udp)")
    if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        raise ValueError(f"invalid port number in '{spec}'")
    return int(port_text), proto


def port_findings(
    listening: set[PortSpec], expected: Iterable[PortSpec], allowed: Iterable[PortSpec] | None
) -> tuple[list[PortSpec], list[PortSpec]]:
    """Return (missing expected ports, listening ports outside the allow-list).

    allowed=None disables the allow-list. Expected ports are always allowed.
    """
    expected_set = set(expected)
    missing = sorted(expected_set - listening)
    unexpected: list[PortSpec] = []
    if allowed is not None:
        unexpected = sorted(listening - set(allowed) - expected_set)
    return missing, unexpected


def port_status(missing: list[PortSpec], unexpected: list[PortSpec]) -> Status:
    """A missing expected service is critical; an extra open port is a warning."""
    if missing:
        return Status.CRITICAL
    if unexpected:
        return Status.WARNING
    return Status.OK


def uptime_status(
    uptime_seconds: float, recent_boot_warn_minutes: float | None, max_uptime_days: float | None
) -> tuple[Status, str | None]:
    """Warn on an unexpected recent reboot, or on an uptime long enough to
    suggest pending kernel updates. Both rules are optional (None disables)."""
    if recent_boot_warn_minutes is not None and uptime_seconds < recent_boot_warn_minutes * 60:
        return Status.WARNING, f"rebooted less than {recent_boot_warn_minutes:g} minutes ago"
    if max_uptime_days is not None and uptime_seconds >= max_uptime_days * 86400:
        return Status.WARNING, f"up for {max_uptime_days:g}+ days; kernel updates may be pending"
    return Status.OK, None
