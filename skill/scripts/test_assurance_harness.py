#!/usr/bin/env python3
"""Fast behavioral checks for the supervised assurance-suite protocol."""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

import test_assurance_regressions as suite


def test_assurance_runner_protocol() -> None:
    with tempfile.TemporaryDirectory(prefix="vibe-control-assurance-harness-", ignore_cleanup_errors=True) as temp:
        root = Path(temp)
        passed = suite.run_supervised_command(
            "synthetic-pass",
            root,
            [sys.executable, "-c", "import json; print(json.dumps({'test':'synthetic-pass','status':'PASS'}))"],
            10,
        )
        if passed.get("status") != "PASS" or passed.get("workerExitCode") != 0:
            raise AssertionError(f"valid worker protocol rejected: {passed}")

        malformed = suite.run_supervised_command(
            "synthetic-malformed",
            root,
            [sys.executable, "-c", "print('not-json')"],
            10,
        )
        if malformed.get("checkId") != "ASSURANCE-CASE-PROTOCOL" or malformed.get("status") != "FAIL":
            raise AssertionError(f"malformed worker did not fail closed: {malformed}")

        timed_out = suite.run_supervised_command(
            "synthetic-timeout",
            root,
            [sys.executable, "-c", "import time; time.sleep(30)"],
            1,
        )
        if timed_out.get("checkId") != "ASSURANCE-CASE-TIMEOUT" or timed_out.get("status") != "TIMEOUT":
            raise AssertionError(f"timeout did not fail closed: {timed_out}")

        report = suite.build_report([passed, malformed, timed_out], 1.0)
        expected = {"total": 3, "passed": 1, "failed": 1, "timedOut": 1, "skipped": 0}
        if report["status"] != "FAIL" or report["formalClaimsAllowed"] is not False or report["counters"] != expected:
            raise AssertionError(f"aggregate counters do not conserve results: {report}")
        all_pass = suite.build_report([passed], 1.0)
        if all_pass["formalClaimsAllowed"] is not suite.fx.package_formal_enabled():
            raise AssertionError(f"package readiness was not reflected in the suite report: {all_pass}")

        suite_timeout_started = time.monotonic()
        suite_results, _ = suite.bounded.run_suite(
            ["suite-timeout-a", "suite-timeout-b"],
            command_for=lambda name: [sys.executable, "-c", "import time; time.sleep(30)"],
            temp_root=root / "suite-timeout",
            jobs=1,
            case_timeout=20,
            suite_timeout=1,
            identity_field="test",
            protocol_id="ASSURANCE-CASE-PROTOCOL",
            timeout_id="ASSURANCE-CASE-TIMEOUT",
            suite_timeout_id="ASSURANCE-SUITE-TIMEOUT",
        )
        suite_counts = suite.bounded.counters(suite_results)
        if suite_counts != {"total": 2, "passed": 0, "failed": 0, "timedOut": 2, "skipped": 0}:
            raise AssertionError(f"suite timeout counters do not conserve results: {suite_results}")
        if not all(item.get("checkId") == "ASSURANCE-SUITE-TIMEOUT" for item in suite_results):
            raise AssertionError(f"suite timeout did not use its stable ID: {suite_results}")
        suite_timeout_elapsed = time.monotonic() - suite_timeout_started
        if suite_timeout_elapsed > suite.bounded.SUITE_CLEANUP_BUDGET_SECONDS + 5:
            raise AssertionError(f"suite timeout exceeded bounded cleanup budget: {suite_timeout_elapsed:.3f}s")


def main() -> int:
    try:
        test_assurance_runner_protocol()
    except Exception as exc:
        print(json.dumps({
            "status": "FAIL",
            "test": "test_assurance_runner_protocol",
            "counters": {"total": 1, "passed": 0, "failed": 1},
            "error": str(exc),
        }, ensure_ascii=False))
        return 1
    print(json.dumps({
        "status": "PASS",
        "test": "test_assurance_runner_protocol",
        "counters": {"total": 1, "passed": 1, "failed": 0},
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
