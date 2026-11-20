"""Checks against small in-memory sources, focused on edge cases and errors."""

import copy
import unittest

from linux_health_check.checks import run_checks
from linux_health_check.config import DEFAULTS
from linux_health_check.sources import FsStats, ToolUnavailable
from linux_health_check.status import Status


class FakeSource:
    """Serves file contents from a dict; values that are exceptions are raised."""

    def __init__(self, files=None, statvfs=None, units=None, owners=None, denied=0):
        self.files = files or {}
        self._statvfs = statvfs or {}
        self.units = units
        self.owners = owners or {}
        self.denied = denied

    def read_text(self, path):
        value = self.files.get(path)
        if value is None:
            raise FileNotFoundError(2, "No such file or directory", path)
        if isinstance(value, Exception):
            raise value
        return value.pop(0) if isinstance(value, list) else value

    def statvfs(self, mountpoint):
        value = self._statvfs[mountpoint]
        if isinstance(value, Exception):
            raise value
        return value

    def systemctl_show(self, unit):
        if isinstance(self.units, Exception):
            raise self.units
        return self.units[unit]

    def socket_owners(self):
        return self.owners, self.denied

    def sleep(self, seconds):
        pass


def config(**overrides):
    cfg = copy.deepcopy(DEFAULTS)
    for section, values in overrides.items():
        cfg[section].update(values)
    return cfg


def run(source, name, cfg=None):
    return run_checks(source, cfg or config(), [name])[0]


class CpuCheckTest(unittest.TestCase):
    def source(self, before, after, load="0.5 0.5 0.5 1/1 1"):
        def stat(line):
            return f"cpu  {line}\ncpu0 0 0 0 0\ncpu1 0 0 0 0\nbtime 1\n"

        return FakeSource({"/proc/stat": [stat(before), stat(after)], "/proc/loadavg": load})

    def test_usage_from_two_samples(self):
        result = run(self.source("0 0 0 100", "50 0 0 150"), "cpu")
        self.assertEqual(result.metrics["usage_percent"], 50.0)
        self.assertEqual(result.metrics["cores"], 2)
        self.assertIs(result.status, Status.OK)

    def test_load_is_normalised_per_core(self):
        result = run(self.source("0 0 0 100", "0 0 0 200", load="5.0 4.2 3.0 1/1 1"), "cpu")
        self.assertEqual(result.metrics["load5_per_core"], 2.1)
        self.assertIs(result.status, Status.CRITICAL)

    def test_counters_not_moving_is_a_note_not_an_alert(self):
        result = run(self.source("0 0 0 100", "0 0 0 100"), "cpu")
        self.assertIsNone(result.metrics["usage_percent"])
        self.assertIs(result.status, Status.OK)
        self.assertTrue(result.notes)


class MemoryCheckTest(unittest.TestCase):
    def test_uses_mem_available(self):
        src = FakeSource(
            {"/proc/meminfo": "MemTotal: 1000 kB\nMemAvailable: 100 kB\nSwapTotal: 0 kB\n"}
        )
        result = run(src, "memory")
        self.assertEqual(result.metrics["used_percent"], 90.0)
        self.assertIs(result.status, Status.WARNING)
        self.assertIn("no swap", result.summary)

    def test_old_kernel_fallback_is_noted(self):
        text = "MemTotal: 1000 kB\nMemFree: 500 kB\nBuffers: 100 kB\nCached: 100 kB\n"
        result = run(FakeSource({"/proc/meminfo": text}), "memory")
        self.assertEqual(result.metrics["available_kib"], 700)
        self.assertTrue(result.notes)

    def test_swap_alone_can_raise_status(self):
        text = "MemTotal: 1000 kB\nMemAvailable: 900 kB\nSwapTotal: 100 kB\nSwapFree: 10 kB\n"
        self.assertIs(run(FakeSource({"/proc/meminfo": text}), "memory").status, Status.CRITICAL)


