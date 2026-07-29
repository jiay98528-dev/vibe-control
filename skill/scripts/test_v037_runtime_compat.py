#!/usr/bin/env python3
"""Focused 0.3.7 regressions for execution, byte identity and case lifecycle."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "assets/project-control/runtime"
sys.path.insert(0, str(RUNTIME))

import vibe_runtime.controller as controller
from vibe_runtime.common import ControlError
from vibe_runtime.controller import (
    ADAPTER_TOOL_PROBE_TIMEOUT_SECONDS,
    EVIDENCE_BYTE_POLICY,
    attach_execution_cleanup_error,
    assert_candidate_case_lifecycle,
    content_ref,
    create_execution_worktree,
    evidence_byte_policy_check,
    execution_result,
    git_blob_ref_check,
    paths,
    remove_execution_worktree,
    resolve_executable,
    run_adapter_tool_probe,
    run_locked_command,
    write_evidence_byte_policy,
)
from vibe_runtime.positioning_control import coverage_check


def git(root: Path, *args: str) -> str:
    command = ["git"]
    if sys.platform == "win32":
        command.extend(["-c", "core.longpaths=true"])
    command.extend(["-C", str(root), *args])
    result = subprocess.run(
        command, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def native_path(path: Path) -> str:
    value = str(path.resolve())
    return f"\\\\?\\{value}" if sys.platform == "win32" and not value.startswith("\\\\?\\") else value


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


def test_adapter_tool_probe_has_bounded_cold_start_budget() -> None:
    assert ADAPTER_TOOL_PROBE_TIMEOUT_SECONDS == 180
    original = controller.run_locked_command
    observed: list[int | None] = []

    def successful_probe(command: list[str], execution_root: Path, *, timeout: int | None = None):
        observed.append(timeout)
        return subprocess.CompletedProcess(command, 0, "Version 1.0\n", ""), {
            "requestedExecutable": command[0],
            "resolvedExecutable": command[0],
            "hostPlatform": sys.platform,
        }

    controller.run_locked_command = successful_probe
    try:
        run, _ = run_adapter_tool_probe(["pnpm", "exec", "playwright", "--version"], Path.cwd())
        assert run.returncode == 0 and observed == [ADAPTER_TOOL_PROBE_TIMEOUT_SECONDS]

        def timed_out_probe(command: list[str], execution_root: Path, *, timeout: int | None = None):
            raise ControlError(
                "HC-EXECUTION-TIMEOUT", "synthetic timeout", status="BLOCKED",
                details={"requestedExecutable": command[0], "timeoutSeconds": timeout},
            )

        controller.run_locked_command = timed_out_probe
        try:
            run_adapter_tool_probe(["pnpm", "exec", "playwright", "--version"], Path.cwd())
        except ControlError as exc:
            assert exc.check_id == "HC-ADAPTER-TOOL-PROBE" and exc.status == "BLOCKED"
            assert exc.details["timeoutSeconds"] == ADAPTER_TOOL_PROBE_TIMEOUT_SECONDS
        else:
            raise AssertionError("adapter tool probe timeout was accepted")
    finally:
        controller.run_locked_command = original


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


def test_execution_worktree_longpaths_and_cleanup() -> None:
    with tempfile.TemporaryDirectory(prefix="vc037-worktree-source-", ignore_cleanup_errors=True) as name:
        root = Path(name) / "source"
        root.mkdir()
        git(root, "init")
        git(root, "config", "user.email", "fixture@example.invalid")
        git(root, "config", "user.name", "Fixture")
        long_relative = Path(*[f"tracked-segment-{index:02d}-abcdef" for index in range(9)]) / "fixture.txt"
        tracked = root / long_relative
        os.makedirs(native_path(tracked.parent), exist_ok=True)
        with open(native_path(tracked), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("candidate bytes\n")
        git(root, "add", "-A")
        git(root, "commit", "-m", "fixture candidate")
        commit = git(root, "rev-parse", "HEAD")

        parent, worktree = create_execution_worktree(root, commit)
        try:
            candidate_file = worktree / long_relative
            assert len(str(candidate_file)) > 260
            with open(native_path(candidate_file), encoding="utf-8") as handle:
                assert handle.read() == "candidate bytes\n"
            if sys.platform == "win32":
                assert str(worktree).lower().startswith((Path(worktree.anchor) / "vce").as_posix().replace("/", "\\").lower())
            generated = worktree / "node_modules" / Path(*[f"generated-segment-{index:02d}-abcdef" for index in range(9)]) / "cache.bin"
            assert len(str(generated)) > 260
            os.makedirs(native_path(generated.parent), exist_ok=True)
            with open(native_path(generated), "wb") as handle:
                handle.write(b"generated and untracked")
        finally:
            remove_execution_worktree(root, parent, worktree)
        assert not parent.exists()
        assert str(worktree.resolve()) not in git(root, "worktree", "list", "--porcelain")


def test_cleanup_failure_does_not_mask_primary_error() -> None:
    cleanup = ControlError("HC-EXECUTION-WORKTREE-CLEANUP", "synthetic cleanup failure", status="BLOCKED")
    primary = ControlError("HC-PRIMARY-EXECUTION", "primary execution failure", status="FAIL")
    attach_execution_cleanup_error(primary, cleanup)
    assert primary.check_id == "HC-PRIMARY-EXECUTION"
    assert primary.details["executionWorktreeCleanup"]["id"] == "HC-EXECUTION-WORKTREE-CLEANUP"

    failed_case = execution_result(["evidence.json"], ["FAIL"], cleanup)
    assert failed_case["status"] == "FAIL"
    assert [item["id"] for item in failed_case["integrity"]["checks"]] == ["HC-EXECUTE", "HC-EXECUTION-WORKTREE-CLEANUP"]
    try:
        execution_result(["evidence.json"], ["PASS"], cleanup)
    except ControlError as exc:
        assert exc.check_id == "HC-EXECUTION-WORKTREE-CLEANUP" and exc.status == "BLOCKED"
    else:
        raise AssertionError("successful execution ignored cleanup failure")


def test_cleanup_fails_closed_when_registration_remains() -> None:
    with tempfile.TemporaryDirectory(prefix="vc037-registration-", ignore_cleanup_errors=True) as name:
        root = Path(name) / "source"
        root.mkdir()
        git(root, "init")
        git(root, "config", "user.email", "fixture@example.invalid")
        git(root, "config", "user.name", "Fixture")
        (root / "fixture.txt").write_text("fixture\n", encoding="utf-8", newline="\n")
        git(root, "add", "-A")
        git(root, "commit", "-m", "fixture candidate")
        parent, worktree = create_execution_worktree(root, git(root, "rev-parse", "HEAD"))
        original_git = controller._execution_worktree_git

        def fail_remove_and_prune(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
            if args and args[0] in {"remove", "prune"}:
                return subprocess.CompletedProcess(["git", *args], 1, "", "synthetic Git cleanup failure")
            return original_git(project, *args)

        controller._execution_worktree_git = fail_remove_and_prune
        try:
            try:
                remove_execution_worktree(root, parent, worktree)
            except ControlError as exc:
                assert exc.check_id == "HC-EXECUTION-WORKTREE-CLEANUP" and exc.status == "BLOCKED"
            else:
                raise AssertionError("remaining worktree registration was accepted")
        finally:
            controller._execution_worktree_git = original_git
            original_git(root, "prune", "--expire", "now")


def main() -> int:
    tests = [
        test_windows_resolution_and_argument_boundary,
        test_adapter_tool_probe_has_bounded_cold_start_budget,
        test_evidence_git_byte_policy_round_trip,
        test_case_lifecycle_is_not_candidate_coverage,
        test_execution_worktree_longpaths_and_cleanup,
        test_cleanup_failure_does_not_mask_primary_error,
        test_cleanup_fails_closed_when_registration_remains,
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
