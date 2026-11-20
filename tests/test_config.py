import json
import os
import tempfile
import unittest

from linux_health_check.config import DEFAULTS, ConfigError, load_config


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, data):
        path = os.path.join(self.tmp.name, "config.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(data if isinstance(data, str) else json.dumps(data))
        return path

    def assertConfigError(self, data, fragment):
        with self.assertRaises(ConfigError) as ctx:
            load_config(self.write(data))
        self.assertIn(fragment, str(ctx.exception))

    def test_defaults_are_valid(self):
        self.assertEqual(load_config(), DEFAULTS)

    def test_partial_override_keeps_other_defaults(self):
        cfg = load_config(self.write({"disk": {"space_warn": 70}}))
        self.assertEqual(cfg["disk"]["space_warn"], 70)
        self.assertEqual(cfg["disk"]["space_crit"], DEFAULTS["disk"]["space_crit"])
        self.assertEqual(cfg["memory"], DEFAULTS["memory"])

    def test_defaults_are_not_mutated(self):
        load_config(self.write({"services": {"units": ["ssh.service"]}}))
        self.assertEqual(DEFAULTS["services"]["units"], [])

    def test_null_disables_a_threshold(self):
        cfg = load_config(self.write({"memory": {"warn": None}}))
        self.assertIsNone(cfg["memory"]["warn"])

    def test_unknown_key_is_rejected(self):
        self.assertConfigError({"disk": {"space_crti": 95}}, "disk.space_crti")
        self.assertConfigError({"network": {}}, "network")

    def test_warn_greater_than_crit_is_rejected(self):
        self.assertConfigError({"memory": {"warn": 96, "crit": 90}}, "memory.warn")

    def test_percent_above_100_is_rejected(self):
        self.assertConfigError({"disk": {"space_crit": 150}}, "at most 100")

    def test_load_has_no_upper_bound(self):
        cfg = load_config(self.write({"cpu": {"load_per_core_warn": 4, "load_per_core_crit": 8}}))
        self.assertEqual(cfg["cpu"]["load_per_core_crit"], 8)

    def test_wrong_types_are_rejected(self):
        self.assertConfigError({"memory": {"warn": "85"}}, "memory.warn")
        self.assertConfigError({"memory": {"warn": True}}, "memory.warn")
        self.assertConfigError({"services": {"units": "ssh.service"}}, "services.units")
        self.assertConfigError({"cpu": 5}, "must be an object")

    def test_invalid_port_is_rejected(self):
        self.assertConfigError({"ports": {"expected": ["99999/tcp"]}}, "99999")
        self.assertConfigError({"ports": {"allowed": ["22/sctp"]}}, "sctp")

    def test_invalid_not_found_policy(self):
        self.assertConfigError({"services": {"not_found": "ignore"}}, "not_found")

    def test_invalid_json(self):
        self.assertConfigError("{not json", "not valid JSON")

    def test_top_level_must_be_object(self):
        self.assertConfigError("[]", "JSON object")

    def test_missing_file(self):
        with self.assertRaises(ConfigError):
            load_config(os.path.join(self.tmp.name, "missing.json"))


if __name__ == "__main__":
    unittest.main()
