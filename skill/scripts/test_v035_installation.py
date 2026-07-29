#!/usr/bin/env python3
"""0.3.5 portable-installation and bounded-suite acceptance cases."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]


def run(*args: str, cwd: Path | None = None, expect: int | None = 0, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(args), cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    if expect is not None and result.returncode != expect:
        raise AssertionError(f"exit={result.returncode}, expected={expect}: {' '.join(args)}\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def report(result: subprocess.CompletedProcess[str]) -> dict:
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"command did not emit JSON: {result.stdout!r}; stderr={result.stderr!r}") from exc
    if not isinstance(value, dict):
        raise AssertionError("command result must be an object")
    return value


def git(root: Path, *args: str) -> str:
    return run("git", "-C", str(root), *args).stdout.strip()


def portable_copy(parent: Path, name: str = "vibe-control") -> Path:
    target = parent / name
    shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns(".git", ".vibe-control", "__pycache__", "*.pyc"))
    return target


def installation(root: Path, *, expect: int | None = 0, isolated: bool = False) -> tuple[subprocess.CompletedProcess[str], dict]:
    command = [sys.executable]
    if isolated:
        command.append("-S")
    command.extend([str(root / "scripts" / "validate_installation.py"), "--skill-root", str(root)])
    result = run(*command, cwd=root, expect=expect, timeout=120)
    return result, report(result)


def assert_clean_install(value: dict, source_kind: str) -> None:
    assert value["status"] == "PASS", value
    assert value["packageMode"] == "DEVELOPMENT"
    assert value["formalClaimsAllowed"] is False
    assert value["maxClaimLevel"] == "DEVELOPMENT_CHECKED"
    assert value["sourceKind"] == source_kind
    binding = value["binding"]
    assert binding["sourceKind"] == source_kind
    if source_kind == "PORTABLE_COPY":
        assert "commit" not in binding and "tree" not in binding
    else:
        assert len(binding["commit"]) == 40 and len(binding["tree"]) == 40


def test_development_installation_source_kinds() -> None:
    with tempfile.TemporaryDirectory(prefix="vc035-install-kinds-", ignore_cleanup_errors=True) as temp_name:
        base = Path(temp_name)

        portable = portable_copy(base, "portable")
        _, portable_report = installation(portable)
        assert_clean_install(portable_report, "PORTABLE_COPY")

        git_root = portable_copy(base, "git-root")
        git(git_root, "init")
        git(git_root, "config", "user.email", "fixture@example.invalid")
        git(git_root, "config", "user.name", "Fixture")
        git(git_root, "add", "-A")
        git(git_root, "commit", "-m", "skill root")
        _, root_report = installation(git_root)
        assert_clean_install(root_report, "GIT_ROOT")

        parent = base / "parent-repo"
        parent.mkdir()
        git(parent, "init")
        git(parent, "config", "user.email", "fixture@example.invalid")
        git(parent, "config", "user.name", "Fixture")
        subdir = portable_copy(parent, "skill")
        (parent / "README.md").write_text("fixture\n", encoding="utf-8", newline="\n")
        git(parent, "add", "-A")
        git(parent, "commit", "-m", "nested skill")
        _, subdir_report = installation(subdir)
        assert_clean_install(subdir_report, "GIT_SUBDIRECTORY")
        expected_subtree = git(parent, "rev-parse", "HEAD:skill")
        assert subdir_report["binding"]["tree"] == expected_subtree
        (parent / "README.md").write_text("unrelated dirty root\n", encoding="utf-8", newline="\n")
        _, scoped_report = installation(subdir)
        assert_clean_install(scoped_report, "GIT_SUBDIRECTORY")

        tampered = portable_copy(base, "tampered")
        (tampered / "SKILL.md").write_text((tampered / "SKILL.md").read_text(encoding="utf-8") + "\nmutation\n", encoding="utf-8", newline="\n")
        _, tampered_report = installation(tampered, expect=3)
        assert "PKG-MANIFEST-VERIFY" in tampered_report["blockers"]

        extra = portable_copy(base, "extra")
        (extra / "unexpected.txt").write_text("unexpected\n", encoding="utf-8", newline="\n")
        _, extra_report = installation(extra, expect=3)
        assert "PKG-MANIFEST-VERIFY" in extra_report["blockers"]

        _, dependency_report = installation(portable, expect=2, isolated=True)
        assert dependency_report["readiness"] == "DEPENDENCY_BLOCKED"
        assert dependency_report["status"] == "BLOCKED"
        assert "Traceback" not in dependency_report.get("message", "")


def _bootstrap_from_portable(portable: Path) -> None:
    import test_v2_support as fixture

    with tempfile.TemporaryDirectory(prefix="vc035-portable-bootstrap-", ignore_cleanup_errors=True) as temp_name:
        base = Path(temp_name)
        project = base / "project"
        project.mkdir()
        git(project, "init")
        git(project, "config", "user.email", "fixture@example.invalid")
        git(project, "config", "user.name", "Fixture")
        (project / "PROJECT_BRIEF.md").write_text("# Fixture\n", encoding="utf-8", newline="\n")
        fixture.write_objective_files(project)
        (project / "POSITIONING_CONFIRMATION.json").write_text('{"actorId":"owner","decision":"CONFIRM"}\n', encoding="utf-8", newline="\n")
        (project / "AUTOMATION_CONFIRMATION.json").write_text('{"actorId":"owner","decision":"CONFIRM"}\n', encoding="utf-8", newline="\n")
        git(project, "add", "-A")
        git(project, "commit", "-m", "authority")
        spec = fixture.valid_bootstrap_spec(project_id="portable-install", release_intent="PRIVATE_OPERATION")
        spec_path = base / "bootstrap.json"
        fixture.write(spec_path, spec)
        result = run(
            sys.executable,
            str(portable / "scripts" / "vibe_control.py"),
            "bootstrap", "--project", str(project), "--spec", str(spec_path),
            expect=2,
            timeout=180,
        )
        value = report(result)
        assert value["status"] == "BLOCKED"
        assert value["data"]["packageMode"] == "DEVELOPMENT"
        assert value["data"]["maxClaimLevel"] == "DEVELOPMENT_CHECKED"
        assert any(item["id"] == "VC-BOOTSTRAP-COMMIT-REQUIRED" for item in value["integrity"]["checks"])
        lock = json.loads((project / ".vibe-control" / "project-governance-lock.json").read_text(encoding="utf-8"))
        assert lock["packageBinding"]["sourceKind"] == "PORTABLE_COPY"
        assert "commit" not in lock["packageBinding"] and "tree" not in lock["packageBinding"]


def test_portable_binding_schema_and_bootstrap() -> None:
    schema = json.loads((ROOT / "assets" / "project-control" / "schemas" / "project-governance-lock.schema.json").read_text(encoding="utf-8"))
    legacy = json.loads((ROOT / ".vibe-control" / "project-governance-lock.json").read_text(encoding="utf-8"))
    legacy["packageBinding"]["version"] = "0.3.4"
    assert not list(Draft202012Validator(schema).iter_errors(legacy)), "legacy 0.3.4 development lock must remain valid"
    missing_source = json.loads(json.dumps(legacy))
    missing_source["packageBinding"]["version"] = "0.3.5"
    assert list(Draft202012Validator(schema).iter_errors(missing_source)), "new development locks must declare sourceKind"
    portable_lock = json.loads(json.dumps(legacy))
    portable_lock["packageBinding"]["version"] = "0.3.5"
    portable_lock["packageBinding"]["sourceKind"] = "PORTABLE_COPY"
    portable_lock["packageBinding"].pop("commit", None)
    portable_lock["packageBinding"].pop("tree", None)
    assert not list(Draft202012Validator(schema).iter_errors(portable_lock))
    forged = json.loads(json.dumps(portable_lock))
    forged["packageBinding"]["commit"] = "0" * 40
    assert list(Draft202012Validator(schema).iter_errors(forged)), "portable lock must not forge Git identity"
    sealed = json.loads(json.dumps(portable_lock))
    sealed["packageMode"] = "SEALED"
    assert list(Draft202012Validator(schema).iter_errors(sealed)), "sealed lock must retain Git/audit closure"

    with tempfile.TemporaryDirectory(prefix="vc035-bootstrap-package-", ignore_cleanup_errors=True) as temp_name:
        portable = portable_copy(Path(temp_name), "skill")
        _bootstrap_from_portable(portable)


def test_installation_and_formal_validation_are_separate() -> None:
    with tempfile.TemporaryDirectory(prefix="vc035-install-vs-seal-", ignore_cleanup_errors=True) as temp_name:
        base = Path(temp_name)
        portable = portable_copy(base, "portable")
        _, portable_install = installation(portable)
        assert_clean_install(portable_install, "PORTABLE_COPY")
        portable_formal_result = run(sys.executable, str(portable / "scripts" / "validate_package_release.py"), "--skill-root", str(portable), cwd=portable, expect=2, timeout=180)
        portable_formal = report(portable_formal_result)
        assert portable_formal["status"] == "BLOCKED"
        assert portable_formal["readiness"] == "DEVELOPMENT_INSTALLABLE_NOT_SEAL_CANDIDATE"
        assert portable_formal["installationUsable"] is True
        assert "PKG-AUDIT-GIT" in portable_formal["blockers"]
        assert "PKG-DEVELOPMENT-NOT-SEAL-CANDIDATE" in portable_formal["blockers"]

        root = portable_copy(base, "skill")
        git(root, "init")
        git(root, "config", "user.email", "fixture@example.invalid")
        git(root, "config", "user.name", "Fixture")
        git(root, "add", "-A")
        git(root, "commit", "-m", "development package")
        _, install_report = installation(root)
        assert_clean_install(install_report, "GIT_ROOT")
        formal_result = run(sys.executable, str(root / "scripts" / "validate_package_release.py"), "--skill-root", str(root), cwd=root, expect=2, timeout=180)
        formal = report(formal_result)
        assert formal["status"] == "BLOCKED"
        assert formal["readiness"] == "DEVELOPMENT_INSTALLABLE_NOT_SEAL_CANDIDATE"
        assert formal["installationUsable"] is True
        assert formal["formalClaimsAllowed"] is False
        assert "PKG-DEVELOPMENT-NOT-SEAL-CANDIDATE" in formal["blockers"]
        assert not any(item["id"] == "PKG-AUDIT-CONTENT-CLOSURE" and item["status"] == "FAIL" for item in formal["checks"])


def _assert_deep_suite(script: str, minimum_cases: int) -> dict:
    listed = report(run(sys.executable, str(ROOT / "scripts" / script), "--list", cwd=ROOT, timeout=60))
    assert listed["status"] == "PASS" and len(listed["cases"]) >= minimum_cases
    started = time.monotonic()
    result = run(
        sys.executable, str(ROOT / "scripts" / script),
        "--jobs", "4", "--case-timeout", "180", "--suite-timeout", "240",
        cwd=ROOT, expect=None, timeout=260,
    )
    elapsed = time.monotonic() - started
    value = report(result)
    assert result.returncode == 0, value
    assert elapsed <= 240, {"elapsedSeconds": elapsed, "report": value}
    counts = value["counters"]
    assert counts["total"] == counts["passed"] + counts["failed"] + counts["timedOut"] + counts["skipped"]
    assert counts["total"] == counts["passed"] > 0
    assert counts["failed"] == counts["timedOut"] == counts["skipped"] == 0
    progress = [json.loads(line) for line in result.stderr.splitlines() if line.strip()]
    assert any(item.get("event") == "case-start" for item in progress)
    assert any(item.get("event") == "case-complete" for item in progress)
    value["wallSeconds"] = round(elapsed, 3)
    return value


def test_bounded_deep_suite_protocol() -> dict:
    return {
        "packageRelease": _assert_deep_suite("test_package_release_audit.py", 30),
        "assurance": _assert_deep_suite("test_assurance_regressions.py", 19),
    }


def main() -> int:
    cases = [
        ("development-installation-source-kinds", test_development_installation_source_kinds),
        ("portable-binding-schema-and-bootstrap", test_portable_binding_schema_and_bootstrap),
        ("installation-and-formal-validation-are-separate", test_installation_and_formal_validation_are_separate),
        ("bounded-deep-suite-protocol", test_bounded_deep_suite_protocol),
    ]
    results = []
    for case_id, test in cases:
        started = time.monotonic()
        try:
            details = test()
            results.append({"case": case_id, "status": "PASS", "durationSeconds": round(time.monotonic() - started, 3), **({"details": details} if details else {})})
        except Exception as exc:
            results.append({"case": case_id, "status": "FAIL", "durationSeconds": round(time.monotonic() - started, 3), "errorType": type(exc).__name__, "error": str(exc)})
    passed = sum(item["status"] == "PASS" for item in results)
    counters = {"total": len(results), "passed": passed, "failed": len(results) - passed, "timedOut": 0, "skipped": 0}
    value = {"test": "vibe-control-0.3.5-installation", "status": "PASS" if passed == len(results) else "FAIL", "counters": counters, "cases": results}
    print(json.dumps(value, ensure_ascii=False))
    return 0 if value["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
