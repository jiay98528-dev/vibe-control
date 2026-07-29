#!/usr/bin/env python3
"""Supplementary side-effect and stop-boundary regressions for vibe-control 0.3.6."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

import test_v036_automation as base


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


def main() -> int:
    tests = [
        test_commit_and_push_are_real_bounded_side_effects,
        test_clean_forbidden_commit_cannot_be_pushed,
        test_boundary_drift_staged_changes_and_unknown_control_paths_stop,
        test_hard_failure_and_completed_automation_stop,
        test_dashboard_without_task_is_external_and_self_bound,
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
