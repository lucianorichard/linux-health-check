"""End-to-end tests of the CLI against the sample systems in examples/."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from linux_health_check.cli import main

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
CONFIG = str(EXAMPLES / "config.example.json")
HEALTHY = str(EXAMPLES / "healthy")
DEGRADED = str(EXAMPLES / "degraded")


def run_cli(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(args))
        except SystemExit as exc:  # argparse exits directly
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def test_healthy_system_exits_0(self):
        code, out, _ = run_cli("-c", CONFIG, "--fixture", HEALTHY, "--no-color")
        self.assertEqual(code, 0, out)
        self.assertIn("Overall: OK (exit code 0)", out)

    def test_degraded_system_exits_2(self):
        code, out, _ = run_cli("-c", CONFIG, "--fixture", DEGRADED, "--no-color")
        self.assertEqual(code, 2)
        self.assertIn("[CRIT] disk", out)
        self.assertIn("nginx.service: failed", out)
        self.assertIn("missing: 443/tcp", out)
        self.assertIn("not allowed: 8080/tcp", out)
        self.assertIn("permission denied", out)

    def test_only_selected_checks_run(self):
        code, out, _ = run_cli("--fixture", DEGRADED, "--check", "memory", "--json")
        report = json.loads(out)
        self.assertEqual([c["name"] for c in report["checks"]], ["memory"])
        self.assertEqual((code, report["overall"]), (1, "WARNING"))

    def test_json_report_shape(self):
        code, out, _ = run_cli("-c", CONFIG, "--fixture", DEGRADED, "--json")
        report = json.loads(out)
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["host"]["hostname"], "db02.example.com")
        self.assertEqual(report["exit_code"], code)
        self.assertEqual(
            [c["name"] for c in report["checks"]],
            ["cpu", "memory", "disk", "services", "ports", "boot"],
        )
        for check in report["checks"]:
            self.assertEqual(
                set(check), {"name", "status", "summary", "details", "metrics", "notes"}
            )
            self.assertIn(check["status"], {"OK", "WARNING", "CRITICAL", "UNKNOWN"})

    def test_output_file_is_written_alongside_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "report.json")
            code, out, _ = run_cli("--fixture", HEALTHY, "-o", path, "--no-color")
            self.assertIn("Overall:", out)
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["exit_code"], code)

    def test_quiet_is_one_line(self):
        code, out, _ = run_cli("-c", CONFIG, "--fixture", DEGRADED, "-q")
        self.assertEqual(out.count("\n"), 1)
        self.assertTrue(out.startswith("CRITICAL: "))

    def test_cli_service_and_port_are_added(self):
        code, out, _ = run_cli(
            "--fixture", HEALTHY, "-s", "missing.service", "-p", "8443", "--json"
        )
        report = {c["name"]: c for c in json.loads(out)["checks"]}
        self.assertEqual(report["services"]["status"], "WARNING")
        self.assertEqual(report["ports"]["metrics"]["missing"], ["8443/tcp"])
        self.assertEqual(code, 2)

    def test_invalid_config_exits_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"memory": {"warn": 99, "crit": 50}}, fh)
            code, _, err = run_cli("-c", path, "--fixture", HEALTHY)
        self.assertEqual(code, 3)
        self.assertIn("invalid configuration", err)

    def test_invalid_cli_port_exits_3(self):
        code, _, err = run_cli("--fixture", HEALTHY, "-p", "70000")
        self.assertEqual(code, 3)
        self.assertIn("70000", err)

    def test_unknown_argument_exits_3_not_2(self):
        code, _, _ = run_cli("--bogus")
        self.assertEqual(code, 3)

    def test_missing_fixture_exits_3(self):
        code, _, err = run_cli("--fixture", "/nonexistent/fixture")
        self.assertEqual(code, 3)
        self.assertIn("not found", err)

    def test_unwritable_output_exits_3(self):
        code, _, err = run_cli("--fixture", HEALTHY, "-o", "/nonexistent/dir/report.json")
        self.assertEqual(code, 3)
        self.assertIn("cannot write", err)


if __name__ == "__main__":
    unittest.main()
