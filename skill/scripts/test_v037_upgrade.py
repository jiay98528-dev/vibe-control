#!/usr/bin/env python3
"""Focused same-Schema runtime-upgrade regressions for vibe-control 0.3.7."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from test_v036_automation import automation_policy, bootstrap_spec  # noqa: E402


def run(*args: str, cwd: Path | None = None, expect: int | None = None, timeout: int = 240) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(args), cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    if expect is not None and result.returncode != expect:
        raise AssertionError(
            f"exit={result.returncode}, expected={expect}: {' '.join(args)}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


def report(result: subprocess.CompletedProcess[str]) -> dict:
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"controller did not emit JSON: {result.stdout!r} / {result.stderr!r}") from exc
    assert isinstance(value, dict)
    return value


def git(root: Path, *args: str, expect: int = 0) -> str:
    return run("git", "-C", str(root), *args, expect=expect, timeout=60).stdout.strip()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    assert isinstance(value, dict)
    return value


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def content_ref(root: Path, path: Path) -> dict:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha(path),
        "tracked": True,
    }


def previous_patch(version: str) -> str:
    major, minor, patch = (int(part) for part in version.split("."))
    assert patch > 0
    return f"{major}.{minor}.{patch - 1}"


def materialize_package(base: Path) -> Path:
    package = base / "package"
    shutil.copytree(
        ROOT,
        package,
        ignore=shutil.ignore_patterns(".git", ".vibe-control", "__pycache__", "*.pyc"),
    )
    run(sys.executable, str(package / "scripts" / "build_manifest.py"), "--root", str(package), expect=0, timeout=240)
    checked = report(run(
        sys.executable,
        str(package / "scripts" / "validate_installation.py"),
        "--skill-root",
        str(package),
        expect=0,
        timeout=240,
    ))
    assert checked["status"] == "PASS", checked
    return package


def make_project(base: Path, package: Path, name: str) -> tuple[Path, str, str]:
    project = base / name
    project.mkdir()
    git(project, "init")
    git(project, "config", "user.email", "fixture@example.invalid")
    git(project, "config", "user.name", "Fixture")
    for filename, text in {
        ".gitattributes": "* text=auto eol=lf\n",
        "PROJECT_BRIEF.md": "# Upgrade fixture\n",
        "KEY_OBJECTIVES.md": "# Objectives\n\n- `KO-001`: outcome\n- `KF-001`: false proof\n- `NG-001`: deployment\n",
        "OBJECTIVES_CONFIRMATION.json": "{}\n",
        "POSITIONING_CONFIRMATION.json": "{}\n",
        "AUTOMATION_CONFIRMATION.json": "{}\n",
        "CHECKPOINT_CONFIRMATION.json": "{}\n",
    }.items():
        (project / filename).write_text(text, encoding="utf-8", newline="\n")
    git(project, "add", "-A")
    git(project, "commit", "-m", "authority")

    target = (package / "VERSION").read_text(encoding="utf-8-sig").strip()
    source = previous_patch(target)
    spec_path = base / f"{name}-bootstrap.json"
    policy = automation_policy(name, "MANUAL_STAGE_CONFIRMATION")
    spec = bootstrap_spec(name, policy)
    spec["projectId"] = name
    spec["automationPolicy"] = automation_policy(name, "MANUAL_STAGE_CONFIRMATION")
    write_json(spec_path, spec)
    bootstrap = report(run(
        sys.executable,
        str(package / "assets/project-control/runtime/control.py"),
        "bootstrap",
        "--project",
        str(project),
        "--spec",
        str(spec_path),
        expect=2,
        timeout=240,
    ))
    assert bootstrap["status"] == "BLOCKED", bootstrap
    git(project, "add", "-A")
    git(project, "commit", "-m", "bootstrap target runtime")

    control = project / ".vibe-control"
    for relative in (
        "tasks/OLD.json",
        "task-locks/OLD.json",
        "candidates/OLD.json",
        "evidence/OLD.txt",
        "reviews/OLD.json",
        "decisions/OLD.json",
        "external-audits/OLD.json",
        "handoffs/OLD.json",
    ):
        path = control / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("historical\n", encoding="utf-8", newline="\n")
    mixed = control / "MiXeD" / "Blob.TXT"
    mixed.parent.mkdir(parents=True, exist_ok=True)
    mixed.write_bytes(b"mixed-case\x00\r\n")
    crlf = control / "evidence" / "CRLF.json"
    crlf.write_bytes(b'{"status":"historical"}\r\n')
    target_runtime = control / "runtime" / target
    source_runtime = control / "runtime" / source
    target_runtime.rename(source_runtime)
    bytecode = source_runtime / "vibe_runtime" / "__pycache__" / "fixture.cpython-312.pyc"
    bytecode.parent.mkdir(parents=True, exist_ok=True)
    bytecode.write_bytes(b"regenerable-python-bytecode\x00\r\n")
    info_exclude = project / ".git" / "info" / "exclude"
    with info_exclude.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("__pycache__/\n*.pyc\n")
    lock_path = control / "project-governance-lock.json"
    lock = load_json(lock_path)
    lock["lockId"] = f"lock-{name}-runtime-{source}"
    lock["packageBinding"]["version"] = source
    for key in ("runtime", "ruleCompiler", "profileDirectory", "adapterDirectory"):
        old_path = lock[key]["path"].replace(f"runtime/{target}/", f"runtime/{source}/")
        lock[key] = content_ref(project, project / old_path)
    lock["packageBinding"]["runtimeManifest"] = dict(lock["runtime"])
    if "evidenceBytePolicy" in lock:
        lock["evidenceBytePolicy"] = content_ref(project, control / ".gitattributes")
    write_json(lock_path, lock)
    git(project, "add", "-A")
    git(project, "commit", "-m", "pin previous development runtime")
    return project, source, target


def add_upgrade_spec(project: Path, source: str, target: str, *, replacement: bool = True) -> Path:
    replacement_path = project / "replacement-case-catalog.json"
    if replacement:
        shutil.copy2(project / ".vibe-control/case-catalog.json", replacement_path)
    summary = f"owner confirms diagnostic runtime upgrade from {source} to {target}"
    spec = {
        "schemaVersion": "3.2",
        "projectId": project.name,
        "sourceRuntimeVersion": source,
        "targetRuntimeVersion": target,
        "replacementCaseCatalog": replacement_path.name if replacement else None,
        "confirmation": {
            "actorId": "owner",
            "summary": summary,
            "summarySha256": hashlib.sha256(summary.encode("utf-8")).hexdigest(),
            "confirmedAt": "2026-07-29T20:00:00+08:00",
        },
    }
    spec_path = project / "runtime-upgrade-spec.json"
    write_json(spec_path, spec)
    git(project, "add", "-A")
    git(project, "commit", "-m", "confirm runtime upgrade")
    return spec_path


def control_snapshot(project: Path) -> dict[str, str]:
    control = project / ".vibe-control"
    return {
        path.relative_to(control).as_posix(): sha(path)
        for path in sorted(value for value in control.rglob("*") if value.is_file())
    }


def command(package: Path, project: Path, *args: str, expect: int | None = None) -> tuple[subprocess.CompletedProcess[str], dict]:
    result = run(
        sys.executable,
        str(package / "assets/project-control/runtime/control.py"),
        "upgrade",
        "--project",
        str(project),
        *args,
        expect=expect,
        timeout=240,
    )
    return result, report(result)


def test_plan_is_read_only_and_apply_invalidates(base: Path, package: Path) -> None:
    project, source, target = make_project(base, package, "positive")
    before = control_snapshot(project)
    _, unsigned = command(package, project, "--plan", expect=2)
    assert "HC-UPGRADE-SPEC-REQUIRED" in unsigned["formal"]["blockers"], unsigned
    assert control_snapshot(project) == before

    spec = add_upgrade_spec(project, source, target)
    before = control_snapshot(project)
    _, planned = command(package, project, "--plan", "--spec", str(spec), expect=2)
    assert planned["data"]["sourceRuntimeVersion"] == source
    assert planned["data"]["targetRuntimeVersion"] == target
    assert planned["data"]["gitHead"] == git(project, "rev-parse", "HEAD")
    assert planned["data"]["spec"]["sha256"] == sha(spec)
    assert planned["data"]["replacementCaseCatalog"]["sha256"] == sha(project / "replacement-case-catalog.json")
    assert control_snapshot(project) == before

    plan_hash = planned["data"]["planHash"]
    _, applied = command(package, project, "--apply", plan_hash, "--spec", str(spec), expect=2)
    assert applied["formal"] == {
        "eligible": False,
        "maxClaimLevel": "DIAGNOSTIC",
        "blockers": ["HC-UPGRADE-INVALIDATION"],
    }, applied
    control = project / ".vibe-control"
    state = load_json(control / "stage-state.json")
    assert (state["phase"], state["health"], state["claimLevel"]) == ("DRAFT", "BLOCKED", "DIAGNOSTIC")
    assert state["taskId"] is None and state["candidateId"] is None
    assert [path.name for path in (control / "runtime").iterdir()] == [target]
    assert (control / ".gitattributes").read_bytes() == b"evidence/** -text -filter -working-tree-encoding\n"
    lock = load_json(control / "project-governance-lock.json")
    assert lock["packageBinding"]["version"] == target
    if tuple(int(part) for part in target.split(".")) >= (0, 3, 7):
        assert lock["evidenceBytePolicy"]["sha256"] == sha(control / ".gitattributes")
    assert load_json(control / "case-catalog.json") == load_json(project / "replacement-case-catalog.json")
    assert not any((control / name).exists() for name in ("tasks", "task-locks", "candidates", "evidence", "reviews", "decisions", "external-audits", "handoffs"))
    archive = control / "legacy" / f"runtime-upgrade-{plan_hash}"
    assert (archive / "control-plane/evidence/OLD.txt").is_file()
    assert (archive / "control-plane/MiXeD/Blob.TXT").read_bytes() == b"mixed-case\x00\r\n"
    assert (archive / "control-plane/evidence/CRLF.json").read_bytes() == b'{"status":"historical"}\r\n'
    assert not list((archive / "control-plane").rglob("*.pyc"))
    assert (archive / ".gitattributes").read_bytes() == (
        b".gitattributes -text -filter -working-tree-encoding\n"
        b"manifest.json -text -filter -working-tree-encoding\n"
        b"control-plane/** -text -filter -working-tree-encoding\n"
    )
    manifest = load_json(archive / "manifest.json")
    actual = [
        {
            "path": path.relative_to(archive / "control-plane").as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha(path),
        }
        for path in sorted(
            (value for value in (archive / "control-plane").rglob("*") if value.is_file()),
            key=lambda value: value.relative_to(archive / "control-plane").as_posix(),
        )
    ]
    assert manifest["files"] == actual, {
        "missing": [item for item in manifest["files"] if item not in actual],
        "extra": [item for item in actual if item not in manifest["files"]],
    }
    assert manifest["snapshotSha256"] == hashlib.sha256(json.dumps(actual, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    expected_archived = {
        path: digest for path, digest in before.items()
        if "__pycache__" not in Path(path).parts and not path.lower().endswith(".pyc")
    }
    assert {item["path"]: item["sha256"] for item in actual} == expected_archived
    dispositions = {item["path"]: item["disposition"] for item in manifest["sourceDispositions"]}
    assert dispositions["MiXeD/Blob.TXT"] == "TRACKED"
    assert sum(manifest["sourceDispositionCounts"].values()) == len(actual)
    excluded = manifest["excludedEphemeral"]
    assert len(excluded) == 1 and excluded[0]["path"].endswith("/__pycache__/fixture.cpython-312.pyc"), excluded
    ephemeral_check = next(
        item for item in planned["integrity"]["checks"]
        if item["id"] == "HC-UPGRADE-EPHEMERAL-EXCLUSION"
    )
    assert len(ephemeral_check["details"]["excludedEphemeral"]) == 1

    git(project, "add", "-A")
    git(project, "commit", "-m", "apply runtime upgrade")
    fresh = base / "positive-fresh"
    run(
        "git", "-c", "core.autocrlf=true", "clone", "--no-local", str(project), str(fresh),
        expect=0, timeout=120,
    )
    fresh_archive = fresh / ".vibe-control" / "legacy" / f"runtime-upgrade-{plan_hash}"
    fresh_manifest = load_json(fresh_archive / "manifest.json")
    fresh_actual = [
        {
            "path": path.relative_to(fresh_archive / "control-plane").as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha(path),
        }
        for path in sorted(
            (value for value in (fresh_archive / "control-plane").rglob("*") if value.is_file()),
            key=lambda value: value.relative_to(fresh_archive / "control-plane").as_posix(),
        )
    ]
    assert fresh_actual == fresh_manifest["files"]
    evidence_attrs = git(fresh, "check-attr", "text", "filter", "working-tree-encoding", "--", ".vibe-control/evidence/probe")
    legacy_attrs = git(fresh, "check-attr", "text", "filter", "working-tree-encoding", "--", f".vibe-control/legacy/runtime-upgrade-{plan_hash}/control-plane/evidence/CRLF.json")
    assert all(line.endswith(": unset") for line in evidence_attrs.splitlines()), evidence_attrs
    assert all(line.endswith(": unset") for line in legacy_attrs.splitlines()), legacy_attrs
    no_op = report(run(
        sys.executable,
        str(package / "assets/project-control/runtime/control.py"),
        "upgrade",
        "--project",
        str(project),
        "--plan",
        expect=2,
    ))
    assert no_op["error"]["id"] == "HC-UPGRADE-NOOP", no_op


def test_dirty_wrong_hash_untracked_and_plan_drift(base: Path, package: Path) -> None:
    untracked_project, source, target = make_project(base, package, "untracked")
    summary = f"upgrade {source} to {target}"
    untracked_spec = untracked_project / "untracked-upgrade.json"
    write_json(untracked_spec, {
        "schemaVersion": "3.2", "projectId": untracked_project.name,
        "sourceRuntimeVersion": source, "targetRuntimeVersion": target,
        "replacementCaseCatalog": None,
        "confirmation": {"actorId": "owner", "summary": summary, "summarySha256": hashlib.sha256(summary.encode()).hexdigest(), "confirmedAt": "2026-07-29T20:00:00+08:00"},
    })
    result, value = command(package, untracked_project, "--plan", "--spec", str(untracked_spec))
    assert result.returncode == 3 and value["error"]["id"] == "HC-FILE-TRACKED", value
    untracked_spec.unlink()
    committed_spec = add_upgrade_spec(untracked_project, source, target)
    ignored_control = untracked_project / ".vibe-control" / "local-cache" / "ignored.bin"
    ignored_control.parent.mkdir(parents=True, exist_ok=True)
    ignored_control.write_bytes(b"non-ephemeral ignored control data")
    with (untracked_project / ".git" / "info" / "exclude").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(".vibe-control/local-cache/ignored.bin\n")
    _, ignored_plan = command(package, untracked_project, "--plan", "--spec", str(committed_spec), expect=2)
    ignored_before = control_snapshot(untracked_project)
    result, value = command(package, untracked_project, "--apply", ignored_plan["data"]["planHash"], "--spec", str(committed_spec))
    assert result.returncode == 2 and value["error"]["id"] == "HC-UPGRADE-ARCHIVE-COMMITTABILITY", value
    assert value["error"]["details"]["ignored"] == ["local-cache/ignored.bin"], value
    assert control_snapshot(untracked_project) == ignored_before
    ignored_control.unlink()
    ignored_control.parent.rmdir()

    project = untracked_project
    spec = committed_spec
    _, planned = command(package, project, "--plan", "--spec", str(spec), expect=2)
    plan_hash = planned["data"]["planHash"]
    dirty = project / "dirty.txt"
    dirty.write_text("dirty\n", encoding="utf-8")
    result, value = command(package, project, "--apply", plan_hash, "--spec", str(spec))
    assert result.returncode == 2 and value["error"]["id"] == "HC-WORKTREE-CLEAN", value
    dirty.unlink()
    result, value = command(package, project, "--apply", "0" * 64, "--spec", str(spec))
    assert result.returncode == 4 and value["error"]["id"] == "HC-UPGRADE-PLAN-HASH", value

    drift = project / ".vibe-control/drift.txt"
    drift.write_text("drift\n", encoding="utf-8", newline="\n")
    git(project, "add", "-A")
    git(project, "commit", "-m", "change upgrade source snapshot")
    result, value = command(package, project, "--apply", plan_hash, "--spec", str(spec))
    assert result.returncode == 4 and value["error"]["id"] == "HC-UPGRADE-PLAN-HASH", value

    brief = project / "PROJECT_BRIEF.md"
    brief.write_text("# drifted authority\n", encoding="utf-8", newline="\n")
    git(project, "add", "-A")
    git(project, "commit", "-m", "drift bound authority")
    result, value = command(package, project, "--plan")
    assert result.returncode == 4 and value["error"]["id"] == "HC-UPGRADE-SOURCE-REF-CLOSURE", value


def test_staging_failure_preserves_live_control(base: Path, package: Path) -> None:
    source_project, source, target = make_project(base, package, "transaction-source")
    add_upgrade_spec(source_project, source, target)

    runtime = package / "assets/project-control/runtime"
    sys.path.insert(0, str(runtime))
    try:
        from vibe_runtime import upgrade_control  # type: ignore

        drift_project = base / "transaction-drift"
        shutil.copytree(source_project, drift_project)
        drift_spec = drift_project / "runtime-upgrade-spec.json"
        drift_plan = upgrade_control.upgrade_plan(drift_project, drift_spec)["data"]
        drift_before = control_snapshot(drift_project)
        original_bindings = upgrade_control._write_skill_bindings

        def mutate_spec(staging: Path, compiled: dict) -> list[dict]:
            refs = original_bindings(staging, compiled)
            value = load_json(drift_spec)
            value["confirmation"]["summary"] += " drift"
            value["confirmation"]["summarySha256"] = hashlib.sha256(
                value["confirmation"]["summary"].encode("utf-8")
            ).hexdigest()
            write_json(drift_spec, value)
            return refs

        with mock.patch.object(upgrade_control, "_write_skill_bindings", side_effect=mutate_spec):
            try:
                upgrade_control.upgrade_apply(drift_project, drift_plan["planHash"], drift_spec)
            except upgrade_control.ControlError as exc:
                assert exc.check_id == "HC-UPGRADE-TRANSACTION-DRIFT", exc.check_id
                assert exc.status == "INVALIDATED"
            else:
                raise AssertionError("expected transaction drift invalidation")
        assert control_snapshot(drift_project) == drift_before
        assert not (drift_project / f".vibe-control.upgrade-{drift_plan['planHash'][:12]}.tmp").exists()

        for label, injected in (("oserror", OSError("injected swap failure")), ("interrupt", KeyboardInterrupt())):
            project = base / f"transaction-{label}"
            shutil.copytree(source_project, project)
            spec = project / "runtime-upgrade-spec.json"
            planned = upgrade_control.upgrade_plan(project, spec)["data"]
            before = control_snapshot(project)
            staging = project / f".vibe-control.upgrade-{planned['planHash'][:12]}.tmp"
            backup = project / f".vibe-control.upgrade-{planned['planHash'][:12]}.backup"
            live = project / ".vibe-control"
            real_replace = upgrade_control._replace_path

            def fail_install(source_path: Path, target_path: Path) -> None:
                if source_path == staging and target_path == live:
                    raise injected
                real_replace(source_path, target_path)

            with mock.patch.object(upgrade_control, "_replace_path", side_effect=fail_install):
                try:
                    upgrade_control.upgrade_apply(project, planned["planHash"], spec)
                except upgrade_control.ControlError as exc:
                    assert exc.check_id == "HC-UPGRADE-SWAP-ROLLED-BACK", exc.check_id
                else:
                    raise AssertionError("expected directory-exchange rollback")
            assert control_snapshot(project) == before
            assert not staging.exists() and not backup.exists()
            journal_dir = Path(git(project, "rev-parse", "--git-path", "vibe-control-upgrades"))
            if not journal_dir.is_absolute():
                journal_dir = project / journal_dir
            assert not list(journal_dir.glob("*.journal.json"))

        recovery = base / "transaction-recovery"
        shutil.copytree(source_project, recovery)
        recovery_spec = recovery / "runtime-upgrade-spec.json"
        recovery_plan = upgrade_control.upgrade_plan(recovery, recovery_spec)["data"]
        recovery_before = control_snapshot(recovery)
        recovery_live = recovery / ".vibe-control"
        recovery_staging = recovery / f".vibe-control.upgrade-{recovery_plan['planHash'][:12]}.tmp"
        recovery_backup = recovery / f".vibe-control.upgrade-{recovery_plan['planHash'][:12]}.backup"
        real_replace = upgrade_control._replace_path

        def fail_install_and_restore(source_path: Path, target_path: Path) -> None:
            if source_path in (recovery_staging, recovery_backup) and target_path == recovery_live:
                raise OSError(f"injected failure for {source_path.name}")
            real_replace(source_path, target_path)

        with mock.patch.object(upgrade_control, "_replace_path", side_effect=fail_install_and_restore):
            try:
                upgrade_control.upgrade_apply(recovery, recovery_plan["planHash"], recovery_spec)
            except upgrade_control.ControlError as exc:
                assert exc.check_id == "HC-UPGRADE-RECOVERY-REQUIRED", exc.check_id
                assert exc.details and Path(exc.details["backup"]) == recovery_backup
                assert Path(exc.details["journal"]).is_file()
            else:
                raise AssertionError("expected explicit recovery-required failure")
        assert not recovery_live.exists()
        assert recovery_backup.is_dir()
        assert {
            path.relative_to(recovery_backup).as_posix(): sha(path)
            for path in sorted(value for value in recovery_backup.rglob("*") if value.is_file())
        } == recovery_before
        assert recovery_staging.is_dir()
    finally:
        sys.path.remove(str(runtime))


def main() -> int:
    tests = [
        test_plan_is_read_only_and_apply_invalidates,
        test_dirty_wrong_hash_untracked_and_plan_drift,
        test_staging_failure_preserves_live_control,
    ]
    results: list[dict[str, str]] = []
    short_root = Path("C:/vc37") if sys.platform == "win32" and Path("C:/vc37").is_dir() else None
    with tempfile.TemporaryDirectory(prefix="u-", dir=short_root, ignore_cleanup_errors=True) as name:
        base = Path(name)
        package = materialize_package(base)
        for test in tests:
            try:
                test(base, package)
            except Exception as exc:
                results.append({"id": test.__name__, "status": "FAIL", "error": f"{type(exc).__name__}: {exc}"})
            else:
                results.append({"id": test.__name__, "status": "PASS"})
    value = {
        "status": "PASS" if all(item["status"] == "PASS" for item in results) else "FAIL",
        "total": len(results),
        "passed": sum(item["status"] == "PASS" for item in results),
        "failed": sum(item["status"] == "FAIL" for item in results),
        "skipped": 0,
        "results": results,
    }
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if value["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
