#!/usr/bin/env python3
"""Supplementary side-effect and stop-boundary regressions for vibe-control 0.3.6."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import test_v036_automation as base


def _file_ref(root: Path, path: Path) -> dict:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "tracked": True,
    }


def _materialize_034_runtime(fixture: base.Fixture) -> Path:
    control = fixture.root / ".vibe-control"
    source = control / "runtime/0.3.6"
    target = control / "runtime/0.3.4"
    source.rename(target)
    compiler = target / "vibe_runtime/project_rules.py"
    text = compiler.read_text(encoding="utf-8")
    nested = '            "**/playwright.config.ts", "**/playwright.config.js", "**/playwright.config.mjs", "**/playwright.config.cjs",\n'
    assert nested in text
    compiler.write_text(text.replace(nested, "", 1), encoding="utf-8", newline="\n")
    assert hashlib.sha256(compiler.read_bytes()).hexdigest() == "6152ee606ab1292327df94474d1b6b0eb14a080a00f6622d2e0cd39bc067b293"

    manifest_path = target / "runtime-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["runtimeVersion"] = "0.3.4"
    for entry in manifest["files"]:
        path = target / entry["path"]
        entry["bytes"] = path.stat().st_size
        entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    base.write_json(manifest_path, manifest)

    lock_path = control / "project-governance-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["packageBinding"]["version"] = "0.3.4"
    lock["packageBinding"]["runtimeManifest"] = _file_ref(fixture.root, manifest_path)
    lock["runtime"] = _file_ref(fixture.root, manifest_path)
    lock["ruleCompiler"] = _file_ref(fixture.root, compiler)
    lock["profileDirectory"] = _file_ref(fixture.root, target / "rules/v1/profiles.json")
    lock["adapterDirectory"] = _file_ref(fixture.root, target / "rules/v1/adapters.json")
    base.write_json(lock_path, lock)

    resolved_path = control / "resolved-rule-set.json"
    resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
    resolved["compiler"]["version"] = "0.3.4"
    resolved["compiler"]["sha256"] = lock["ruleCompiler"]["sha256"]
    base.write_json(resolved_path, resolved)
    lock["resolvedRuleSet"] = _file_ref(fixture.root, resolved_path)
    base.write_json(lock_path, lock)

    shutil.rmtree(control / "task-locks")
    state_path = control / "stage-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update({"phase": "DRAFT", "health": "BLOCKED", "claimLevel": "DIAGNOSTIC", "taskId": None, "candidateId": None, "revision": 0, "phaseHistory": []})
    base.write_json(state_path, state)
    base.git(fixture.root, "add", "-A")
    base.git(fixture.root, "commit", "-m", "pin legacy runtime")
    return target


def test_commit_and_push_are_real_bounded_side_effects() -> None:
    local = base.Fixture("AUTO_LOCAL_TO_REVIEW")
    try:
        before = base.git(local.root, "rev-parse", "HEAD")
        (local.root / "fixture.py").write_text("print('OK')\n", encoding="utf-8", newline="\n")
        _, result = local.command("automation", "--action", "commit")
        after = base.git(local.root, "rev-parse", "HEAD")
        assert result["status"] == "PASS" and result["data"]["milestoneCommit"] == after != before
        assert not base.git(local.root, "status", "--porcelain=v1")
    finally:
        local.close()

    pushed = base.Fixture("AUTO_PUSH_TO_REVIEW", remote=True)
    try:
        before_remote = base.git(pushed.root, "ls-remote", "--heads", "origin", "refs/heads/master").split()[0]
        head = base.git(pushed.root, "rev-parse", "HEAD")
        assert before_remote != head
        _, result = pushed.command("automation", "--action", "push")
        after_remote = base.git(pushed.root, "ls-remote", "--heads", "origin", "refs/heads/master").split()[0]
        assert result["status"] == "PASS" and result["data"]["pushedCommit"] == after_remote == head
    finally:
        pushed.close()


def test_clean_forbidden_commit_cannot_be_pushed() -> None:
    fixture = base.Fixture("AUTO_PUSH_TO_REVIEW", remote=True)
    try:
        before_remote = base.git(fixture.root, "ls-remote", "--heads", "origin", "refs/heads/master").split()[0]
        forbidden = fixture.root / "forbidden.txt"
        forbidden.write_text("must not cross the task boundary\n", encoding="utf-8", newline="\n")
        base.git(fixture.root, "add", "forbidden.txt")
        base.git(fixture.root, "commit", "-m", "pre-created forbidden commit")
        assert not base.git(fixture.root, "status", "--porcelain=v1")
        _, result = fixture.command("automation", "--action", "push")
        after_remote = base.git(fixture.root, "ls-remote", "--heads", "origin", "refs/heads/master").split()[0]
        assert result["status"] == "BLOCKED" and result["error"]["id"] == "HC-AUTOMATION-PUSH-SCOPE", result
        assert after_remote == before_remote

        forbidden.unlink()
        base.git(fixture.root, "add", "forbidden.txt")
        base.git(fixture.root, "commit", "-m", "remove forbidden file")
        _, history = fixture.command("automation", "--action", "push")
        assert history["status"] == "BLOCKED" and history["error"]["id"] == "HC-AUTOMATION-PUSH-SCOPE", history
        assert base.git(fixture.root, "ls-remote", "--heads", "origin", "refs/heads/master").split()[0] == before_remote
    finally:
        fixture.close()


def test_boundary_drift_staged_changes_and_unknown_control_paths_stop() -> None:
    fixture = base.Fixture("AUTO_LOCAL_TO_REVIEW")
    try:
        objective = fixture.root / "KEY_OBJECTIVES.md"
        original = objective.read_bytes(); objective.write_bytes(original + b"\nchanged\n")
        _, drift = fixture.command("automation", "--action", "dispatch")
        assert drift["status"] == "BLOCKED" and drift["error"]["id"] == "HC-AUTOMATION-BOUNDARY-CHANGE", drift
        objective.write_bytes(original)

        product = fixture.root / "fixture.py"; product.write_text("print('OK')\n", encoding="utf-8", newline="\n")
        base.git(fixture.root, "add", "fixture.py")
        _, staged = fixture.command("automation", "--action", "commit")
        assert staged["status"] == "BLOCKED" and staged["error"]["id"] == "HC-AUTOMATION-WORKTREE-CLEAN", staged
        base.git(fixture.root, "reset")
        product.unlink()

        rogue = fixture.root / ".vibe-control" / "rogue.json"; rogue.write_text("{}\n", encoding="utf-8")
        _, unknown = fixture.command("automation", "--action", "commit")
        assert unknown["status"] == "BLOCKED" and unknown["error"]["id"] == "HC-AUTOMATION-CONTROL-SCOPE", unknown
    finally:
        fixture.close()


def test_hard_failure_and_completed_automation_stop() -> None:
    fixture = base.Fixture("AUTO_LOCAL_TO_REVIEW")
    try:
        state_path = fixture.root / ".vibe-control" / "stage-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")); state["health"] = "FAILED"
        base.write_json(state_path, state); base.git(fixture.root, "add", "-A"); base.git(fixture.root, "commit", "-m", "fixture failed state")
        _, failed = fixture.command("automation", "--action", "continue")
        assert failed["status"] == "BLOCKED" and failed["error"]["id"] == "HC-AUTOMATION-HARD-FAILURE", failed

        state["health"] = "CLEAR"; state["phase"] = "VERIFIED"
        base.write_json(state_path, state); base.git(fixture.root, "add", "-A"); base.git(fixture.root, "commit", "-m", "fixture review point")
        _, review = fixture.command("automation", "--action", "continue")
        assert review["status"] == "BLOCKED" and review["error"]["id"] == "HC-AUTOMATION-REVIEW-POINT", review
    finally:
        fixture.close()


def test_dashboard_without_task_is_external_and_self_bound() -> None:
    with tempfile.TemporaryDirectory(prefix="vc036-empty-", ignore_cleanup_errors=True) as name:
        root = Path(name) / "project"; root.mkdir()
        base.git(root, "init"); base.git(root, "config", "user.email", "fixture@example.invalid"); base.git(root, "config", "user.name", "Fixture")
        (root / "README.md").write_text("# Empty\n", encoding="utf-8"); base.git(root, "add", "-A"); base.git(root, "commit", "-m", "initial")
        result = base.run(sys.executable, str(base.CONTROL), "dashboard", "--project", str(root), expect=0)
        value = base.report(result); status_path = Path(value["data"]["files"]["status"])
        status = json.loads(status_path.read_text(encoding="utf-8")); recorded = status.pop("snapshotSha256")
        actual = hashlib.sha256(base.canonical(status).encode("utf-8")).hexdigest()
        assert recorded == actual == value["data"]["snapshotSha256"]
        assert status["phase"] == "DRAFT" and status["formal"]["eligible"] is False
        try:
            status_path.resolve().relative_to(root.resolve())
        except ValueError:
            pass
        else:
            raise AssertionError("default dashboard output entered the project worktree")


def test_current_controller_relocks_exact_supported_bound_runtime() -> None:
    fixture = base.Fixture("AUTO_LOCAL_TO_REVIEW")
    try:
        legacy_runtime = _materialize_034_runtime(fixture)
        contract = fixture.root / ".vibe-control/tasks/TASK-001.json"
        result = base.run(
            sys.executable, str(base.CONTROL), "lock-task",
            "--project", str(fixture.root), "--contract", str(contract),
        )
        value = base.report(result)
        assert result.returncode == 0 and value["status"] == "PASS", value
        state = json.loads((fixture.root / ".vibe-control/stage-state.json").read_text(encoding="utf-8"))
        assert state["phase"] == "CONTRACT_LOCKED" and state["taskId"] == "TASK-001"
        assert not (fixture.root / ".vibe-control/runtime/0.3.6").exists()
        assert legacy_runtime.is_dir(), "the compatibility controller must not migrate or replace the bound runtime"

        compiler = legacy_runtime / "vibe_runtime/project_rules.py"
        compiler.write_text(compiler.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8", newline="\n")
        base.git(fixture.root, "add", "-A")
        base.git(fixture.root, "commit", "-m", "tamper compiler")
        result = base.run(
            sys.executable, str(base.CONTROL), "validate", "--project", str(fixture.root),
        )
        rejected = base.report(result)
        failing = {item["id"] for item in rejected.get("integrity", {}).get("checks", []) if item.get("status") != "PASS"}
        assert result.returncode != 0 and "HC-RULESET-BINDING" in failing, rejected
    finally:
        fixture.close()


def main() -> int:
    tests = [
        test_commit_and_push_are_real_bounded_side_effects,
        test_clean_forbidden_commit_cannot_be_pushed,
        test_boundary_drift_staged_changes_and_unknown_control_paths_stop,
        test_hard_failure_and_completed_automation_stop,
        test_dashboard_without_task_is_external_and_self_bound,
        test_current_controller_relocks_exact_supported_bound_runtime,
    ]
    results = []
    for test in tests:
        started = time.monotonic()
        try:
            test(); results.append({"test": test.__name__, "status": "PASS", "durationSeconds": round(time.monotonic() - started, 3)})
        except Exception as exc:
            results.append({"test": test.__name__, "status": "FAIL", "durationSeconds": round(time.monotonic() - started, 3), "errorType": type(exc).__name__, "error": str(exc)})
    passed = sum(item["status"] == "PASS" for item in results)
    output = {"suite": "vibe-control-0.3.6-boundaries", "status": "PASS" if passed == len(results) else "FAIL", "counters": {"total": len(results), "passed": passed, "failed": len(results) - passed, "skipped": 0, "timedOut": 0}, "tests": results}
    print(json.dumps(output, ensure_ascii=False))
    return 0 if output["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
