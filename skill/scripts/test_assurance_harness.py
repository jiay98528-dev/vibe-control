#!/usr/bin/env python3
"""Fast behavioral checks for the supervised assurance-suite protocol."""
from __future__ import annotations

import json
import hashlib
import subprocess
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

        shared = suite.run_supervised_command(
            "synthetic-shared-input",
            root,
            [
                sys.executable,
                "-c",
                "import json,os; print(json.dumps({'test':'synthetic-shared-input','status':'PASS' if os.environ.get('VC_SHARED')=='bound' else 'FAIL'}))",
            ],
            10,
            env_overrides={"VC_SHARED": "bound"},
        )
        if shared.get("status") != "PASS":
            raise AssertionError(f"bounded worker did not receive its read-only shared input binding: {shared}")

        try:
            suite.run_supervised_command(
                "synthetic-reserved-env",
                root,
                [sys.executable, "-c", "print('should not run')"],
                10,
                env_overrides={"TEMP": "escape"},
            )
        except ValueError as exc:
            if "runner isolation variables" not in str(exc):
                raise
        else:
            raise AssertionError("bounded worker allowed its isolated TEMP root to be replaced")

        invalid_descriptor = root / "invalid-shared-package.json"
        invalid_descriptor.write_text("{}\n", encoding="utf-8")
        invalid_result = subprocess.run(
            [
                sys.executable, str(Path(suite.__file__).resolve()), "--case", "test_cli_error_surface_is_stable",
                suite.fx.SHARED_TEST_PACKAGE_DESCRIPTOR_ARG, str(invalid_descriptor),
                suite.fx.SHARED_TEST_PACKAGE_DESCRIPTOR_SHA_ARG,
                hashlib.sha256(invalid_descriptor.read_bytes()).hexdigest(),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )
        invalid_lines = [line for line in invalid_result.stdout.splitlines() if line.strip()]
        invalid_report = json.loads(invalid_lines[0]) if len(invalid_lines) == 1 else {}
        if (
            invalid_result.returncode != 1
            or invalid_report.get("checkId") != "ASSURANCE-SHARED-FIXTURE-SETUP"
            or invalid_result.stderr
        ):
            raise AssertionError(f"invalid shared descriptor did not produce stable JSON: {invalid_result}")

        post_results: list[dict] = []
        accepted = suite.append_shared_fixture_postcheck(
            post_results, invalid_descriptor, "0" * 64,
            verifier=lambda *_: (_ for _ in ()).throw(AssertionError("post-run drift")),
        )
        if accepted or post_results != [{
            "test": "shared-package-post-verify",
            "status": "FAIL",
            "checkId": "ASSURANCE-SHARED-FIXTURE-POST-VERIFY",
            "errorType": "AssertionError",
            "error": "post-run drift",
        }]:
            raise AssertionError(f"post-run shared fixture drift lacked a stable result: {post_results}")

        timed_out = suite.run_supervised_command(
            "synthetic-timeout",
            root,
            [sys.executable, "-c", "import time; time.sleep(30)"],
            1,
        )
        if timed_out.get("checkId") != "ASSURANCE-CASE-TIMEOUT" or timed_out.get("status") != "TIMEOUT":
            raise AssertionError(f"timeout did not fail closed: {timed_out}")

        report = suite.build_report([passed, shared, malformed, timed_out], 1.0)
        expected = {"total": 4, "passed": 2, "failed": 1, "timedOut": 1, "skipped": 0}
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
