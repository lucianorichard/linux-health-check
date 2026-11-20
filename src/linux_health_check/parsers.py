"""Pure parsers for Linux kernel interfaces (/proc).

Every function takes the text of a file and returns plain data, without touching
the filesystem. That keeps them trivially testable with captured samples.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

_CPU_N = re.compile(r"^cpu\d+\s")
_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")

# Socket states as encoded in /proc/net/{tcp,udp}[6].
TCP_LISTEN = "0A"
UDP_UNCONNECTED = "07"


@dataclass(frozen=True)
class Mount:
    device: str
    mountpoint: str
    fstype: str


@dataclass(frozen=True)
class Listener:
    proto: str  # "tcp" or "udp"
    address: str
    port: int
    inode: int


def parse_key_values(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines (the output format of `systemctl show`)."""
    result = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key:
            result[key.strip()] = value.strip()
    return result


def parse_meminfo(text: str) -> dict[str, int]:
    """Return /proc/meminfo as {field: value_in_kB}."""
    result = {}
    for line in text.splitlines():
        key, sep, rest = line.partition(":")
        parts = rest.split()
        if sep and parts and parts[0].isdigit():
            result[key.strip()] = int(parts[0])
    return result


def parse_cpu_times(stat_text: str) -> tuple[int, int]:
    """Return (idle, total) jiffies from the aggregate "cpu" line of /proc/stat.

    idle includes iowait. total uses the first 8 fields only: guest and
    guest_nice are already accounted inside user and nice.
    """
    for line in stat_text.splitlines():
        if line.startswith("cpu "):
            values = [int(v) for v in line.split()[1:]]
            values += [0] * (8 - len(values))
            return values[3] + values[4], sum(values[:8])
    raise ValueError("no aggregate 'cpu' line in /proc/stat")


def count_cpus(stat_text: str) -> int:
    """Count online CPUs from the per-CPU lines of /proc/stat."""
    count = sum(1 for line in stat_text.splitlines() if _CPU_N.match(line))
    if count == 0:
        raise ValueError("no per-CPU lines in /proc/stat")
    return count


def parse_btime(stat_text: str) -> int:
    """Return the boot time (Unix epoch seconds) from /proc/stat."""
    for line in stat_text.splitlines():
        if line.startswith("btime "):
            return int(line.split()[1])
    raise ValueError("no 'btime' line in /proc/stat")


def parse_loadavg(text: str) -> tuple[float, float, float]:
    """Return the 1, 5 and 15 minute load averages from /proc/loadavg."""
    fields = text.split()
    if len(fields) < 3:
        raise ValueError("unexpected /proc/loadavg format")
    return float(fields[0]), float(fields[1]), float(fields[2])


def parse_uptime(text: str) -> float:
    """Return seconds since boot from /proc/uptime."""
    fields = text.split()
    if not fields:
        raise ValueError("empty /proc/uptime")
    return float(fields[0])


def _unescape(value: str) -> str:
    # The kernel escapes space, tab, newline and backslash as \ooo octal.
    return _OCTAL_ESCAPE.sub(lambda m: chr(int(m.group(1), 8)), value)


def parse_mounts(text: str) -> list[Mount]:
    """Parse /proc/self/mounts."""
    mounts = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 3:
            mounts.append(Mount(_unescape(fields[0]), _unescape(fields[1]), fields[2]))
    return mounts


def _decode_address(hex_addr: str, ipv6: bool) -> str:
    raw = bytes.fromhex(hex_addr)
    if ipv6:
        # Four 32-bit words, each in host (little-endian) byte order.
        raw = b"".join(raw[i : i + 4][::-1] for i in range(0, 16, 4))
        return str(ipaddress.IPv6Address(raw))
    return str(ipaddress.IPv4Address(raw[::-1]))


def parse_proc_net(text: str, proto: str) -> list[Listener]:
    """Return listening sockets from /proc/net/<proto>.

    proto is one of tcp, tcp6, udp, udp6. TCP sockets count when in LISTEN
    state; UDP sockets when unconnected (no remote peer), which is what
    `ss -lu` reports as listening.
    """
    ipv6 = proto.endswith("6")
    base = proto[:-1] if ipv6 else proto
    if base not in ("tcp", "udp"):
        raise ValueError(f"unsupported protocol: {proto}")
    listeners = []
    for line in text.splitlines()[1:]:  # first line is a header
        fields = line.split()
        if len(fields) < 10:
            continue
        local, remote, state, inode = fields[1], fields[2], fields[3], fields[9]
        if base == "tcp" and state != TCP_LISTEN:
            continue
        if base == "udp" and (state != UDP_UNCONNECTED or not remote.endswith(":0000")):
            continue
        addr_hex, _, port_hex = local.partition(":")
        listeners.append(
            Listener(base, _decode_address(addr_hex, ipv6), int(port_hex, 16), int(inode))
        )
    return listeners