class DiskCheckTest(unittest.TestCase):
    MOUNTS = "/dev/sda1 / ext4 rw 0 0\ntmpfs /run tmpfs rw 0 0\n/dev/sdb1 /data xfs rw 0 0\n"

    def test_space_uses_df_formula_and_skips_pseudo_fs(self):
        src = FakeSource(
            {"/proc/self/mounts": self.MOUNTS},
            statvfs={
                # 800 used, 100 available to users, 100 reserved for root -> 88.9%
                "/": FsStats(1000, 200, 100, 4096, 100, 50),
                "/data": FsStats(1000, 900, 900, 4096, 0, 0),
            },
        )
        result = run(src, "disk")
        by_mount = {f["mountpoint"]: f for f in result.metrics["filesystems"]}
        self.assertEqual(set(by_mount), {"/", "/data"})
        self.assertEqual(by_mount["/"]["space_used_percent"], 88.9)
        self.assertIsNone(by_mount["/data"]["inodes_used_percent"])  # xfs/btrfs may report 0 inodes
        self.assertIs(result.status, Status.WARNING)

    def test_inodes_alone_can_be_critical(self):
        src = FakeSource(
            {"/proc/self/mounts": "/dev/sda1 / ext4 rw 0 0\n"},
            statvfs={"/": FsStats(1000, 900, 900, 4096, 1000, 50)},
        )
        self.assertIs(run(src, "disk").status, Status.CRITICAL)

    def test_permission_denied_on_one_mount_is_reported(self):
        src = FakeSource(
            {"/proc/self/mounts": self.MOUNTS},
            statvfs={
                "/": FsStats(1000, 900, 900, 4096, 100, 90),
                "/data": PermissionError(13, "denied"),
            },
        )
        result = run(src, "disk")
        self.assertIs(result.status, Status.UNKNOWN)
        self.assertTrue(any("sudo" in n for n in result.notes))

    def test_excluded_prefixes(self):
        src = FakeSource(
            {"/proc/self/mounts": "/dev/loop1 /snap/x/1 ext4 ro 0 0\n/dev/sda1 / ext4 rw 0 0\n"},
            statvfs={"/": FsStats(1000, 900, 900, 4096, 100, 90)},
        )
        self.assertEqual(len(run(src, "disk").metrics["filesystems"]), 1)


class ServicesCheckTest(unittest.TestCase):
    def test_no_units_configured_is_ok(self):
        self.assertIs(run(FakeSource(), "services").status, Status.OK)

    def test_failed_unit_is_critical(self):
        src = FakeSource(
            units={
                "a.service": {
                    "LoadState": "loaded",
                    "ActiveState": "active",
                    "SubState": "running",
                },
                "b.service": {"LoadState": "loaded", "ActiveState": "failed", "SubState": "failed"},
            }
        )
        result = run(src, "services", config(services={"units": ["a.service", "b.service"]}))
        self.assertIs(result.status, Status.CRITICAL)
        self.assertIn("b.service", result.summary)

    def test_missing_systemctl_is_unknown(self):
        src = FakeSource(units=ToolUnavailable("systemctl not found"))
        result = run(src, "services", config(services={"units": ["a.service"]}))
        self.assertIs(result.status, Status.UNKNOWN)
        self.assertIn("systemctl not found", result.summary)


class PortsCheckTest(unittest.TestCase):
    HEADER = "  sl  local_address rem_address st ...\n"

    def tcp(self, *ports):
        lines = [
            f"   0: 00000000:{p:04X} 00000000:0000 0A 0:0 00:0 0 0 0 {1000 + p} 1 0 100 0 0 10 0\n"
            for p in ports
        ]
        return self.HEADER + "".join(lines)

    def test_missing_ipv6_file_is_fine(self):
        src = FakeSource({"/proc/net/tcp": self.tcp(22), "/proc/net/udp": self.HEADER})
        result = run(src, "ports", config(ports={"expected": ["22/tcp"]}))
        self.assertIs(result.status, Status.OK)

    def test_permission_note_when_owners_unknown(self):
        src = FakeSource({"/proc/net/tcp": self.tcp(22)}, denied=5)
        result = run(src, "ports")
        self.assertTrue(any("sudo" in n for n in result.notes))

    def test_no_note_when_all_owners_resolved(self):
        src = FakeSource({"/proc/net/tcp": self.tcp(22)}, owners={1022: "sshd(1)"}, denied=5)
        self.assertEqual(run(src, "ports").notes, [])


class RunChecksTest(unittest.TestCase):
    def test_permission_error_becomes_unknown_with_hint(self):
        src = FakeSource(
            {"/proc/meminfo": PermissionError(13, "Permission denied", "/proc/meminfo")}
        )
        result = run(src, "memory")
        self.assertIs(result.status, Status.UNKNOWN)
        self.assertIn("/proc/meminfo", result.notes[0])
        self.assertIn("sudo", result.notes[0])

    def test_missing_file_becomes_unknown(self):
        result = run(FakeSource(), "boot")
        self.assertIs(result.status, Status.UNKNOWN)

    def test_one_failure_does_not_stop_the_others(self):
        src = FakeSource({"/proc/meminfo": "MemTotal: 100 kB\nMemAvailable: 90 kB\n"})
        results = run_checks(src, config(), ["boot", "memory"])
        self.assertEqual([r.status for r in results], [Status.UNKNOWN, Status.OK])


if __name__ == "__main__":
    unittest.main()
