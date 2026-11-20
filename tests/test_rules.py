import unittest

from linux_health_check.rules import (
    parse_port_spec,
    port_findings,
    port_status,
    service_status,
    threshold_status,
    uptime_status,
)
from linux_health_check.status import Status, worst


class ThresholdStatusTest(unittest.TestCase):
    def test_below_warn_is_ok(self):
        self.assertIs(threshold_status(79.9, 80, 90), Status.OK)

    def test_reaching_warn_is_warning(self):
        self.assertIs(threshold_status(80, 80, 90), Status.WARNING)

    def test_between_warn_and_crit_is_warning(self):
        self.assertIs(threshold_status(89.99, 80, 90), Status.WARNING)

    def test_reaching_crit_is_critical(self):
        self.assertIs(threshold_status(90, 80, 90), Status.CRITICAL)

    def test_above_crit_is_critical(self):
        self.assertIs(threshold_status(100, 80, 90), Status.CRITICAL)

    def test_disabled_warn_jumps_to_critical_only(self):
        self.assertIs(threshold_status(85, None, 90), Status.OK)
        self.assertIs(threshold_status(95, None, 90), Status.CRITICAL)

    def test_disabled_crit_caps_at_warning(self):
        self.assertIs(threshold_status(99, 80, None), Status.WARNING)

    def test_both_disabled_is_always_ok(self):
        self.assertIs(threshold_status(100, None, None), Status.OK)

    def test_missing_value_is_unknown(self):
        self.assertIs(threshold_status(None, 80, 90), Status.UNKNOWN)

    def test_equal_warn_and_crit_is_critical(self):
        self.assertIs(threshold_status(90, 90, 90), Status.CRITICAL)


class WorstTest(unittest.TestCase):
    def test_empty_is_ok(self):
        self.assertIs(worst([]), Status.OK)

    def test_critical_beats_everything(self):
        self.assertIs(
            worst([Status.OK, Status.UNKNOWN, Status.WARNING, Status.CRITICAL]), Status.CRITICAL
        )

    def test_warning_beats_unknown(self):
        # An actionable problem must not be hidden behind missing data.
        self.assertIs(worst([Status.UNKNOWN, Status.WARNING]), Status.WARNING)

    def test_unknown_beats_ok(self):
        self.assertIs(worst([Status.OK, Status.UNKNOWN]), Status.UNKNOWN)

    def test_exit_codes_follow_plugin_convention(self):
        self.assertEqual([int(s) for s in Status], [0, 1, 2, 3])


class ServiceStatusTest(unittest.TestCase):
    def test_active_is_ok(self):
        self.assertEqual(service_status("loaded", "active"), (Status.OK, "active"))

    def test_failed_is_critical(self):
        self.assertIs(service_status("loaded", "failed")[0], Status.CRITICAL)

    def test_inactive_is_critical(self):
        self.assertIs(service_status("loaded", "inactive")[0], Status.CRITICAL)

    def test_transitional_states_are_warning(self):
        for state in ("activating", "reloading"):
            self.assertIs(service_status("loaded", state)[0], Status.WARNING, state)

    def test_masked_is_critical(self):
        self.assertIs(service_status("masked", "inactive")[0], Status.CRITICAL)

    def test_not_found_uses_policy(self):
        self.assertIs(service_status("not-found", "inactive")[0], Status.WARNING)
        self.assertIs(service_status("not-found", "inactive", Status.CRITICAL)[0], Status.CRITICAL)
        self.assertIs(service_status("not-found", "inactive", Status.OK)[0], Status.OK)

    def test_unexpected_state_is_unknown(self):
        self.assertIs(service_status("loaded", "")[0], Status.UNKNOWN)


class PortRulesTest(unittest.TestCase):
    def test_parse_port_spec(self):
        self.assertEqual(parse_port_spec("22"), (22, "tcp"))
        self.assertEqual(parse_port_spec("53/UDP"), (53, "udp"))
        self.assertEqual(parse_port_spec(" 443/tcp "), (443, "tcp"))

    def test_parse_port_spec_rejects_invalid(self):
        for bad in ("0", "65536", "ssh", "22/sctp", "", "-1"):
            with self.assertRaises(ValueError, msg=bad):
                parse_port_spec(bad)

    def test_missing_expected_port_is_critical(self):
        missing, unexpected = port_findings({(22, "tcp")}, [(22, "tcp"), (443, "tcp")], None)
        self.assertEqual(missing, [(443, "tcp")])
        self.assertIs(port_status(missing, unexpected), Status.CRITICAL)

    def test_protocol_matters(self):
        missing, _ = port_findings({(53, "tcp")}, [(53, "udp")], None)
        self.assertEqual(missing, [(53, "udp")])

    def test_port_outside_allow_list_is_warning(self):
        listening = {(22, "tcp"), (8080, "tcp")}
        missing, unexpected = port_findings(listening, [(22, "tcp")], [])
        self.assertEqual(unexpected, [(8080, "tcp")])
        self.assertIs(port_status(missing, unexpected), Status.WARNING)

    def test_expected_ports_are_implicitly_allowed(self):
        _, unexpected = port_findings({(22, "tcp")}, [(22, "tcp")], [])
        self.assertEqual(unexpected, [])

    def test_allow_list_disabled_reports_nothing_extra(self):
        _, unexpected = port_findings({(1, "tcp"), (2, "udp")}, [], None)
        self.assertEqual(unexpected, [])

    def test_all_good_is_ok(self):
        self.assertIs(port_status([], []), Status.OK)


class UptimeStatusTest(unittest.TestCase):
    def test_recent_reboot_warns(self):
        status, reason = uptime_status(5 * 60, 15, None)
        self.assertIs(status, Status.WARNING)
        self.assertIn("15 minutes", reason)

    def test_exactly_at_recent_limit_is_ok(self):
        self.assertIs(uptime_status(15 * 60, 15, None)[0], Status.OK)

    def test_long_uptime_warns_when_enabled(self):
        self.assertIs(uptime_status(91 * 86400, 15, 90)[0], Status.WARNING)

    def test_long_uptime_ignored_when_disabled(self):
        self.assertEqual(uptime_status(1000 * 86400, 15, None), (Status.OK, None))

    def test_recent_reboot_rule_can_be_disabled(self):
        self.assertIs(uptime_status(10, None, None)[0], Status.OK)


if __name__ == "__main__":
    unittest.main()
