#!/usr/bin/env python3
"""Focused regressions for vibe-control 0.3.7 milestone commits."""

from __future__ import annotations

import json
import time
from pathlib import Path

import test_v036_automation as base


def _dirty_product(fixture: base.Fixture, text: str = "print('OK')\n") -> Path:
    path = fixture.root / "fixture.py"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def test_default_subject_is_conventional() -> None:
    fixture = base.Fixture("AUTO_LOCAL_TO_REVIEW")
    try:
        _dirty_product(fixture)
        _, result = fixture.command("automation", "--action", "commit")
        expected = "chore(governance): record TASK-001 milestone"
        assert result["status"] == "PASS", result
        assert result["data"]["commitSubject"] == expected
        assert base.git(fixture.root, "log", "-1", "--pretty=%s") == expected
        assert not base.git(fixture.root, "status", "--porcelain=v1")
    finally:
        fixture.close()


def test_explicit_subject_and_cli_scope() -> None:
    fixture = base.Fixture("AUTO_LOCAL_TO_REVIEW")
    try:
        _dirty_product(fixture)
        expected = "fix(governance): record compatible milestone"
        _, result = fixture.command("automation", "--action", "commit", "--message", expected)
        assert result["status"] == "PASS", result
        assert result["data"]["commitSubject"] == expected
        assert base.git(fixture.root, "log", "-1", "--pretty=%s") == expected

        _, rejected = fixture.command("automation", "--action", "push", "--message", expected)
        assert rejected["status"] == "FAIL" and rejected["error"]["id"] == "CLI-INVALID-ARGUMENTS", rejected
    finally:
        fixture.close()


def test_invalid_subjects_fail_before_staging() -> None:
    fixture = base.Fixture("AUTO_LOCAL_TO_REVIEW")
    try:
        path = _dirty_product(fixture, "print('kept')\n")
        before = base.git(fixture.root, "rev-parse", "HEAD")
        for subject in ("", "two\nlines", "bad\tcontrol"):
            _, result = fixture.command("automation", "--action", "commit", "--message", subject)
            assert result["status"] == "BLOCKED" and result["error"]["id"] == "HC-AUTOMATION-MILESTONE-MESSAGE", result
            assert base.git(fixture.root, "rev-parse", "HEAD") == before
            assert not base.git(fixture.root, "diff", "--cached", "--name-only")
            assert path.read_text(encoding="utf-8") == "print('kept')\n"
    finally:
        fixture.close()


def test_hook_failure_keeps_worktree_and_clears_automation_staging() -> None:
    fixture = base.Fixture("AUTO_LOCAL_TO_REVIEW")
    try:
        path = _dirty_product(fixture, "print('hook-kept')\n")
        before = base.git(fixture.root, "rev-parse", "HEAD")
        hook = fixture.root / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nprintf '%s\\n' 'VC037_HOOK_REJECTED' >&2\nexit 1\n", encoding="utf-8", newline="\n")
        _, result = fixture.command("automation", "--action", "commit")
        assert result["status"] == "BLOCKED" and result["error"]["id"] == "HC-AUTOMATION-MILESTONE-COMMIT", result
        details = result["error"]["details"]
        assert "VC037_HOOK_REJECTED" in details["stderr"] and details["stagingCleared"] is True, result
        assert base.git(fixture.root, "rev-parse", "HEAD") == before
        assert not base.git(fixture.root, "diff", "--cached", "--name-only")
        assert path.read_text(encoding="utf-8") == "print('hook-kept')\n"
        assert "fixture.py" in base.git(fixture.root, "status", "--porcelain=v1")
    finally:
        fixture.close()


def test_first_porcelain_line_keeps_dot_vibe_control_prefix() -> None:
    fixture = base.Fixture("AUTO_LOCAL_TO_REVIEW")
    try:
        evidence = fixture.root / ".vibe-control" / "evidence" / "first-line.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text('{"value": 1}\n', encoding="utf-8", newline="\n")
        base.git(fixture.root, "add", "-A")
        base.git(fixture.root, "commit", "-m", "test: seed managed evidence")
        evidence.write_text('{"value": 2}\n', encoding="utf-8", newline="\n")
        status = base.run("git", "-C", str(fixture.root), "status", "--porcelain=v1", expect=0).stdout
        assert status.splitlines()[0] == " M .vibe-control/evidence/first-line.json", status
        _, result = fixture.command("automation", "--action", "commit")
        assert result["status"] == "PASS", result
        changed = base.git(fixture.root, "show", "--name-only", "--format=", "HEAD").splitlines()
        assert ".vibe-control/evidence/first-line.json" in changed
    finally:
        fixture.close()


def main() -> int:
    tests = [
        test_default_subject_is_conventional,
        test_explicit_subject_and_cli_scope,
        test_invalid_subjects_fail_before_staging,
        test_hook_failure_keeps_worktree_and_clears_automation_staging,
        test_first_porcelain_line_keeps_dot_vibe_control_prefix,
    ]
    results = []
    for test in tests:
        started = time.monotonic()
        try:
            test()
            results.append({"test": test.__name__, "status": "PASS", "durationSeconds": round(time.monotonic() - started, 3)})
        except Exception as exc:
            results.append({"test": test.__name__, "status": "FAIL", "durationSeconds": round(time.monotonic() - started, 3), "errorType": type(exc).__name__, "error": str(exc)})
    passed = sum(item["status"] == "PASS" for item in results)
    output = {
        "suite": "vibe-control-0.3.7-automation-commit",
        "status": "PASS" if passed == len(results) else "FAIL",
        "counters": {"total": len(results), "passed": passed, "failed": len(results) - passed, "skipped": 0, "timedOut": 0},
        "tests": results,
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0 if output["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
