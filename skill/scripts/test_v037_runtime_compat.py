#!/usr/bin/env python3
"""Focused 0.3.7 regressions for execution, byte identity and case lifecycle."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "assets/project-control/runtime"
sys.path.insert(0, str(RUNTIME))

from vibe_runtime.common import ControlError
from vibe_runtime.controller import (
    EVIDENCE_BYTE_POLICY,
    assert_candidate_case_lifecycle,
    content_ref,
    evidence_byte_policy_check,
    git_blob_ref_check,
    paths,
    resolve_executable,
    run_locked_command,
    write_evidence_byte_policy,
)
from vibe_runtime.positioning_control import coverage_check


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def test_windows_resolution_and_argument_boundary() -> None:
    resolved, metadata = resolve_executable([sys.executable, "--version"], Path.cwd())
    assert Path(resolved[0]).is_absolute()
    assert metadata["requestedExecutable"] == sys.executable
    if sys.platform == "win32" and shutil.which("pnpm"):
        pnpm, observed = resolve_executable(["pnpm", "--version"], Path.cwd())
        assert pnpm[0].lower().endswith((".cmd", ".exe")), pnpm
        assert observed["requestedExecutable"] == "pnpm"
    injected = "value; echo SHOULD_NOT_EXECUTE"
    run, _ = run_locked_command(
        [sys.executable, "-c", "import sys; print(sys.argv[1])", injected], Path.cwd(),
    )
    assert run.returncode == 0 and run.stdout.strip() == injected
    try:
        resolve_executable(["definitely-missing-vibe-control-037"], Path.cwd())
    except ControlError as exc:
        assert exc.check_id == "HC-EXECUTABLE-RESOLUTION" and exc.status == "BLOCKED"
    else:
        raise AssertionError("missing executable was accepted")


def test_evidence_git_byte_policy_round_trip() -> None:
    with tempfile.TemporaryDirectory(prefix="vc037-bytes-", ignore_cleanup_errors=True) as name:
        root = Path(name) / "source"
        root.mkdir()
        git(root, "init")
        git(root, "config", "user.email", "fixture@example.invalid")
        git(root, "config", "user.name", "Fixture")
        (root / ".gitattributes").write_text("* text=auto eol=lf\n", encoding="utf-8", newline="\n")
        control_paths = paths(root)
        write_evidence_byte_policy(control_paths["evidence_byte_policy"])
        evidence = control_paths["evidence"] / "artifact.json"
        evidence.parent.mkdir(parents=True)
        payload = b'{\r\n  "status": "PASS"\r\n}\r\n'
        evidence.write_bytes(payload)
        git(root, "add", "-A")
        git(root, "commit", "-m", "fixture evidence")
        assert control_paths["evidence_byte_policy"].read_bytes() == EVIDENCE_BYTE_POLICY
        assert evidence_byte_policy_check(root, control_paths)["status"] == "PASS"
        ref = content_ref(root, evidence)
        assert git_blob_ref_check(root, ref)["status"] == "PASS"
        clone = Path(name) / "clone"
        subprocess.run(["git", "-c", "core.autocrlf=true", "clone", str(root), str(clone)], check=True, capture_output=True)
        clone_payload = (clone / ".vibe-control/evidence/artifact.json").read_bytes()
        assert clone_payload == payload
        assert hashlib.sha256(clone_payload).hexdigest() == ref["sha256"]

        control_paths["evidence_byte_policy"].unlink()
        git(root, "add", "-A")
        git(root, "commit", "-m", "remove byte policy")
        assert evidence_byte_policy_check(root, control_paths)["status"] == "BLOCKED"


def test_case_lifecycle_is_not_candidate_coverage() -> None:
    compiled = {
        "canonical": {
            "layers": [{"id": "RULE-001", "rule": {"caseCapabilities": ["runtime-proof"]}}],
            "runtimeAdapters": [{"id": "generic-command", "provesCaseCapabilities": ["runtime-proof"]}],
        }
    }
    base = {
        "id": "CASE-001",
        "adapter": {"id": "generic-command"},
        "satisfiesRuleIds": ["RULE-001"],
        "capabilities": ["runtime-proof"],
    }
    diagnostic = {**base, "lifecycle": "BOOTSTRAP_DIAGNOSTIC"}
    assert coverage_check(compiled, [diagnostic])["status"] == "FAIL"
    try:
        assert_candidate_case_lifecycle([diagnostic])
    except ControlError as exc:
        assert exc.check_id == "HC-CASE-LIFECYCLE-SCOPE" and exc.status == "BLOCKED"
    else:
        raise AssertionError("bootstrap diagnostic entered candidate scope")
    assert coverage_check(compiled, [{**base, "lifecycle": "CANDIDATE_EXECUTION"}])["status"] == "PASS"


def main() -> int:
    tests = [
        test_windows_resolution_and_argument_boundary,
        test_evidence_git_byte_policy_round_trip,
        test_case_lifecycle_is_not_candidate_coverage,
    ]
    failures = []
    for test in tests:
        try:
            test()
        except Exception as exc:  # pragma: no cover - top-level deterministic report
            failures.append({"case": test.__name__, "error": repr(exc)})
    report = {
        "status": "PASS" if not failures else "FAIL",
        "counters": {"total": len(tests), "passed": len(tests) - len(failures), "failed": len(failures), "skipped": 0, "timedOut": 0},
        "failures": failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
