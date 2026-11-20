import unittest

from linux_health_check.parsers import (
    Listener,
    Mount,
    count_cpus,
    parse_btime,
    parse_cpu_times,
    parse_key_values,
    parse_loadavg,
    parse_meminfo,
    parse_mounts,
    parse_proc_net,
    parse_uptime,
)

HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"


def sock(local, remote, state, inode):
    return f"   0: {local} {remote} {state} 00000000:00000000 00:00000000 00000000  0  0 {inode} 1 0 100 0 0 10 0\n"


STAT = """cpu  100 5 50 800 20 1 2 3 9 9
cpu0 50 2 25 400 10 0 1 1 0 0
cpu1 50 3 25 400 10 1 1 2 0 0
intr 12345
btime 1700000000
"""


class ProcStatTest(unittest.TestCase):
    def test_cpu_times_exclude_guest_fields(self):
        idle, total = parse_cpu_times(STAT)
        self.assertEqual(idle, 820)  # idle + iowait
        self.assertEqual(total, 100 + 5 + 50 + 800 + 20 + 1 + 2 + 3)

    def test_cpu_times_tolerate_short_lines(self):
        self.assertEqual(parse_cpu_times("cpu  1 2 3 4\n"), (4, 10))

    def test_cpu_times_require_aggregate_line(self):
        with self.assertRaises(ValueError):
            parse_cpu_times("cpu0 1 2 3 4\n")

    def test_count_cpus(self):
        self.assertEqual(count_cpus(STAT), 2)

    def test_btime(self):
        self.assertEqual(parse_btime(STAT), 1700000000)


class SimpleFilesTest(unittest.TestCase):
    def test_loadavg(self):
        self.assertEqual(parse_loadavg("0.50 1.25 2.00 3/400 1234\n"), (0.5, 1.25, 2.0))

    def test_loadavg_invalid(self):
        with self.assertRaises(ValueError):
            parse_loadavg("0.5\n")

    def test_uptime(self):
        self.assertEqual(parse_uptime("12345.67 99999.00\n"), 12345.67)

    def test_meminfo(self):
        text = "MemTotal:       16000 kB\nMemAvailable:    8000 kB\nHugePages_Total:       0\nbogus line\n"
        self.assertEqual(
            parse_meminfo(text), {"MemTotal": 16000, "MemAvailable": 8000, "HugePages_Total": 0}
        )

    def test_key_values(self):
        text = "LoadState=loaded\nActiveState=active\nDescription=A=B\n\n"
        self.assertEqual(
            parse_key_values(text),
            {"LoadState": "loaded", "ActiveState": "active", "Description": "A=B"},
        )


class MountsTest(unittest.TestCase):
    def test_octal_escapes_are_decoded(self):
        mounts = parse_mounts("/dev/sdb1 /mnt/my\\040disk ext4 rw 0 0\n")
        self.assertEqual(mounts, [Mount("/dev/sdb1", "/mnt/my disk", "ext4")])

    def test_short_lines_are_ignored(self):
        self.assertEqual(parse_mounts("garbage\n\n"), [])


class ProcNetTest(unittest.TestCase):
    def test_tcp_only_listen_state(self):
        text = (
            HEADER
            + sock("00000000:0016", "00000000:0000", "0A", 11)
            + sock("0500000A:0016", "0900000A:C738", "01", 12)
        )
        self.assertEqual(parse_proc_net(text, "tcp"), [Listener("tcp", "0.0.0.0", 22, 11)])

    def test_ipv4_is_little_endian(self):
        text = HEADER + sock("0100007F:1538", "00000000:0000", "0A", 5)
        self.assertEqual(parse_proc_net(text, "tcp")[0].address, "127.0.0.1")
        self.assertEqual(parse_proc_net(text, "tcp")[0].port, 5432)

    def test_ipv6_addresses(self):
        any6 = "0" * 32
        loop6 = "00000000000000000000000001000000"
        text = (
            HEADER
            + sock(f"{any6}:0050", f"{any6}:0000", "0A", 7)
            + sock(f"{loop6}:0277", f"{any6}:0000", "0A", 8)
        )
        result = parse_proc_net(text, "tcp6")
        self.assertEqual(
            [(r.address, r.port, r.proto) for r in result], [("::", 80, "tcp"), ("::1", 631, "tcp")]
        )

    def test_udp_only_unconnected_sockets(self):
        text = (
            HEADER
            + sock("00000000:007B", "00000000:0000", "07", 1)
            + sock("0500000A:D431", "08080808:0035", "01", 2)
        )
        self.assertEqual(parse_proc_net(text, "udp"), [Listener("udp", "0.0.0.0", 123, 1)])

    def test_unknown_protocol(self):
        with self.assertRaises(ValueError):
            parse_proc_net(HEADER, "sctp")


if __name__ == "__main__":
    unittest.main()
