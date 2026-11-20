"""Health checks: collect data from a source and apply the alert rules.

Each check is isolated: if one fails (missing file, permission denied, tool not
installed) it reports UNKNOWN with an explanation and the others still run.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

from .models import CheckResult
from .parsers import (
    count_cpus,
    parse_btime,
    parse_cpu_times,
    parse_loadavg,
    parse_meminfo,
    parse_mounts,
    parse_proc_net,
    parse_uptime,
)
from .rules import (
    parse_port_spec,
    port_findings,
    port_status,
    service_status,
    threshold_status,
    uptime_status,
)
from .sources import ToolUnavailable
from .status import Status, worst

SUDO_HINT = "run with sudo for complete data"


def _pct(part: float, whole: float) -> Optional[float]:
    return None if whole <= 0 else 100.0 * part / whole


def _fmt_pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def _limit(status: Status, warn, crit) -> str:
    """Explain which threshold was crossed, e.g. ' (crit 90%)'."""
    if status is Status.CRITICAL:
        return f" (crit {crit:g}%)"
    if status is Status.WARNING:
        return f" (warn {warn:g}%)"
    return ""


def _fmt_duration(seconds: float) -> str:
    minutes, _ = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def check_cpu(source, cfg: dict) -> CheckResult:
    first = source.read_text("/proc/stat")
    source.sleep(cfg["sample_seconds"])
    second = source.read_text("/proc/stat")
    idle1, total1 = parse_cpu_times(first)
    idle2, total2 = parse_cpu_times(second)
    cores = count_cpus(first)
    load1, load5, load15 = parse_loadavg(source.read_text("/proc/loadavg"))
    per_core = load5 / cores

    notes = []
    usage = _pct((total2 - total1) - (idle2 - idle1), total2 - total1)
    if usage is None:
        usage_st = Status.OK
        notes.append("CPU usage not sampled (sample_seconds is 0 or the counters did not move)")
    else:
        usage_st = threshold_status(usage, cfg["usage_warn"], cfg["usage_crit"])
    load_st = threshold_status(per_core, cfg["load_per_core_warn"], cfg["load_per_core_crit"])

    summary = f"usage {_fmt_pct(usage)}, load {per_core:.2f}/core (5m), {cores} cores"
    details = [f"load average 1m/5m/15m: {load1:.2f} {load5:.2f} {load15:.2f}"]
    if load_st is not Status.OK:
        limit = cfg["load_per_core_crit"] if load_st is Status.CRITICAL else cfg["load_per_core_warn"]
        details.append(f"5m load per core {per_core:.2f} reached {load_st.name.lower()} {limit:g}")
    if usage_st is not Status.OK:
        details.append(f"CPU usage {_fmt_pct(usage)}{_limit(usage_st, cfg['usage_warn'], cfg['usage_crit'])}")
    return CheckResult(
        "cpu",
        worst([usage_st, load_st]),
        summary,
        details,
        {
            "usage_percent": None if usage is None else round(usage, 1),
            "cores": cores,
            "load1": load1,
            "load5": load5,
            "load15": load15,
            "load5_per_core": round(per_core, 2),
        },
        notes,
    )


def check_memory(source, cfg: dict) -> CheckResult:
    info = parse_meminfo(source.read_text("/proc/meminfo"))
    total = info["MemTotal"]
    notes = []
    if "MemAvailable" in info:
        available = info["MemAvailable"]
    else:  # kernels older than 3.14
        available = info.get("MemFree", 0) + info.get("Buffers", 0) + info.get("Cached", 0)
        notes.append("MemAvailable missing (old kernel); estimated from MemFree+Buffers+Cached")
    used = _pct(total - available, total)
    mem_st = threshold_status(used, cfg["warn"], cfg["crit"])

    swap_total = info.get("SwapTotal", 0)
    swap_used = _pct(swap_total - info.get("SwapFree", 0), swap_total)
    swap_st = Status.OK if swap_used is None else threshold_status(
        swap_used, cfg["swap_warn"], cfg["swap_crit"]
    )

    summary = f"used {_fmt_pct(used)}{_limit(mem_st, cfg['warn'], cfg['crit'])}"
    swap_text = "no swap" if swap_used is None else (
        f"swap {_fmt_pct(swap_used)}{_limit(swap_st, cfg['swap_warn'], cfg['swap_crit'])}"
    )
    summary += f", {swap_text}"
    details = [f"available {available // 1024} MiB of {total // 1024} MiB"]
    return CheckResult(
        "memory",
        worst([mem_st, swap_st]),
        summary,
        details,
        {
            "total_kib": total,
            "available_kib": available,
            "used_percent": None if used is None else round(used, 1),
            "swap_total_kib": swap_total,
            "swap_used_percent": None if swap_used is None else round(swap_used, 1),
        },
        notes,
    )


def _selected_mounts(source, cfg: dict):
    seen = {}
    excluded_types = set(cfg["exclude_fstypes"])
    for mount in parse_mounts(source.read_text("/proc/self/mounts")):
        if mount.fstype in excluded_types:
            continue
        if any(mount.mountpoint.startswith(p) for p in cfg["exclude_mount_prefixes"]):
            continue
        seen[mount.mountpoint] = mount  # last mount on a path wins, like the kernel
    return list(seen.values())


def check_disk(source, cfg: dict) -> CheckResult:
    statuses, details, notes, mounts = [], [], [], []
    for mount in _selected_mounts(source, cfg):
        try:
            st = source.statvfs(mount.mountpoint)
        except PermissionError:
            statuses.append(Status.UNKNOWN)
            details.append(f"{mount.mountpoint}: permission denied")
            notes.append(f"cannot stat {mount.mountpoint}: permission denied; {SUDO_HINT}")
            continue
        except OSError as exc:
            statuses.append(Status.UNKNOWN)
            details.append(f"{mount.mountpoint}: {exc.strerror or exc}")
            continue
        if st.blocks == 0:
            continue  # pseudo filesystem without capacity
        used_blocks = st.blocks - st.bfree
        # Same formula as df: blocks reserved for root do not count as free.
        space = _pct(used_blocks, used_blocks + st.bavail)
        inodes = _pct(st.files - st.ffree, st.files) if st.files else None
        space_st = threshold_status(space, cfg["space_warn"], cfg["space_crit"])
        inode_st = Status.OK if inodes is None else threshold_status(
            inodes, cfg["inode_warn"], cfg["inode_crit"]
        )
        statuses.append(worst([space_st, inode_st]))
        details.append(
            f"{mount.mountpoint} ({mount.fstype}): space {_fmt_pct(space)}"
            f"{_limit(space_st, cfg['space_warn'], cfg['space_crit'])}, inodes {_fmt_pct(inodes)}"
            f"{_limit(inode_st, cfg['inode_warn'], cfg['inode_crit'])}"
        )
        mounts.append({
            "mountpoint": mount.mountpoint,
            "device": mount.device,
            "fstype": mount.fstype,
            "size_bytes": st.blocks * st.frsize,
            "available_bytes": st.bavail * st.frsize,
            "space_used_percent": None if space is None else round(space, 1),
            "inodes_used_percent": None if inodes is None else round(inodes, 1),
            "status": worst([space_st, inode_st]).name,
        })

    status = worst(statuses)
    if not mounts and not statuses:
        return CheckResult("disk", Status.UNKNOWN, "no filesystems to check", notes=notes)
    problems = [m["mountpoint"] for m in mounts if m["status"] != "OK"]
    summary = f"{len(mounts)} filesystems checked"
    if problems:
        summary += f"; attention: {', '.join(problems)}"
    return CheckResult("disk", status, summary, details, {"filesystems": mounts}, notes)


def check_services(source, cfg: dict) -> CheckResult:
    units = cfg["units"]
    if not units:
        return CheckResult("services", Status.OK, "no services configured (use --service or config)")
    not_found = Status[cfg["not_found"].upper()]
    statuses, details, metrics = [], [], {}
    for unit in units:
        props = source.systemctl_show(unit)
        status, reason = service_status(props.get("LoadState", ""), props.get("ActiveState", ""), not_found)
        sub = props.get("SubState")
        statuses.append(status)
        details.append(f"{unit}: {reason}" + (f" ({sub})" if sub and status is Status.OK else ""))
        metrics[unit] = {"status": status.name, **props}
    bad = [u for u, m in metrics.items() if m["status"] != "OK"]
    summary = f"{len(units) - len(bad)}/{len(units)} healthy"
    if bad:
        summary += f"; problems: {', '.join(bad)}"
    return CheckResult("services", worst(statuses), summary, details, {"units": metrics})


def _fmt_port(spec: Tuple[int, str]) -> str:
    return f"{spec[0]}/{spec[1]}"


def check_ports(source, cfg: dict) -> CheckResult:
    listeners, notes = [], []
    for proto in ("tcp", "tcp6", "udp", "udp6"):
        try:
            listeners += parse_proc_net(source.read_text(f"/proc/net/{proto}"), proto)
        except FileNotFoundError:
            continue  # e.g. IPv6 disabled
    owners, denied = source.socket_owners()

    by_port: Dict[Tuple[int, str], List[str]] = defaultdict(list)
    unresolved = 0
    for lst in listeners:
        owner = owners.get(lst.inode)
        if owner is None:
            unresolved += 1
        by_port[(lst.port, lst.proto)].append(f"{lst.address} {owner or '?'}")
    if unresolved and denied:
        notes.append(f"owner unknown for {unresolved} sockets; {denied} processes not readable; {SUDO_HINT}")

    expected = [parse_port_spec(p) for p in cfg["expected"]]
    allowed = None if cfg["allowed"] is None else [parse_port_spec(p) for p in cfg["allowed"]]
    missing, unexpected = port_findings(set(by_port), expected, allowed)
    status = port_status(missing, unexpected)

    summary = f"{len(by_port)} listening ports"
    if missing:
        summary += f"; missing: {', '.join(map(_fmt_port, missing))}"
    if unexpected:
        summary += f"; not allowed: {', '.join(map(_fmt_port, unexpected))}"
    details = [f"{_fmt_port(spec)}: {', '.join(sorted(set(where)))}" for spec, where in sorted(by_port.items())]
    metrics = {
        "listening": [
            {"port": port, "proto": proto, "bindings": sorted(set(where))}
            for (port, proto), where in sorted(by_port.items())
        ],
        "missing": [_fmt_port(p) for p in missing],
        "not_allowed": [_fmt_port(p) for p in unexpected],
    }
    return CheckResult("ports", status, summary, details, metrics, notes)


def check_boot(source, cfg: dict) -> CheckResult:
    btime = parse_btime(source.read_text("/proc/stat"))
    uptime = parse_uptime(source.read_text("/proc/uptime"))
    booted = datetime.fromtimestamp(btime, tz=timezone.utc)
    status, reason = uptime_status(uptime, cfg["recent_boot_warn_minutes"], cfg["max_uptime_days"])
    summary = f"last boot {booted:%Y-%m-%d %H:%M} UTC, up {_fmt_duration(uptime)}"
    return CheckResult(
        "boot",
        status,
        summary,
        [reason] if reason else [],
        {"boot_time": booted.isoformat(), "uptime_seconds": int(uptime)},
    )


CHECKS: Dict[str, Callable[..., CheckResult]] = {
    "cpu": check_cpu,
    "memory": check_memory,
    "disk": check_disk,
    "services": check_services,
    "ports": check_ports,
    "boot": check_boot,
}


def run_checks(source, config: dict, names: List[str]) -> List[CheckResult]:
    results = []
    for name in names:
        try:
            results.append(CHECKS[name](source, config[name]))
        except PermissionError as exc:
            what = exc.filename or "required data"
            results.append(CheckResult(
                name, Status.UNKNOWN, "permission denied",
                notes=[f"cannot read {what}: permission denied; {SUDO_HINT}"],
            ))
        except ToolUnavailable as exc:
            results.append(CheckResult(name, Status.UNKNOWN, f"cannot run: {exc}"))
        except (OSError, ValueError, KeyError) as exc:
            results.append(CheckResult(name, Status.UNKNOWN, f"could not collect data: {exc}"))
        except Exception as exc:  # a bug in one check must not hide the others
            results.append(CheckResult(
                name, Status.UNKNOWN, f"internal error: {type(exc).__name__}: {exc}"
            ))
    return results
