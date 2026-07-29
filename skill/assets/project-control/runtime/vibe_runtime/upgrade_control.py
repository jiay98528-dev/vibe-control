from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import VERSION
from .common import (
    ControlError,
    canonical_bytes,
    check,
    clean_status,
    envelope,
    file_ref,
    git,
    git_root,
    load_json,
    now_iso,
    safe_relative,
    sha256_bytes,
    sha256_file,
    verify_ref,
    write_json_atomic,
)
from .controller import (
    SCHEMA_VERSION,
    _adapter_binding,
    _guard_v3_control_plane,
    assert_dependencies,
    copy_runtime_bundle,
    initial_state,
    paths,
    require_identifier,
    runtime_root,
    validate_adapter_case_contract,
)
from .package_release import validate_development_package
from .positioning_control import (
    compile_for_project,
    compiler_checks,
    coverage_check,
    fail_on_compile_issues,
)
from .schema import validate_object


INVALIDATES = [
    "task",
    "task-lock",
    "candidate",
    "evidence",
    "review",
    "decision",
    "external-audit",
    "receipt",
    "handoff",
]
DOWNSTREAM_DIRECTORIES = [
    "tasks",
    "task-locks",
    "candidates",
    "evidence",
    "reviews",
    "decisions",
    "external-audits",
    "handoffs",
]
CONTROL_ATTRIBUTES = "evidence/** -text -filter -working-tree-encoding\n"
ARCHIVE_ATTRIBUTES = (
    ".gitattributes -text -filter -working-tree-encoding\n"
    "manifest.json -text -filter -working-tree-encoding\n"
    "control-plane/** -text -filter -working-tree-encoding\n"
)
LOCK_SINGLE_REFS = (
    "skill",
    "runtime",
    "packageAuditReceipt",
    "keyObjectives",
    "automationPolicy",
    "caseCatalog",
    "evidenceBytePolicy",
    "ruleInputs",
    "positioning",
    "resolvedRuleSet",
    "ruleCompiler",
    "profileDirectory",
    "adapterDirectory",
)
LOCK_ARRAY_REFS = ("authorityFiles", "skillBindings")
PACKAGE_REFS = ("packageManifest", "runtimeManifest", "assuranceMatrix")


def _version_tuple(value: Any) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value if isinstance(value, str) else "")
    if match is None:
        raise ControlError(
            "HC-UPGRADE-VERSION",
            "runtime upgrade requires semantic patch versions such as 0.3.6",
            status="BLOCKED",
            details={"version": value},
        )
    return tuple(int(part) for part in match.groups())


def _is_ephemeral_path(relative: Path) -> bool:
    return any(part.casefold() == "__pycache__" for part in relative.parts) or relative.suffix.casefold() == ".pyc"


def _top_level_excluded(relative: Path, excluded: set[str] | frozenset[str]) -> bool:
    return bool(relative.parts) and relative.parts[0].casefold() in {item.casefold() for item in excluded}


