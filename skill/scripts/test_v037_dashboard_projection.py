#!/usr/bin/env python3
"""Focused 0.3.7 regressions for read-only Dashboard state projection."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
RUNTIME = SKILL_ROOT / "assets" / "project-control" / "runtime"
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from vibe_runtime.dashboard import _checkpoint_rows, _coverage_projection  # noqa: E402
import test_v036_automation as v036  # noqa: E402


def _evidence(evidence_id: str, case_id: str) -> dict:
    return {
        "evidenceId": evidence_id,
        "caseId": case_id,
        "result": "PASS",
        "counters": {"executed": 1, "passed": 1, "failed": 0, "skipped": 0},
    }


def _coverage(mapping: dict[str, str]) -> dict:
    return {
        "integrity": {
            "checks": [
                {
                    "id": "HC-REQUIRED-CASE-COVERAGE",
                    "status": "PASS" if len(mapping) == 2 else "FAIL",
                    "message": "fixture coverage",
                    "details": {
                        "missing": sorted({"CASE-A", "CASE-B"} - set(mapping)),
                        "eligibleEvidenceByCase": mapping,
                    },
                }
            ]
        }
    }


def _contract() -> dict:
    return {
        "acceptanceCheckpoints": [
            {
                "id": "CP-MULTI",
                "type": "AUTOMATED",
                "statement": "both candidate-bound cases pass",
                "requiredForClaim": "VERIFIED",
                "caseIds": ["CASE-A", "CASE-B"],
                "expected": {
                    "status": "PASS",
                    "minExecuted": 2,
                    "maxFailed": 0,
                    "maxSkipped": 0,
                    "artifacts": "AS_DECLARED",
                },
                "notProven": [],
            }
        ]
    }


def _run(*args: str, cwd: Path | None = None, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _json_result(result: subprocess.CompletedProcess[str]) -> dict:
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"command did not emit JSON\nstdout={result.stdout}\nstderr={result.stderr}"
        ) from exc
    assert isinstance(value, dict)
    return value


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _evidence_inventory(root: Path) -> dict[str, str]:
    directory = root / ".vibe-control" / "evidence"
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.glob("*"))
        if path.is_file()
    }


def _portable_skill(parent: Path) -> Path:
    target = parent / "skill"
    shutil.copytree(
        SKILL_ROOT,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".vibe-control"),
    )
    built = _run(
        sys.executable,
        str(target / "scripts" / "build_manifest.py"),
        "--root",
        str(target),
        timeout=120,
    )
    if built.returncode != 0:
        raise AssertionError(
            f"temporary manifest rebuild failed\nstdout={built.stdout}\nstderr={built.stderr}"
        )
    return target


def test_multi_case_checkpoint_uses_only_controller_eligible_evidence() -> None:
    raw = [_evidence("E-A", "CASE-A"), _evidence("E-B-RAW-PASS", "CASE-B")]
    eligible = _coverage_projection(_coverage({"CASE-A": "E-A"}), raw)
    rows = _checkpoint_rows(_contract(), eligible, None)
    assert rows[0]["status"] == "BLOCKED", rows
    assert rows[0]["evidenceIds"] == ["E-A"], rows

    eligible = _coverage_projection(
        _coverage({"CASE-A": "E-A", "CASE-B": "E-B-RAW-PASS"}), raw
    )
    rows = _checkpoint_rows(_contract(), eligible, None)
    assert rows[0]["status"] == "PASS", rows
    assert rows[0]["evidenceIds"] == ["E-A", "E-B-RAW-PASS"], rows


def test_dashboard_projection_is_read_only_and_shows_declared_derived_drift() -> None:
    with tempfile.TemporaryDirectory(prefix="vc037-dashboard-", ignore_cleanup_errors=True) as name:
        base = Path(name)
        portable = _portable_skill(base)
        original_root, original_control = v036.ROOT, v036.CONTROL
        v036.ROOT = portable
        v036.CONTROL = portable / "assets" / "project-control" / "runtime" / "control.py"
        fixture = None
        try:
            fixture = v036.Fixture("AUTO_LOCAL_TO_REVIEW")
            state_path = fixture.root / ".vibe-control" / "stage-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            state.update(
                {
                    "phase": "DRAFT",
                    "health": "CLEAR",
                    "claimLevel": "DIAGNOSTIC",
                    "revision": 0,
                    "phaseHistory": [],
                }
            )
            v036.write_json(state_path, state)
            v036.git(fixture.root, "add", "-A")
            v036.git(fixture.root, "commit", "-m", "fixture: pre-transition state")

            output = fixture.base / "dashboard-readonly"
            stage_before = _sha(state_path)
            evidence_before = _evidence_inventory(fixture.root)
            result = _run(
                sys.executable,
                str(fixture.control),
                "dashboard",
                "--project",
                str(fixture.root),
                "--output-dir",
                str(output),
            )
            assert result.returncode == 0, result.stderr
            envelope = _json_result(result)
            snapshot = json.loads((output / "status.json").read_text(encoding="utf-8-sig"))
            assert _sha(state_path) == stage_before
            assert _evidence_inventory(fixture.root) == evidence_before
            assert envelope["state"]["declared"]["phase"] == "DRAFT", envelope
            assert envelope["state"]["derived"]["phase"] == "CONTRACT_LOCKED", envelope
            assert snapshot["stateDrift"]["detected"] is True, snapshot
            assert "phase" in snapshot["stateDrift"]["fields"], snapshot
            assert snapshot["phase"] == snapshot["derivedState"]["phase"]

            advanced = _run(
                sys.executable,
                str(fixture.control),
                "validate",
                "--project",
                str(fixture.root),
            )
            _json_result(advanced)
            advanced_state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            assert advanced_state["phase"] == "CONTRACT_LOCKED", advanced_state
            v036.git(fixture.root, "add", "-A")
            v036.git(fixture.root, "commit", "-m", "fixture: persist controller transition")

            advanced_state["health"] = "CLEAR"
            advanced_state["claimLevel"] = "RELEASE_READY"
            v036.write_json(state_path, advanced_state)
            v036.git(fixture.root, "add", "-A")
            v036.git(fixture.root, "commit", "-m", "fixture: stale clear state")
            stage_before = _sha(state_path)
            failed_output = fixture.base / "dashboard-failed"
            failed = _run(
                sys.executable,
                str(fixture.control),
                "dashboard",
                "--project",
                str(fixture.root),
                "--output-dir",
                str(failed_output),
            )
            assert failed.returncode == 0, failed.stderr
            failed_snapshot = json.loads(
                (failed_output / "status.json").read_text(encoding="utf-8-sig")
            )
            assert _sha(state_path) == stage_before
            assert failed_snapshot["declaredState"]["health"] == "CLEAR"
            assert failed_snapshot["derivedState"]["health"] == "FAILED"
            assert failed_snapshot["health"] == "FAILED"
            assert failed_snapshot["stateDrift"]["detected"] is True
        finally:
            if fixture is not None:
                fixture.close()
            v036.ROOT, v036.CONTROL = original_root, original_control


def main() -> int:
    tests = [
        (
            "multi-case-controller-eligible-evidence",
            test_multi_case_checkpoint_uses_only_controller_eligible_evidence,
        ),
        (
            "readonly-declared-derived-projection",
            test_dashboard_projection_is_read_only_and_shows_declared_derived_drift,
        ),
    ]
    results = []
    for case_id, test in tests:
        started = time.monotonic()
        try:
            test()
            results.append(
                {
                    "case": case_id,
                    "status": "PASS",
                    "durationSeconds": round(time.monotonic() - started, 3),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "case": case_id,
                    "status": "FAIL",
                    "durationSeconds": round(time.monotonic() - started, 3),
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                }
            )
    passed = sum(item["status"] == "PASS" for item in results)
    counters = {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "timedOut": 0,
        "skipped": 0,
    }
    report = {
        "test": "vibe-control-0.3.7-dashboard-projection",
        "status": "PASS" if passed == len(results) else "FAIL",
        "counters": counters,
        "cases": results,
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
