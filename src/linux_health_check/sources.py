"""Where system data comes from.

Checks never read the system directly: they ask a *source*. LiveSource reads the
running host; FixtureSource reads a captured directory with the same layout, so
the whole tool can be demonstrated and tested on any machine.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

from .parsers import parse_key_values

SYSTEMCTL_PROPERTIES = ("LoadState", "ActiveState", "SubState")
SYSTEMCTL_TIMEOUT_SECONDS = 10


class ToolUnavailable(Exception):
    """An external tool the check depends on is missing or unusable."""


@dataclass(frozen=True)
class FsStats:
    blocks: int
    bfree: int
    bavail: int
    frsize: int
    files: int
    ffree: int


class LiveSource:
    """Reads the host this process runs on. Never writes anything."""

    is_live = True

    def read_text(self, path: str) -> str:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()

    def statvfs(self, mountpoint: str) -> FsStats:
        st = os.statvfs(mountpoint)
        return FsStats(st.f_blocks, st.f_bfree, st.f_bavail, st.f_frsize, st.f_files, st.f_ffree)

    def systemctl_show(self, unit: str) -> Dict[str, str]:
        cmd = ["systemctl", "show", "--property=" + ",".join(SYSTEMCTL_PROPERTIES), "--", unit]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=SYSTEMCTL_TIMEOUT_SECONDS, check=False
            )
        except FileNotFoundError:
            raise ToolUnavailable("systemctl not found (is this a systemd host?)") from None
        except subprocess.TimeoutExpired:
            raise ToolUnavailable("systemctl timed out") from None
        if proc.returncode != 0:
            # e.g. containers: "System has not been booted with systemd".
            raise ToolUnavailable(proc.stderr.strip() or f"systemctl exited with {proc.returncode}")
        return parse_key_values(proc.stdout)

    def socket_owners(self) -> Tuple[Dict[int, str], int]:
        """Map socket inode -> "name(pid)" by scanning /proc/<pid>/fd.

        Returns the map and how many processes could not be inspected because
        they belong to another user (reading their fds requires root).
        """
        owners: Dict[int, str] = {}
        denied = 0
        for pid_dir in Path("/proc").iterdir():
            if not pid_dir.name.isdigit():
                continue
            try:
                fds = list((pid_dir / "fd").iterdir())
            except PermissionError:
                denied += 1
                continue
            except OSError:  # process exited while scanning
                continue
            name = None
            for fd in fds:
                try:
                    target = os.readlink(fd)
                except OSError:
                    continue
                if not target.startswith("socket:["):
                    continue
                if name is None:
                    try:
                        name = (pid_dir / "comm").read_text().strip()
                    except OSError:
                        name = "?"
                owners.setdefault(int(target[8:-1]), f"{name}({pid_dir.name})")
        return owners, denied

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class FixtureSource:
    """Reads a captured system from a directory.

    Layout: proc/... mirrors /proc (a second read of a file returns
    "<file>.after" when present, to simulate time passing), plus optional
    statvfs.json, systemd.json and sockets.json for data that is not a file.
    """

    is_live = False

    def __init__(self, root: str) -> None:
        self.root = Path(root)
        if not self.root.is_dir():
            raise FileNotFoundError(f"fixture directory not found: {root}")
        self._reads: Dict[str, int] = {}

    def _json(self, name: str, default):
        path = self.root / name
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def read_text(self, path: str) -> str:
        count = self._reads.get(path, 0)
        self._reads[path] = count + 1
        file = self.root / path.lstrip("/")
        after = file.with_name(file.name + ".after")
        if count > 0 and after.exists():
            file = after
        return file.read_text(encoding="utf-8")

    def statvfs(self, mountpoint: str) -> FsStats:
        entry = self._json("statvfs.json", {}).get(mountpoint)
        if entry is None:
            raise FileNotFoundError(2, "No such file or directory", mountpoint)
        if entry == "permission_denied":
            raise PermissionError(13, "Permission denied", mountpoint)
        return FsStats(**entry)

    def systemctl_show(self, unit: str) -> Dict[str, str]:
        units = self._json("systemd.json", None)
        if units is None:
            raise ToolUnavailable("fixture has no systemd.json")
        return units.get(unit, {"LoadState": "not-found", "ActiveState": "inactive"})

    def socket_owners(self) -> Tuple[Dict[int, str], int]:
        data = self._json("sockets.json", {})
        owners = {int(inode): name for inode, name in data.get("owners", {}).items()}
        return owners, int(data.get("denied_processes", 0))

    def sleep(self, seconds: float) -> None:
        pass