def _ephemeral_files(root: Path, *, exclude_top_level: set[str] | frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if _top_level_excluded(relative, exclude_top_level):
            continue
        if path.is_file() and _is_ephemeral_path(relative):
            files.append({
                "path": relative.as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "reason": "PYTHON_BYTECODE_CACHE",
            })
    return files


def _ignore_ephemeral(_: str, names: list[str]) -> set[str]:
    return {
        name for name in names
        if name.casefold() == "__pycache__" or Path(name).suffix.casefold() == ".pyc"
    }


def _snapshot_files(root: Path, *, exclude_top_level: set[str] | frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if _top_level_excluded(relative, exclude_top_level):
            continue
        if _is_ephemeral_path(relative):
            continue
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ControlError(
                "HC-UPGRADE-SOURCE-SYMLINK",
                "runtime upgrades do not archive symbolic links from the control plane",
                details={"path": relative.as_posix()},
            )
        if path.is_file():
            files.append({
                "path": relative.as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })
    return files


def _lock_reference_items(lock: dict[str, Any]) -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    package_binding = lock.get("packageBinding")
    if isinstance(package_binding, dict):
        for name in PACKAGE_REFS:
            items.append((f"packageBinding.{name}", package_binding.get(name)))
    for name in LOCK_SINGLE_REFS:
        if name in lock:
            items.append((name, lock.get(name)))
    for name in LOCK_ARRAY_REFS:
        values = lock.get(name)
        if isinstance(values, list):
            items.extend((f"{name}[{index}]", value) for index, value in enumerate(values))
    return items


def _validate_lock_reference_closure(root: Path, lock: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate every file reference carried by the old governance lock."""
    results: list[dict[str, Any]] = []
    for label, ref in _lock_reference_items(lock):
        result = verify_ref(root, ref if isinstance(ref, dict) else {}, "HC-UPGRADE-SOURCE-REF")
        results.append({"binding": label, **result})
    failures = [item for item in results if item.get("status") != "PASS"]
    if failures:
        raise ControlError(
            "HC-UPGRADE-SOURCE-REF-CLOSURE",
            "one or more references in the source governance lock drifted",
            status="INVALIDATED",
            details={"failures": failures},
        )
    return results


def _source_dispositions(root: Path, control: Path, files: list[dict[str, Any]]) -> list[dict[str, str]]:
    dispositions: list[dict[str, str]] = []
    for item in files:
        relative = (control / item["path"]).resolve().relative_to(root.resolve()).as_posix()
        tracked = bool(git(root, "ls-files", "--error-unmatch", "--", relative, required=False))
        ignored = bool(git(root, "check-ignore", "--no-index", "--", relative, required=False))
        disposition = "TRACKED" if tracked else ("IGNORED" if ignored else "UNTRACKED")
        dispositions.append({"path": item["path"], "disposition": disposition})
    return dispositions


def _archive_manifest(
    snapshot: Path,
    *,
    source_dispositions: list[dict[str, str]] | None = None,
    byte_policy: Path | None = None,
    excluded_ephemeral: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    files = _snapshot_files(snapshot)
    manifest: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "hashAlgorithm": "sha256",
        "files": files,
        "snapshotSha256": sha256_bytes(canonical_bytes(files)),
    }
    if source_dispositions is not None:
        manifest["sourceDispositions"] = source_dispositions
        manifest["sourceDispositionCounts"] = {
            name: sum(1 for item in source_dispositions if item["disposition"] == name)
            for name in ("TRACKED", "UNTRACKED", "IGNORED")
        }
    if byte_policy is not None:
        manifest["bytePolicy"] = {
            "path": byte_policy.name,
            "bytes": byte_policy.stat().st_size,
            "sha256": sha256_file(byte_policy),
        }
    if excluded_ephemeral is not None:
        manifest["excludedEphemeral"] = excluded_ephemeral
    return manifest


def _staged_ref(staging: Path, relative: str) -> dict[str, Any]:
    path = staging / relative
    if not path.is_file():
        raise ControlError("HC-UPGRADE-STAGING", f"staged file is missing: {relative}", status="BLOCKED")
    return {
        "path": f".vibe-control/{relative}",
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "tracked": True,
    }


def _target_package() -> tuple[Path, dict[str, Any]]:
    skill_root = runtime_root().parents[2]
    report = validate_development_package(skill_root)
    binding = report.get("binding")
    if report.get("status") != "PASS" or not isinstance(binding, dict):
        raise ControlError(
            "HC-DEVELOPMENT-PACKAGE-INTEGRITY",
            f"runtime upgrade requires an integrity-checked {VERSION} development package",
            status="BLOCKED" if report.get("status") == "BLOCKED" else "FAIL",
            details={"blockers": report.get("blockers", []), "readiness": report.get("readiness")},
        )
    if binding.get("version") != VERSION:
        raise ControlError(
            "HC-UPGRADE-TARGET-IDENTITY",
            "development package binding does not match the executing runtime version",
            status="INVALIDATED",
            details={"runtimeVersion": VERSION, "bindingVersion": binding.get("version")},
        )
    return skill_root, binding


def _source_context(project: Path) -> tuple[dict[str, Path], Path, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    p = paths(project)
    root = git_root(p["root"])
    _guard_v3_control_plane(p)
    lock = load_json(p["lock"])
    catalog = load_json(p["cases"])
    validate_object("project-governance-lock", lock)
    validate_object("case-catalog", catalog)
    if lock.get("packageMode") != "DEVELOPMENT":
        raise ControlError(
            "HC-UPGRADE-PACKAGE-MODE",
            "the development runtime upgrader cannot replace a sealed package binding",
            status="BLOCKED",
        )
    _validate_lock_reference_closure(root, lock)
    source_files = _snapshot_files(p["control"])
    return p, root, lock, catalog, source_files


def _replacement_catalog(
    root: Path,
    spec: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    relative = spec.get("replacementCaseCatalog")
    if relative is None:
        return None, None
    path = safe_relative(root, relative)
    ref = file_ref(root, path)
    value = load_json(path)
    validate_object("case-catalog", value)
    return value, ref


def _spec_and_ref(
    root: Path,
    spec_path: Path,
    *,
    project_id: str,
    source_version: str,
    target_version: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    spec_path = spec_path.resolve()
    spec_ref = file_ref(root, spec_path)
    spec = load_json(spec_path)
    validate_object("upgrade-spec", spec)
    if spec["projectId"] != project_id:
        raise ControlError("HC-UPGRADE-SPEC-PROJECT", "upgrade spec targets a different project")
    if spec["sourceRuntimeVersion"] != source_version or spec["targetRuntimeVersion"] != target_version:
        raise ControlError(
            "HC-UPGRADE-SPEC-VERSION",
            "upgrade spec does not bind the observed source and target runtime versions",
            status="INVALIDATED",
            details={
                "expectedSource": source_version,
                "actualSource": spec["sourceRuntimeVersion"],
                "expectedTarget": target_version,
                "actualTarget": spec["targetRuntimeVersion"],
            },
        )
    summary_sha = sha256_bytes(spec["confirmation"]["summary"].encode("utf-8"))
    if spec["confirmation"]["summarySha256"] != summary_sha:
        raise ControlError("HC-UPGRADE-CONFIRMATION", "upgrade confirmation summary hash is invalid")
    replacement, replacement_ref = _replacement_catalog(root, spec)
    return spec, spec_ref, replacement, replacement_ref


def upgrade_plan(project: Path, spec_path: Path | None = None) -> dict[str, Any]:
    """Return a read-only, content-bound plan for a same-Schema runtime upgrade."""
    assert_dependencies()
    p, root, lock, _, source_files = _source_context(project)
    excluded_ephemeral = _ephemeral_files(p["control"])
    _, target_binding = _target_package()
    source_version = lock["packageBinding"]["version"]
    source_tuple = _version_tuple(source_version)
    target_tuple = _version_tuple(target_binding["version"])
    if target_tuple == source_tuple:
        raise ControlError("HC-UPGRADE-NOOP", "project already binds the executing runtime version", status="BLOCKED")
    if target_tuple < source_tuple:
        raise ControlError(
            "HC-UPGRADE-DOWNGRADE",
            "the same-Schema upgrader cannot downgrade a project runtime",
            status="BLOCKED",
            details={"sourceVersion": source_version, "targetVersion": target_binding["version"]},
        )

    spec_ref = None
    replacement_ref = None
    if spec_path is not None:
        _, spec_ref, _, replacement_ref = _spec_and_ref(
            root,
            spec_path,
            project_id=lock["projectId"],
            source_version=source_version,
            target_version=target_binding["version"],
        )

    source_snapshot_sha = sha256_bytes(canonical_bytes(source_files))
    source_binding = {
        "packageMode": lock["packageMode"],
        "packageBinding": lock["packageBinding"],
        "lock": file_ref(root, p["lock"]),
        "caseCatalog": file_ref(root, p["cases"]),
    }
    base: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "operation": "runtime-upgrade",
        "planId": f"runtime-{source_version}-to-{target_binding['version']}-{source_snapshot_sha[:12]}",
        "project": str(root),
        "projectId": lock["projectId"],
        "gitHead": git(root, "rev-parse", "HEAD"),
        "sourceRuntimeVersion": source_version,
        "targetRuntimeVersion": target_binding["version"],
        "sourceSnapshotSha256": source_snapshot_sha,
        "sourcePackageBinding": source_binding,
        "targetPackageBinding": target_binding,
        "spec": spec_ref,
        "replacementCaseCatalog": replacement_ref,
        "actions": [
            "archive-bound-control-plane",
            f"install-runtime-{target_binding['version']}",
            "recompile-rules",
            "replace-package-binding",
            "invalidate-downstream",
            "reset-diagnostic-state",
        ],
        "invalidates": INVALIDATES,
        "risk": "R2",
    }
    base["planHash"] = sha256_bytes(canonical_bytes(base))
    validate_object("upgrade-plan", base)
    blocker = "HC-UPGRADE-APPROVAL" if spec_ref is not None else "HC-UPGRADE-SPEC-REQUIRED"
    message = (
        "apply requires the exact content-bound plan hash"
        if spec_ref is not None
        else "review the source/target bindings and provide one tracked confirmed upgrade spec"
    )
    return envelope(
        status="BLOCKED",
        checks=[
            check(
                "HC-UPGRADE-EPHEMERAL-EXCLUSION",
                "PASS",
                "only reproducible Python bytecode caches are excluded from the source snapshot and recovery archive",
                excludedEphemeral=excluded_ephemeral,
            ),
            check(blocker, "BLOCKED", message),
        ],
        formal={"eligible": False, "maxClaimLevel": "DIAGNOSTIC", "blockers": [blocker]},
        data=base,
    )


def _copy_archive_source(root: Path, live: Path, archive_root: Path) -> dict[str, Any]:
    """Copy the exact old control file tree and prove source/archive equality."""
    source_before = _snapshot_files(live, exclude_top_level={"legacy"})
    excluded_ephemeral = _ephemeral_files(live, exclude_top_level={"legacy"})
    dispositions = _source_dispositions(root, live, source_before)
    ignored = [item["path"] for item in dispositions if item["disposition"] == "IGNORED"]
    if ignored:
        raise ControlError(
            "HC-UPGRADE-ARCHIVE-COMMITTABILITY",
            "ignored control files other than Python bytecode caches cannot enter a committable recovery archive",
            status="BLOCKED",
            details={"ignored": ignored, "excludedEphemeral": excluded_ephemeral},
        )
    if any(".git" in Path(item["path"]).parts for item in source_before):
        raise ControlError(
            "HC-UPGRADE-ARCHIVE-COMMITTABILITY",
            "a nested .git path cannot be represented in a committable recovery archive",
            status="BLOCKED",
        )
    snapshot = archive_root / "control-plane"
    archive_root.mkdir(parents=True)
    live_resolved = live.resolve()

    def ignore_active_archive(directory: str, names: list[str]) -> set[str]:
        ignored = _ignore_ephemeral(directory, names)
        if Path(directory).resolve() == live_resolved and "legacy" in names:
            ignored.add("legacy")
        return ignored

    shutil.copytree(live, snapshot, ignore=ignore_active_archive)
    byte_policy = archive_root / ".gitattributes"
    byte_policy.write_text(ARCHIVE_ATTRIBUTES, encoding="utf-8", newline="\n")

    source_after = _snapshot_files(live, exclude_top_level={"legacy"})
    archive_files = _snapshot_files(snapshot)
    if source_before != source_after or source_before != archive_files:
        raise ControlError(
            "HC-UPGRADE-ARCHIVE-MISMATCH",
            "the source control tree changed while its recovery archive was being created",
            status="INVALIDATED",
            details={
                "plannedSourceSha256": sha256_bytes(canonical_bytes(source_before)),
                "currentSourceSha256": sha256_bytes(canonical_bytes(source_after)),
                "archiveSha256": sha256_bytes(canonical_bytes(archive_files)),
            },
        )
    return _archive_manifest(
        snapshot,
        source_dispositions=dispositions,
        byte_policy=byte_policy,
        excluded_ephemeral=excluded_ephemeral,
    )


def _porcelain_path(line: str) -> str:
    value = line[3:] if len(line) >= 4 else line
    if " -> " in value:
        value = value.rsplit(" -> ", 1)[1]
    return value.strip('"').replace("\\", "/")


def _unexpected_status(root: Path, *owned_names: str) -> list[str]:
    prefixes = tuple(f"{name.rstrip('/')}/" for name in owned_names)
    unexpected: list[str] = []
    # ``common.git`` intentionally strips command output, which removes the
    # leading index-status space from the first porcelain record.  Read the
    # porcelain stream directly here so the first managed path is parsed with
    # exactly the same two status columns as every subsequent path.
    result = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v1"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise ControlError(
            "HC-UPGRADE-TRANSACTION-DRIFT",
            result.stderr.strip() or "Git worktree status could not be revalidated",
            status="INVALIDATED",
        )
    for line in result.stdout.splitlines():
        if not line:
            continue
        relative = _porcelain_path(line)
        if relative in owned_names or relative.startswith(prefixes):
            continue
        unexpected.append(line)
    return unexpected


def _git_metadata_path(root: Path, relative: str) -> Path:
    raw = git(root, "rev-parse", "--git-path", relative)
    candidate = Path(raw)
    return (candidate if candidate.is_absolute() else root / candidate).resolve()


def _acquire_transaction_lock(root: Path, plan_hash: str) -> tuple[int, Path, Path]:
    directory = _git_metadata_path(root, "vibe-control-upgrades")
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / "active.lock"
    journal_path = directory / f"{plan_hash}.journal.json"
    existing_journals = sorted(directory.glob("*.journal.json"))
    if existing_journals:
        raise ControlError(
            "HC-UPGRADE-RECOVERY-REQUIRED",
            "a prior runtime-upgrade journal requires recovery before another apply",
            status="BLOCKED",
            details={"journals": [str(path) for path in existing_journals]},
        )
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ControlError(
            "HC-UPGRADE-TRANSACTION-LOCK",
            "another runtime upgrade transaction is active",
            status="BLOCKED",
            details={"lock": str(lock_path)},
        ) from exc
    try:
        os.write(descriptor, f"plan={plan_hash}\npid={os.getpid()}\n".encode("utf-8"))
        os.fsync(descriptor)
    except OSError as exc:
        os.close(descriptor)
        try:
            lock_path.unlink()
        except OSError:
            pass
        raise ControlError(
            "HC-UPGRADE-TRANSACTION-LOCK",
            "runtime upgrade transaction lock could not be persisted",
            status="BLOCKED",
            details={"lock": str(lock_path), "error": str(exc)},
        ) from exc
    return descriptor, lock_path, journal_path


def _release_transaction_lock(descriptor: int, lock_path: Path) -> None:
    os.close(descriptor)
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


def _write_journal(
    journal: Path,
    *,
    phase: str,
    plan_hash: str,
    live: Path,
    staging: Path,
    backup: Path,
    error: str | None = None,
) -> None:
    value: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "operation": "runtime-upgrade",
        "planHash": plan_hash,
        "phase": phase,
        "live": str(live),
        "staging": str(staging),
        "backup": str(backup),
        "updatedAt": now_iso(),
    }
    if error is not None:
        value["error"] = error
    write_json_atomic(journal, value)


def _preserve_journal(journal: Path, **values: Any) -> str | None:
    try:
        _write_journal(journal, **values)
    except (OSError, KeyboardInterrupt) as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def _replace_path(source: Path, target: Path) -> None:
    os.replace(source, target)


def _ignored_paths(root: Path, relatives: list[str]) -> list[str]:
    if not relatives:
        return []
    result = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "--no-index", "-z", "--stdin"],
        input="\0".join(relatives) + "\0",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode not in (0, 1):
        raise ControlError(
            "HC-UPGRADE-ARCHIVE-COMMITTABILITY",
            result.stderr.strip() or "Git could not evaluate recovery archive ignore rules",
            status="BLOCKED",
        )
    return [value for value in result.stdout.split("\0") if value]


def _validate_installed_archive(root: Path, live: Path, archive_relative: str) -> None:
    archive_root = live / archive_relative
    snapshot = archive_root / "control-plane"
    manifest_path = archive_root / "manifest.json"
    policy_path = archive_root / ".gitattributes"
    manifest = load_json(manifest_path)
    actual = _snapshot_files(snapshot)
    expected = manifest.get("files") if isinstance(manifest, dict) else None
    if expected != actual or manifest.get("snapshotSha256") != sha256_bytes(canonical_bytes(actual)):
        raise ControlError(
            "HC-UPGRADE-ARCHIVE-MISMATCH",
            "installed recovery archive does not match its source-bound manifest",
            status="INVALIDATED",
        )
    if policy_path.read_bytes() != ARCHIVE_ATTRIBUTES.encode("utf-8"):
        raise ControlError(
            "HC-UPGRADE-ARCHIVE-BYTE-POLICY",
            "recovery archive lacks its byte-preserving Git policy",
            status="INVALIDATED",
        )
    final_root = f".vibe-control/{archive_relative}"
    candidate_paths = [
        f"{final_root}/.gitattributes",
        f"{final_root}/manifest.json",
        *(f"{final_root}/control-plane/{item['path']}" for item in actual),
    ]
    ignored = _ignored_paths(root, candidate_paths)
    if ignored:
        raise ControlError(
            "HC-UPGRADE-ARCHIVE-COMMITTABILITY",
            "one or more recovery archive files would be ignored instead of committed",
            status="BLOCKED",
            details={"ignored": ignored},
        )


def _restore_old_live(live: Path, staging: Path, backup: Path) -> None:
    if not backup.exists():
        if not live.exists():
            raise OSError("neither live control plane nor backup exists")
        return
    if live.exists():
        if staging.exists():
            raise OSError("both the replacement staging tree and live tree exist")
        _replace_path(live, staging)
    _replace_path(backup, live)
    if not live.exists():
        raise OSError("restored live control plane is missing")


def _transactional_swap(
    *,
    root: Path,
    live: Path,
    staging: Path,
    backup: Path,
    journal: Path,
    plan_hash: str,
    approved_git_head: str,
    source_snapshot_sha256: str,
    archive_relative: str,
) -> None:
    journal_error = _preserve_journal(
        journal, phase="READY_TO_SWAP", plan_hash=plan_hash,
        live=live, staging=staging, backup=backup,
    )
    if journal_error is not None:
        raise ControlError(
            "HC-UPGRADE-JOURNAL",
            "runtime upgrade journal could not be persisted before directory exchange",
            status="BLOCKED",
            details={"journal": str(journal), "error": journal_error},
        )
    try:
        _replace_path(live, backup)
        _write_journal(
            journal, phase="LIVE_MOVED_TO_BACKUP", plan_hash=plan_hash,
            live=live, staging=staging, backup=backup,
        )
        moved_snapshot = _snapshot_files(backup)
        if sha256_bytes(canonical_bytes(moved_snapshot)) != source_snapshot_sha256:
            raise ControlError(
                "HC-UPGRADE-TRANSACTION-DRIFT",
                "the exact control tree moved into backup no longer matches the approved plan",
                status="INVALIDATED",
            )
        _replace_path(staging, live)
        _write_journal(
            journal, phase="REPLACEMENT_INSTALLED", plan_hash=plan_hash,
            live=live, staging=staging, backup=backup,
        )
        current_head = git(root, "rev-parse", "HEAD")
        unexpected = _unexpected_status(root, ".vibe-control", staging.name, backup.name)
        if current_head != approved_git_head or unexpected:
            raise ControlError(
                "HC-UPGRADE-TRANSACTION-DRIFT",
                "Git HEAD or a non-control worktree path changed during directory exchange",
                status="INVALIDATED",
                details={
                    "approvedGitHead": approved_git_head,
                    "currentGitHead": current_head,
                    "unexpectedStatus": unexpected,
                },
            )
        _validate_installed_archive(root, live, archive_relative)
    except (Exception, KeyboardInterrupt) as exc:
        try:
            _restore_old_live(live, staging, backup)
        except (Exception, KeyboardInterrupt) as restore_exc:
            journal_error = _preserve_journal(
                journal, phase="RECOVERY_REQUIRED", plan_hash=plan_hash,
                live=live, staging=staging, backup=backup,
                error=f"{type(exc).__name__}: {exc}; restore={type(restore_exc).__name__}: {restore_exc}",
            )
            raise ControlError(
                "HC-UPGRADE-RECOVERY-REQUIRED",
                "runtime upgrade could not restore the old live control plane; recovery material was preserved",
                status="BLOCKED",
                details={
                    "journal": str(journal),
                    "live": str(live),
                    "staging": str(staging),
                    "backup": str(backup),
                    "cause": f"{type(exc).__name__}: {exc}",
                    "restoreError": f"{type(restore_exc).__name__}: {restore_exc}",
                    "journalError": journal_error,
                },
            ) from restore_exc
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        try:
            journal.unlink()
        except FileNotFoundError:
            pass
        if isinstance(exc, ControlError):
            raise exc
        raise ControlError(
            "HC-UPGRADE-SWAP-ROLLED-BACK",
            "runtime upgrade was interrupted during directory exchange and the old control plane was restored",
            status="BLOCKED",
            details={"cause": f"{type(exc).__name__}: {exc}"},
        ) from exc

    try:
        shutil.rmtree(backup)
        journal.unlink()
    except OSError as exc:
        journal_error = _preserve_journal(
            journal, phase="REPLACEMENT_INSTALLED_RECOVERY_RESIDUE", plan_hash=plan_hash,
            live=live, staging=staging, backup=backup, error=f"{type(exc).__name__}: {exc}",
        )
        raise ControlError(
            "HC-UPGRADE-RECOVERY-RESIDUE",
            "the new control plane is installed but backup cleanup failed; recovery material was retained",
            status="BLOCKED",
            details={"journal": str(journal), "backup": str(backup), "journalError": journal_error},
        ) from exc


def _verify_runtime_inventory(runtime: Path) -> None:
    manifest = load_json(runtime / "runtime-manifest.json")
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list):
        raise ControlError("HC-UPGRADE-TARGET-RUNTIME", "target runtime manifest files must be an array")
    for index, item in enumerate(files):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ControlError("HC-UPGRADE-TARGET-RUNTIME", f"target runtime manifest entry {index} is invalid")
        path = safe_relative(runtime, item["path"])
        if not path.is_file() or path.stat().st_size != item.get("bytes") or sha256_file(path) != item.get("sha256"):
            raise ControlError(
                "HC-UPGRADE-TARGET-RUNTIME",
                "copied target runtime does not match its manifest",
                status="INVALIDATED",
                details={"path": item["path"]},
            )


def _validate_catalog_against_runtime(catalog: dict[str, Any], compiled: dict[str, Any]) -> None:
    descriptors = {item["id"]: item for item in compiled["canonical"]["runtimeAdapters"]}
    for case in catalog["cases"]:
        adapter = case.get("adapter", {})
        adapter_id = adapter.get("id") if isinstance(adapter, dict) else None
        descriptor = descriptors.get(adapter_id)
        if descriptor is None:
            raise ControlError(
                "HC-UPGRADE-CASE-ADAPTER",
                f"case {case.get('id')} references an adapter absent from the target runtime",
                status="BLOCKED",
            )
        expected = _adapter_binding(compiled, adapter)
        if adapter != expected:
            raise ControlError(
                "HC-UPGRADE-CASE-ADAPTER",
                f"case {case.get('id')} adapter binding drifted; provide a confirmed replacement case catalog",
                status="BLOCKED",
                details={"expected": expected, "actual": adapter},
            )
        validate_adapter_case_contract(case["id"], case, descriptor)
    coverage = coverage_check(compiled, catalog["cases"])
    if coverage["status"] != "PASS":
        raise ControlError(coverage["id"], coverage["message"], status=coverage["status"], details=coverage.get("details"))


def _write_skill_bindings(staging: Path, compiled: dict[str, Any]) -> list[dict[str, Any]]:
    directory = staging / "skill-bindings"
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    refs: list[dict[str, Any]] = []
    for binding in compiled["canonical"]["skillBindings"]:
        binding_id = require_identifier(binding["skillId"], "skillId")
        value = {
            "schemaVersion": SCHEMA_VERSION,
            "skillId": binding_id,
            "version": binding["version"],
            "treeSha256": binding["treeSha256"],
            "requirement": binding["requirement"],
            "role": binding["role"],
            "triggerConditions": binding["triggerConditions"],
            "writePermissions": binding["writePermissions"],
            "canApprove": False,
            "path": binding["path"],
        }
        validate_object("skill-binding", value)
        relative = f"skill-bindings/{binding_id}.json"
        write_json_atomic(staging / relative, value)
        refs.append(_staged_ref(staging, relative))
    return refs


def _revalidate_transaction_inputs(
    project: Path,
    spec_path: Path,
    planned: dict[str, Any],
    *,
    staging: Path,
    backup: Path,
) -> None:
    unexpected = _unexpected_status(project, staging.name, backup.name)
    if unexpected:
        raise ControlError(
            "HC-UPGRADE-TRANSACTION-DRIFT",
            "the Git worktree changed while the runtime upgrade was staged",
            status="INVALIDATED",
            details={"status": unexpected},
        )
    try:
        current = upgrade_plan(project, spec_path)["data"]
    except ControlError as exc:
        raise ControlError(
            "HC-UPGRADE-TRANSACTION-DRIFT",
            "an approved runtime-upgrade input became invalid while staging",
            status="INVALIDATED",
            details={"cause": exc.check_id, "message": exc.message},
        ) from exc
    if current["planHash"] != planned["planHash"]:
        raise ControlError(
            "HC-UPGRADE-TRANSACTION-DRIFT",
            "HEAD, source snapshot, spec, case catalog or target package drifted while staging",
            status="INVALIDATED",
            details={"planned": planned["planHash"], "current": current["planHash"]},
        )


def upgrade_apply(project: Path, plan_hash: str, spec_path: Path) -> dict[str, Any]:
    """Run an upgrade under an exclusive Git-metadata transaction lock."""
    if re.fullmatch(r"[0-9a-f]{64}", plan_hash) is None:
        raise ControlError("HC-UPGRADE-PLAN-HASH", "runtime upgrade plan hash is invalid", status="INVALIDATED")
    p = paths(project)
    root = git_root(p["root"])
    descriptor, lock_path, journal = _acquire_transaction_lock(root, plan_hash)
    try:
        return _upgrade_apply_locked(project, plan_hash, spec_path, journal)
    finally:
        _release_transaction_lock(descriptor, lock_path)


def _upgrade_apply_locked(project: Path, plan_hash: str, spec_path: Path, journal: Path) -> dict[str, Any]:
    """Atomically replace a Schema 3.2 development runtime and invalidate old facts."""
    p = paths(project)
    root = git_root(p["root"])
    staging = root / f".vibe-control.upgrade-{plan_hash[:12]}.tmp"
    backup = root / f".vibe-control.upgrade-{plan_hash[:12]}.backup"
    if staging.exists() or backup.exists():
        raise ControlError(
            "HC-UPGRADE-RECOVERY-REQUIRED",
            "upgrade staging or backup recovery material already exists",
            status="BLOCKED",
            details={"staging": str(staging), "backup": str(backup), "journal": str(journal)},
        )
    if clean_status(root):
        raise ControlError("HC-WORKTREE-CLEAN", "runtime upgrade apply requires a clean worktree", status="BLOCKED")
    planned = upgrade_plan(project, spec_path)["data"]
    if planned["planHash"] != plan_hash:
        raise ControlError("HC-UPGRADE-PLAN-HASH", "runtime upgrade plan hash mismatch", status="INVALIDATED")

    p, root, lock, source_catalog, _ = _source_context(project)
    skill_root, target_binding = _target_package()
    spec, spec_ref, replacement_catalog, _ = _spec_and_ref(
        root,
        spec_path,
        project_id=lock["projectId"],
        source_version=planned["sourceRuntimeVersion"],
        target_version=planned["targetRuntimeVersion"],
    )
    catalog = replacement_catalog if replacement_catalog is not None else source_catalog

    new_state: dict[str, Any] | None = None
    archive_relative = f"legacy/runtime-upgrade-{plan_hash}"
    try:
        prior_legacy = _snapshot_files(p["control"] / "legacy") if (p["control"] / "legacy").exists() else []
        shutil.copytree(p["control"], staging, ignore=_ignore_ephemeral)
        staged_legacy = _snapshot_files(staging / "legacy") if (staging / "legacy").exists() else []
        if staged_legacy != prior_legacy:
            raise ControlError(
                "HC-UPGRADE-LEGACY-PRESERVATION",
                "existing runtime-upgrade archives were not preserved byte-for-byte in staging",
                status="INVALIDATED",
                details={
                    "sourceSha256": sha256_bytes(canonical_bytes(prior_legacy)),
                    "stagedSha256": sha256_bytes(canonical_bytes(staged_legacy)),
                },
            )
        archive_root = staging / archive_relative
        archive_manifest = _copy_archive_source(root, p["control"], archive_root)
        write_json_atomic(archive_root / "manifest.json", archive_manifest)

        for name in DOWNSTREAM_DIRECTORIES:
            target = staging / name
            if target.exists():
                shutil.rmtree(target)
        runtime_directory = staging / "runtime"
        if runtime_directory.exists():
            shutil.rmtree(runtime_directory)
        package_receipt = staging / "governance" / "package-audit-receipt.json"
        if package_receipt.exists():
            package_receipt.unlink()

        new_runtime = staging / "runtime" / VERSION
        copy_runtime_bundle(new_runtime)
        _verify_runtime_inventory(new_runtime)
        (staging / ".gitattributes").write_text(CONTROL_ATTRIBUTES, encoding="utf-8", newline="\n")

        validate_object("case-catalog", catalog)
        write_json_atomic(staging / "case-catalog.json", catalog)
        rule_inputs = load_json(staging / "rule-inputs.json")
        positioning = load_json(staging / "project-positioning.json")
        # The executing target runtime has already byte-verified the copied
        # bundle above. Compile with that exact implementation instead of
        # routing the newly copied path through the legacy-runtime adapter.
        compiled = compile_for_project(rule_inputs, root, runtime_root())
        fail_on_compile_issues(compiler_checks(compiled))
        _validate_catalog_against_runtime(catalog, compiled)

        resolved = {
            "schemaVersion": SCHEMA_VERSION,
            "ruleSetId": f"rules-{compiled['canonicalSha256'][:16]}",
            "positioning": _staged_ref(staging, "project-positioning.json"),
            "compiler": {
                "id": "vibe-control-project-rules",
                "version": VERSION,
                "sha256": sha256_file(new_runtime / "vibe_runtime" / "project_rules.py"),
            },
            "canonical": compiled["canonical"],
            "canonicalSha256": compiled["canonicalSha256"],
            "conflicts": compiled["conflicts"],
            "warnings": compiled["warnings"],
            "investigations": compiled["investigations"],
            "installRequests": compiled["installRequests"],
            "blockers": compiled["blockers"],
            "canApprove": False,
            "compiledAt": now_iso(),
        }
        validate_object("resolved-rule-set", resolved)
        write_json_atomic(staging / "resolved-rule-set.json", resolved)
        skill_binding_refs = _write_skill_bindings(staging, compiled)

        governance = staging / "governance"
        governance.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill_root / "package-manifest.json", governance / "package-manifest.json")
        shutil.copy2(skill_root / "references" / "controller-assurance-matrix.json", governance / "controller-assurance-matrix.json")

        package_binding: dict[str, Any] = {
            "version": target_binding["version"],
            "sourceKind": target_binding["sourceKind"],
            "packageManifest": _staged_ref(staging, "governance/package-manifest.json"),
            "runtimeManifest": _staged_ref(staging, f"runtime/{VERSION}/runtime-manifest.json"),
            "assuranceMatrix": _staged_ref(staging, "governance/controller-assurance-matrix.json"),
        }
        if "commit" in target_binding and "tree" in target_binding:
            package_binding.update({"commit": target_binding["commit"], "tree": target_binding["tree"]})

        new_lock = {
            **lock,
            "lockId": f"lock-{lock['projectId']}-runtime-{VERSION}",
            "packageMode": "DEVELOPMENT",
            "packageBinding": package_binding,
            "skill": _staged_ref(staging, "governance/package-manifest.json"),
            "runtime": _staged_ref(staging, f"runtime/{VERSION}/runtime-manifest.json"),
            "keyObjectives": _staged_ref(staging, "key-objectives-lock.json"),
            "caseCatalog": _staged_ref(staging, "case-catalog.json"),
            "ruleInputs": _staged_ref(staging, "rule-inputs.json"),
            "positioning": _staged_ref(staging, "project-positioning.json"),
            "resolvedRuleSet": _staged_ref(staging, "resolved-rule-set.json"),
            "ruleCompiler": _staged_ref(staging, f"runtime/{VERSION}/vibe_runtime/project_rules.py"),
            "profileDirectory": _staged_ref(staging, f"runtime/{VERSION}/rules/v1/profiles.json"),
            "adapterDirectory": _staged_ref(staging, f"runtime/{VERSION}/rules/v1/adapters.json"),
            "skillBindings": skill_binding_refs,
            "lockedAt": now_iso(),
        }
        if (staging / "automation-policy.json").is_file():
            new_lock["automationPolicy"] = _staged_ref(staging, "automation-policy.json")
        else:
            new_lock.pop("automationPolicy", None)
        if _version_tuple(target_binding["version"]) >= (0, 3, 7):
            new_lock["evidenceBytePolicy"] = _staged_ref(staging, ".gitattributes")
        else:
            new_lock.pop("evidenceBytePolicy", None)
        new_lock.pop("packageAuditReceipt", None)
        validate_object("project-governance-lock", new_lock)
        write_json_atomic(staging / "project-governance-lock.json", new_lock)

        new_state = initial_state(lock["projectId"], positioning["positioningId"], resolved["ruleSetId"])
        validate_object("stage-state", new_state)
        write_json_atomic(staging / "stage-state.json", new_state)
        upgrade_record = {
            "schemaVersion": SCHEMA_VERSION,
            "operation": "runtime-upgrade",
            "plan": planned,
            "spec": spec_ref,
            "confirmation": spec["confirmation"],
            "archiveManifest": _staged_ref(staging, f"{archive_relative}/manifest.json"),
            "appliedAt": now_iso(),
        }
        write_json_atomic(governance / f"runtime-upgrade-{plan_hash[:12]}.json", upgrade_record)

        for name, kind in (
            ("project-governance-lock.json", "project-governance-lock"),
            ("key-objectives-lock.json", "key-objectives-lock"),
            ("project-positioning.json", "project-positioning"),
            ("resolved-rule-set.json", "resolved-rule-set"),
            ("case-catalog.json", "case-catalog"),
            ("stage-state.json", "stage-state"),
        ):
            validate_object(kind, load_json(staging / name))
        persisted_archive = load_json(archive_root / "manifest.json")
        if persisted_archive != archive_manifest:
            raise ControlError("HC-UPGRADE-ARCHIVE-MISMATCH", "upgrade archive manifest changed during staging")
    except (OSError, shutil.Error) as exc:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise ControlError(
            "HC-UPGRADE-STAGING",
            f"runtime upgrade staging failed before a complete replacement: {type(exc).__name__}",
            status="BLOCKED",
            details={"error": str(exc)},
        ) from exc
    except KeyboardInterrupt as exc:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise ControlError(
            "HC-UPGRADE-INTERRUPTED",
            "runtime upgrade was interrupted before directory exchange; the live control plane is unchanged",
            status="BLOCKED",
        ) from exc
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise

    try:
        _revalidate_transaction_inputs(root, spec_path, planned, staging=staging, backup=backup)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    _transactional_swap(
        root=root,
        live=p["control"],
        staging=staging,
        backup=backup,
        journal=journal,
        plan_hash=plan_hash,
        approved_git_head=planned["gitHead"],
        source_snapshot_sha256=planned["sourceSnapshotSha256"],
        archive_relative=archive_relative,
    )

    assert new_state is not None
    return envelope(
        status="BLOCKED",
        checks=[check("HC-UPGRADE-INVALIDATION", "BLOCKED", "old runtime and downstream objects were archived; no prior claim was inherited")],
        formal={"eligible": False, "maxClaimLevel": "DIAGNOSTIC", "blockers": ["HC-UPGRADE-INVALIDATION"]},
        state={"declared": new_state, "derived": new_state},
        data={
            "planHash": plan_hash,
            "fromVersion": planned["sourceRuntimeVersion"],
            "toVersion": planned["targetRuntimeVersion"],
            "legacy": f".vibe-control/{archive_relative}",
            "invalidated": planned["invalidates"],
            "next": "commit the runtime upgrade, then create and confirm a fresh Schema 3.2 task",
        },
    )
