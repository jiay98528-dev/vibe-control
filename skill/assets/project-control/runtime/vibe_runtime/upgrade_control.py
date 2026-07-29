from __future__ import annotations

import re
import shutil
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
    _atomic_replace_control_plane,
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
ARCHIVE_ITEMS = [
    ".gitattributes",
    "project-governance-lock.json",
    "stage-state.json",
    "key-objectives-lock.json",
    "automation-policy.json",
    "project-positioning.json",
    "rule-inputs.json",
    "resolved-rule-set.json",
    "case-catalog.json",
    "skill-bindings",
    "governance",
    "runtime",
    "tasks",
    "task-locks",
    "candidates",
    "evidence",
    "reviews",
    "decisions",
    "external-audits",
    "handoffs",
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


def _snapshot_files(root: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ControlError(
                "HC-UPGRADE-SOURCE-SYMLINK",
                "runtime upgrades do not archive symbolic links from the control plane",
                details={"path": path.relative_to(root).as_posix()},
            )
        if path.is_file():
            files.append({
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })
    return files


def _archive_manifest(snapshot: Path) -> dict[str, Any]:
    files = _snapshot_files(snapshot)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "hashAlgorithm": "sha256",
        "files": files,
        "snapshotSha256": sha256_bytes(canonical_bytes(files)),
    }


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


def _verified_ref(root: Path, ref: Any, check_id: str) -> dict[str, Any]:
    if not isinstance(ref, dict):
        raise ControlError(check_id, "upgrade source omits a required content reference")
    result = verify_ref(root, ref, check_id)
    if result["status"] != "PASS":
        raise ControlError(result["id"], result["message"], status=result["status"], details=result.get("details"))
    return ref


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
    package_binding = lock.get("packageBinding", {})
    for name, check_id in (
        ("packageManifest", "HC-UPGRADE-SOURCE-PACKAGE"),
        ("runtimeManifest", "HC-UPGRADE-SOURCE-RUNTIME"),
        ("assuranceMatrix", "HC-UPGRADE-SOURCE-MATRIX"),
    ):
        _verified_ref(root, package_binding.get(name), check_id)
    _verified_ref(root, lock.get("caseCatalog"), "HC-UPGRADE-SOURCE-CASE-CATALOG")
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
        checks=[check(blocker, "BLOCKED", message)],
        formal={"eligible": False, "maxClaimLevel": "DIAGNOSTIC", "blockers": [blocker]},
        data=base,
    )


def _copy_archive_source(live: Path, snapshot: Path) -> None:
    snapshot.mkdir(parents=True)
    for name in ARCHIVE_ITEMS:
        source = live / name
        if not source.exists():
            continue
        target = snapshot / name
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


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


def upgrade_apply(project: Path, plan_hash: str, spec_path: Path) -> dict[str, Any]:
    """Atomically replace a Schema 3.2 development runtime and invalidate old facts."""
    p = paths(project)
    root = git_root(p["root"])
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

    staging = root / f".vibe-control.upgrade-{plan_hash[:12]}.tmp"
    backup = root / f".vibe-control.upgrade-{plan_hash[:12]}.backup"
    if staging.exists() or backup.exists():
        raise ControlError("HC-UPGRADE-STAGING", "upgrade staging or backup path already exists", status="BLOCKED")

    new_state: dict[str, Any] | None = None
    archive_relative = f"legacy/runtime-upgrade-{plan_hash}"
    try:
        shutil.copytree(p["control"], staging)
        archive_root = staging / archive_relative
        snapshot = archive_root / "control-plane"
        _copy_archive_source(p["control"], snapshot)
        write_json_atomic(archive_root / "manifest.json", _archive_manifest(snapshot))

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
        if _archive_manifest(snapshot) != load_json(archive_root / "manifest.json"):
            raise ControlError("HC-UPGRADE-ARCHIVE", "upgrade archive manifest does not match archived bytes")
        _atomic_replace_control_plane(p["control"], staging, backup)
    except (OSError, shutil.Error) as exc:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise ControlError(
            "HC-UPGRADE-STAGING",
            f"runtime upgrade staging failed before a complete replacement: {type(exc).__name__}",
            status="BLOCKED",
            details={"error": str(exc)},
        ) from exc
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise

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
