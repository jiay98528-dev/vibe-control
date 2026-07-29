from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


REQUIRED_PACKAGE_CONTROL_IDS = frozenset({
    "CTRL-ASSURE-001", "CTRL-ASSURE-002", "CTRL-ASSURE-003", "CTRL-ASSURE-004",
    "CTRL-ASSURE-005", "CTRL-ASSURE-006", "CTRL-ASSURE-007", "CTRL-ASSURE-008",
    "CTRL-CONFIRMED-001", "CTRL-CONFIRMED-002", "CTRL-CONFIRMED-003",
    "CTRL-CONFIRMED-004", "CTRL-CONFIRMED-005", "CTRL-CONFIRMED-006",
    "CTRL-CONFIRMED-007", "CTRL-CONFIRMED-008", "CTRL-CONFIRMED-009",
    "CTRL-CONFIRMED-010", "CTRL-CONFIRMED-011", "CTRL-CONFIRMED-012",
    "CTRL-CONFIRMED-013", "CTRL-CONFIRMED-014", "CTRL-CONFIRMED-015",
    "CTRL-CONFIRMED-016", "CTRL-CONFIRMED-017", "CTRL-CONFIRMED-018",
    "CTRL-CONFIRMED-019", "CTRL-CONFIRMED-020", "CTRL-CONFIRMED-021",
    "CTRL-CONFIRMED-022", "CTRL-CONFIRMED-023", "CTRL-CONFIRMED-024",
    "CTRL-CONFIRMED-025", "CTRL-CONFIRMED-026", "CTRL-CONFIRMED-027",
    "CTRL-CONFIRMED-028", "CTRL-CONFIRMED-029", "CTRL-CONFIRMED-030",
    "CTRL-CONFIRMED-031", "CTRL-CONFIRMED-032", "CTRL-CONFIRMED-033",
    "CTRL-CONFIRMED-034", "CTRL-CONFIRMED-035", "CTRL-CONFIRMED-036",
})


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _git(root: Path, *args: str) -> tuple[int, str, str]:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _git_bytes(root: Path, *args: str) -> tuple[int, bytes, str]:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    return result.returncode, result.stdout, result.stderr.decode("utf-8", errors="replace").strip()


def _check(check_id: str, status: str, message: str, **details: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"id": check_id, "status": status, "message": message}
    if details:
        value["details"] = details
    return value


def _schema_check(root: Path, kind: str, value: Any, check_id: str) -> dict[str, Any]:
    try:
        schema = json.loads((root / "assets" / "project-control" / "runtime" / "schemas" / f"{kind}.schema.json").read_text(encoding="utf-8-sig"))
        errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value), key=lambda item: list(item.absolute_path))
    except Exception as exc:
        return _check(check_id, "FAIL", f"{kind} schema could not be evaluated", error=str(exc))
    if not errors:
        return _check(check_id, "PASS", f"{kind} satisfies JSON Schema 2020-12")
    first = errors[0]
    location = "/".join(str(part) for part in first.absolute_path) or "$"
    return _check(check_id, "FAIL", f"{kind} schema violation at {location}: {first.message}")


def _manifest_builder(root: Path) -> Any:
    """Load the canonical inventory implementation instead of duplicating it."""
    path = root / "scripts" / "build_manifest.py"
    spec = importlib.util.spec_from_file_location("vibe_control_build_manifest", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("canonical manifest builder cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest_inventory_checks(root: Path, package: Any, runtime: Any) -> list[dict[str, Any]]:
    """Compare the recorded manifests with freshly calculated byte inventories."""
    try:
        builder = _manifest_builder(root)
        current_runtime = builder.build_runtime_manifest(root)
        current_package = builder.build(root)
        runtime_ok = runtime == current_runtime
        package_ok = package == current_package
        runtime_details = builder.manifest_diff(runtime if isinstance(runtime, dict) else {}, current_runtime)
        package_details = builder.manifest_diff(package if isinstance(package, dict) else {}, current_package)
    except Exception as exc:
        return [
            _check("RUNTIME-MANIFEST-VERIFY", "FAIL", "runtime manifest inventory could not be verified", error=str(exc)),
            _check("PKG-MANIFEST-VERIFY", "FAIL", "package manifest inventory could not be verified", error=str(exc)),
        ]
    return [
        _check(
            "RUNTIME-MANIFEST-VERIFY",
            "PASS" if runtime_ok else "FAIL",
            "runtime manifest matches current bytes" if runtime_ok else "runtime manifest does not match current bytes",
            counters=runtime_details,
        ),
        _check(
            "PKG-MANIFEST-VERIFY",
            "PASS" if package_ok else "FAIL",
            "package manifest matches current bytes" if package_ok else "package manifest does not match current bytes",
            counters=package_details,
        ),
    ]


def validate_development_package(root: Path) -> dict[str, Any]:
    """Validate a development package without pretending every install has Git provenance."""
    root = root.resolve()
    checks: list[dict[str, Any]] = []
    code, top, _ = _git(root, "rev-parse", "--show-toplevel")
    source_kind = "PORTABLE_COPY"
    head: str | None = None
    tree: str | None = None
    git_marker = root / ".git"
    if code == 0:
        repository = Path(top).resolve()
        try:
            relative = root.relative_to(repository)
        except ValueError:
            checks.append(_check("PKG-DEV-GIT-IDENTITY", "FAIL", "Git reported a repository that does not contain the Skill root", repository=str(repository)))
        else:
            source_kind = "GIT_ROOT" if relative == Path(".") else "GIT_SUBDIRECTORY"
            head_code, head_value, head_error = _git(repository, "rev-parse", "HEAD")
            head = head_value if head_code == 0 and len(head_value) == 40 else None
            if source_kind == "GIT_ROOT":
                tree_code, tree_value, tree_error = _git(repository, "show", "-s", "--format=%T", "HEAD")
                tree = tree_value if tree_code == 0 and len(tree_value) == 40 else None
                _, dirty, _ = _git(repository, "status", "--porcelain=v1", "--untracked-files=all")
            else:
                relative_path = relative.as_posix()
                tree_code, tree_value, tree_error = _git(repository, "rev-parse", f"HEAD:{relative_path}")
                tree = tree_value if tree_code == 0 and len(tree_value) == 40 else None
                checks.append(_check(
                    "PKG-DEV-GIT-SUBTREE",
                    "PASS" if tree is not None else "FAIL",
                    "development package is a tracked Git subtree" if tree is not None else "development package is not present as a tree in the current commit",
                    path=relative_path,
                    error=tree_error,
                ))
                _, dirty, _ = _git(repository, "status", "--porcelain=v1", "--untracked-files=all", "--", relative_path)
            checks.append(_check(
                "PKG-DEV-GIT-IDENTITY",
                "PASS" if head is not None and tree is not None else "FAIL",
                "development package binds the current Git commit and package tree" if head is not None and tree is not None else "development package Git commit or tree identity is unavailable",
                sourceKind=source_kind,
                headError=head_error,
                treeError=tree_error,
            ))
            checks.append(_check(
                "PKG-DEV-WORKTREE-CLEAN",
                "PASS" if not dirty else "BLOCKED",
                "development package source scope is clean" if not dirty else "development package source scope is dirty",
                sourceKind=source_kind,
                entries=dirty.splitlines(),
            ))
    elif git_marker.exists():
        checks.append(_check("PKG-DEV-GIT-IDENTITY", "FAIL", "a .git marker exists but does not identify a usable repository"))
    else:
        checks.append(_check(
            "PKG-DEV-PORTABLE-IDENTITY",
            "PASS",
            "portable development package has no Git provenance and will be identified only by recomputed content inventories",
        ))
    try:
        version = (root / "VERSION").read_text(encoding="utf-8-sig").strip()
        package_path = root / "package-manifest.json"
        runtime_path = root / "assets" / "project-control" / "runtime" / "runtime-manifest.json"
        matrix_path = root / "references" / "controller-assurance-matrix.json"
        package = json.loads(package_path.read_text(encoding="utf-8-sig"))
        runtime = json.loads(runtime_path.read_text(encoding="utf-8-sig"))
        matrix = json.loads(matrix_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        checks.append(_check("PKG-DEV-CONTENT", "FAIL", "development package inputs are missing or malformed", error=str(exc)))
        return {"status": "FAIL", "readiness": "DIAGNOSTIC", "packageMode": "DEVELOPMENT", "formalClaimsAllowed": False, "maxClaimLevel": "DIAGNOSTIC", "checks": checks, "blockers": [item["id"] for item in checks if item["status"] != "PASS"]}
    checks.extend(_manifest_inventory_checks(root, package, runtime))
    assurance = package.get("assuranceValidation") if isinstance(package, dict) else None
    content_ready = (
        isinstance(package, dict)
        and package.get("version") == version
        and package.get("maturity") == "DEVELOPMENT_DIAGNOSTIC"
        and isinstance(assurance, dict)
        and assurance.get("status") == "PASS"
        and assurance.get("readiness") in {"CONTROL_IMPLEMENTATION_READY", "CONTROL_IMPLEMENTATION_PENDING_EXTERNAL_VALIDATION"}
        and assurance.get("formalClaimsAllowed") is False
        and isinstance(matrix, dict)
        and matrix.get("formalClaimsAllowed") is False
    )
    checks.append(_check("PKG-DEV-CONTENT-CLOSURE", "PASS" if content_ready else "FAIL", "development package integrity is closed without formal qualification" if content_ready else "development package content closure is invalid"))
    blockers = [item["id"] for item in checks if item["status"] != "PASS"]
    priorities = {"PASS": 0, "BLOCKED": 1, "INVALIDATED": 2, "FAIL": 3}
    status = max((item["status"] for item in checks), key=lambda item: priorities[item])
    return {
        "status": status,
        "readiness": "DEVELOPMENT_CHECKED" if not blockers else "DIAGNOSTIC",
        "packageMode": "DEVELOPMENT",
        "formalClaimsAllowed": False,
        "maxClaimLevel": "DEVELOPMENT_CHECKED" if not blockers else "DIAGNOSTIC",
        "checks": checks,
        "blockers": blockers,
        "binding": {
            "version": version,
            "sourceKind": source_kind,
            **({"commit": head, "tree": tree} if head is not None and tree is not None else {}),
            "packageManifestSha256": _sha256_file(package_path),
            "runtimeManifestSha256": _sha256_file(runtime_path),
            "assuranceMatrixSha256": _sha256_file(matrix_path),
        } if not blockers else None,
    }


def _tree_index(root: Path, tree: str) -> tuple[dict[str, dict[str, str]], str | None]:
    code, raw, error = _git_bytes(root, "ls-tree", "-r", "-z", tree)
    if code != 0:
        return {}, error or "audit bundle tree cannot be read"
    entries: dict[str, dict[str, str]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            header, raw_path = record.split(b"\t", 1)
            mode, kind, oid = header.decode("ascii").split(" ", 2)
            path = raw_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return {}, "audit bundle tree contains an invalid entry"
        if path in entries:
            return {}, f"duplicate audit bundle path: {path}"
        entries[path] = {"mode": mode, "type": kind, "oid": oid}
    return entries, None


def _blob_json(root: Path, oid: str) -> tuple[Any | None, bytes, str | None]:
    code, value, error = _git_bytes(root, "cat-file", "blob", oid)
    if code != 0:
        return None, b"", error or "blob cannot be read"
    try:
        return json.loads(value.decode("utf-8-sig")), value, None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, value, str(exc)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _validate_audit_evidence(
    root: Path,
    *,
    tree_entries: dict[str, dict[str, str]],
    report: dict[str, Any],
    receipt: dict[str, Any],
    expected: dict[str, str],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    manifest_entry = tree_entries.get("evidence-manifest.json", {})
    manifest_oid = manifest_entry.get("oid")
    manifest_ref_ok = (
        manifest_entry.get("type") == "blob"
        and manifest_oid == report.get("evidenceManifestBlob")
        and manifest_oid == receipt.get("evidenceManifestBlob")
    )
    if not manifest_ref_ok:
        checks.append(_check("PKG-AUDIT-EVIDENCE-MANIFEST", "FAIL", "audit evidence manifest is missing from the sealed bundle or its blob identity drifted"))
        return checks
    manifest, manifest_bytes, error = _blob_json(root, manifest_oid)
    if error or not isinstance(manifest, dict):
        checks.append(_check("PKG-AUDIT-EVIDENCE-MANIFEST", "FAIL", "audit evidence manifest is not readable JSON", error=error))
        return checks
    manifest_sha = _sha256_bytes(manifest_bytes)
    manifest_hash_ok = (
        manifest_sha == report.get("evidenceManifestSha256")
        and manifest_sha == receipt.get("evidenceManifestSha256")
    )
    schema_check = _schema_check(root, "package-audit-evidence-manifest", manifest, "PKG-AUDIT-EVIDENCE-MANIFEST")
    if not manifest_hash_ok:
        checks.append(_check(
            "PKG-AUDIT-EVIDENCE-BINDING",
            "FAIL",
            "audit evidence manifest SHA-256 differs from the sealed report or receipt",
            actualSha256=manifest_sha,
        ))
    if schema_check["status"] != "PASS":
        checks.append(_check(
            "PKG-AUDIT-EVIDENCE-MANIFEST",
            "FAIL",
            "audit evidence manifest violates its schema",
            schema=schema_check,
        ))
        missing_transcript = any(
            isinstance(case, dict)
            and any(key not in case for key in ("transcriptPath", "transcriptBlob", "transcriptSha256", "transcriptBytes"))
            for case in manifest.get("cases", []) if isinstance(manifest.get("cases"), list)
        )
        if missing_transcript:
            checks.append(_check("PKG-AUDIT-EVIDENCE-TRANSCRIPT", "FAIL", "one or more audit cases omit a required transcript binding"))
        missing_artifacts = any(
            isinstance(case, dict)
            and (not isinstance(case.get("artifacts"), list) or not case.get("artifacts"))
            for case in manifest.get("cases", []) if isinstance(manifest.get("cases"), list)
        )
        if missing_artifacts:
            checks.append(_check("PKG-AUDIT-EVIDENCE-ARTIFACT", "FAIL", "one or more audit cases omit a required artifact binding"))
        return checks
    if not manifest_hash_ok:
        return checks
    checks.append(_check("PKG-AUDIT-EVIDENCE-MANIFEST", "PASS", "audit evidence manifest is schema-valid and content-bound"))

    binding_expected: dict[str, Any] = {
        **expected,
        "reportId": report.get("reportId"),
        "auditor": report.get("auditor"),
        "auditedAt": report.get("auditedAt"),
    }
    binding_mismatches = {
        key: {"expected": value, "actual": manifest.get(key)}
        for key, value in binding_expected.items()
        if manifest.get(key) != value
    }
    cases = manifest.get("cases", [])
    if not isinstance(cases, list) or not cases:
        binding_mismatches["cases"] = {"expected": "non-empty array", "actual": cases}
    case_ids = [case.get("caseId") for case in cases if isinstance(case, dict)]
    if len(case_ids) != len(cases) or len(set(case_ids)) != len(case_ids):
        binding_mismatches["caseIds"] = {"expected": "unique string IDs", "actual": case_ids}
    checks.append(_check(
        "PKG-AUDIT-EVIDENCE-BINDING",
        "PASS" if not binding_mismatches else "FAIL",
        "audit evidence binds the exact package candidate" if not binding_mismatches else "audit evidence candidate or identity binding drifted",
        mismatches=binding_mismatches,
    ))

    counter_errors: list[dict[str, Any]] = []
    transcript_errors: list[dict[str, Any]] = []
    artifact_errors: list[dict[str, Any]] = []
    observed_controls: set[str] = set()
    aggregate = {"executed": 0, "passed": 0, "failed": 0, "skipped": 0}
    audited_at = _parse_time(report.get("auditedAt"))
    for case in cases if isinstance(cases, list) else []:
        if not isinstance(case, dict):
            continue
        case_id = case.get("caseId")
        command = case.get("command")
        counters = case.get("counters", {})
        started = _parse_time(case.get("startedAt"))
        finished = _parse_time(case.get("finishedAt"))
        case_ok = (
            isinstance(command, list) and bool(command) and all(isinstance(part, str) and part for part in command)
            and case.get("exitCode") == 0
            and case.get("status") == "PASS"
            and isinstance(counters, dict)
            and isinstance(counters.get("executed"), int) and counters.get("executed", 0) >= 1
            and isinstance(counters.get("passed"), int) and counters.get("passed", 0) >= 1
            and counters.get("failed") == 0 and counters.get("skipped") == 0
            and counters.get("executed") == counters.get("passed") + counters.get("failed") + counters.get("skipped")
            and started is not None and finished is not None and audited_at is not None
            and started <= finished <= audited_at
        )
        if not case_ok:
            counter_errors.append({"caseId": case_id, "reason": "command, time, exit, result, or counters are not PASS-eligible"})
        if isinstance(counters, dict):
            for key in aggregate:
                value = counters.get(key)
                if isinstance(value, int):
                    aggregate[key] += value

        path = case.get("transcriptPath")
        entry = tree_entries.get(path, {}) if isinstance(path, str) else {}
        oid = case.get("transcriptBlob")
        safe_path = isinstance(path, str) and path.startswith("transcripts/") and ".." not in Path(path).parts and not Path(path).is_absolute()
        transcript_ok = safe_path and entry.get("type") == "blob" and entry.get("oid") == oid
        transcript_bytes = b""
        if transcript_ok:
            code, transcript_bytes, _ = _git_bytes(root, "cat-file", "blob", oid)
            transcript_ok = code == 0
        transcript_ok = (
            transcript_ok
            and len(transcript_bytes) == case.get("transcriptBytes")
            and _sha256_bytes(transcript_bytes) == case.get("transcriptSha256")
            and len(transcript_bytes) > 0
        )
        if not transcript_ok:
            transcript_errors.append({"caseId": case_id, "path": path, "reason": "sealed transcript blob, size, or SHA-256 mismatch"})
        artifacts = case.get("artifacts", [])
        if not isinstance(artifacts, list) or not artifacts:
            artifact_errors.append({"caseId": case_id, "reason": "at least one content-bound artifact is required"})
        if isinstance(artifacts, list):
            for artifact in artifacts:
                if not isinstance(artifact, dict):
                    artifact_errors.append({"caseId": case_id, "reason": "artifact is not an object"})
                    continue
                artifact_path = artifact.get("path")
                artifact_entry = tree_entries.get(artifact_path, {}) if isinstance(artifact_path, str) else {}
                artifact_oid = artifact.get("blob")
                safe_artifact_path = (
                    isinstance(artifact_path, str)
                    and artifact_path.startswith("artifacts/")
                    and ".." not in Path(artifact_path).parts
                    and not Path(artifact_path).is_absolute()
                )
                artifact_ok = safe_artifact_path and artifact_entry.get("type") == "blob" and artifact_entry.get("oid") == artifact_oid
                artifact_bytes = b""
                if artifact_ok:
                    code, artifact_bytes, _ = _git_bytes(root, "cat-file", "blob", artifact_oid)
                    artifact_ok = code == 0
                artifact_ok = (
                    artifact_ok
                    and len(artifact_bytes) == artifact.get("bytes")
                    and _sha256_bytes(artifact_bytes) == artifact.get("sha256")
                    and len(artifact_bytes) > 0
                )
                if not artifact_ok:
                    artifact_errors.append({"caseId": case_id, "path": artifact_path, "reason": "sealed artifact blob, size, or SHA-256 mismatch"})
        controls = case.get("validatedControlIds", [])
        if isinstance(controls, list):
            observed_controls.update(value for value in controls if isinstance(value, str))

    declared_aggregate = manifest.get("counters")
    if aggregate != declared_aggregate:
        counter_errors.append({"reason": "aggregate counters do not conserve per-case counters", "expected": aggregate, "actual": declared_aggregate})
    checks.append(_check(
        "PKG-AUDIT-EVIDENCE-COUNTERS",
        "PASS" if not counter_errors else "FAIL",
        "all audit cases have non-zero conserved PASS counters and zero skip" if not counter_errors else "audit evidence counters, exit codes, commands, or times are not eligible",
        errors=counter_errors,
    ))
    checks.append(_check(
        "PKG-AUDIT-EVIDENCE-TRANSCRIPT",
        "PASS" if not transcript_errors else "FAIL",
        "all audit cases bind non-empty transcripts in the sealed bundle" if not transcript_errors else "one or more audit transcripts are missing or drifted",
        transcriptErrors=transcript_errors,
    ))
    checks.append(_check(
        "PKG-AUDIT-EVIDENCE-ARTIFACT",
        "PASS" if not artifact_errors else "FAIL",
        "all declared audit artifacts are content-bound in the sealed bundle" if not artifact_errors else "one or more audit artifacts are missing or drifted",
        artifactErrors=artifact_errors,
    ))
    coverage_ok = observed_controls == set(REQUIRED_PACKAGE_CONTROL_IDS)
    checks.append(_check(
        "PKG-AUDIT-EVIDENCE-COVERAGE",
        "PASS" if coverage_ok else "FAIL",
        "audit cases cover the fixed assurance control universe" if coverage_ok else "audit case coverage is incomplete or contains unknown controls",
        missing=sorted(set(REQUIRED_PACKAGE_CONTROL_IDS) - observed_controls),
        unknown=sorted(observed_controls - set(REQUIRED_PACKAGE_CONTROL_IDS)),
    ))
    return checks


def _load_tag_json(root: Path, tag_name: str, check_prefix: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    ref = f"refs/tags/{tag_name}"
    code, object_type, _ = _git(root, "cat-file", "-t", ref)
    if code != 0:
        checks.append(_check(f"{check_prefix}-TAG-MISSING", "BLOCKED", f"required annotated tag is missing: {tag_name}"))
        return None, checks
    if object_type != "tag":
        checks.append(_check(f"{check_prefix}-TAG-ANNOTATED", "FAIL", f"tag must be annotated: {tag_name}", actualType=object_type))
        return None, checks
    code, contents, error = _git(root, "for-each-ref", "--format=%(contents)", ref)
    if code != 0:
        checks.append(_check(f"{check_prefix}-TAG-READ", "FAIL", "annotated tag message cannot be read", error=error))
        return None, checks
    try:
        value = json.loads(contents)
    except json.JSONDecodeError as exc:
        checks.append(_check(f"{check_prefix}-TAG-JSON", "FAIL", "annotated tag message must be one JSON object", line=exc.lineno, column=exc.colno))
        return None, checks
    if not isinstance(value, dict):
        checks.append(_check(f"{check_prefix}-TAG-JSON", "FAIL", "annotated tag message must be one JSON object"))
        return None, checks
    checks.append(_check(f"{check_prefix}-TAG-ANNOTATED", "PASS", f"annotated tag is present: {tag_name}"))
    return value, checks


def validate_package_release(root: Path) -> dict[str, Any]:
    """Validate the exact local package candidate and its independent audit seal.

    The package manifest remains a content inventory.  Only this validator may
    combine that inventory with Git object identity and an external audit tag.
    """
    root = root.resolve()
    checks: list[dict[str, Any]] = []
    code, top, _ = _git(root, "rev-parse", "--show-toplevel")
    if code != 0 or Path(top).resolve() != root:
        try:
            package = json.loads((root / "package-manifest.json").read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            package = None
        if isinstance(package, dict) and package.get("maturity") == "DEVELOPMENT_DIAGNOSTIC":
            installation = validate_development_package(root)
            checks.extend(installation.get("checks", []))
            checks.extend([
                _check("PKG-AUDIT-GIT", "BLOCKED", "formal sealing requires the Skill package to be the root of a Git repository"),
                _check("PKG-AUDIT-RELEASE-TAG-MISSING", "BLOCKED", "a portable or Git-subdirectory development install has no package-root release tag"),
                _check("PKG-AUDIT-REPORT-TAG-MISSING", "BLOCKED", "a portable or Git-subdirectory development install has no package-root audit tag"),
                _check(
                    "PKG-DEVELOPMENT-NOT-SEAL-CANDIDATE",
                    "BLOCKED",
                    "the development package may be installation-usable, but it is not a formal seal candidate; run validate_installation.py for installation status",
                ),
            ])
            installation_usable = installation.get("status") == "PASS"
            return {
                "status": "BLOCKED" if installation_usable else "FAIL",
                "readiness": "DEVELOPMENT_INSTALLABLE_NOT_SEAL_CANDIDATE" if installation_usable else "DIAGNOSTIC",
                "packageMode": "DEVELOPMENT",
                "installationUsable": installation_usable,
                "formalClaimsAllowed": False,
                "maxClaimLevel": "DEVELOPMENT_CHECKED" if installation_usable else "DIAGNOSTIC",
                "checks": checks,
                "blockers": [item["id"] for item in checks if item["status"] != "PASS"],
            }
        checks.append(_check("PKG-AUDIT-GIT", "BLOCKED", "the Skill package must be the root of a Git repository"))
        return _report(checks)
    code, head, _ = _git(root, "rev-parse", "HEAD")
    _, tree, _ = _git(root, "show", "-s", "--format=%T", "HEAD")
    _, dirty, _ = _git(root, "status", "--porcelain=v1")
    checks.append(_check("PKG-AUDIT-WORKTREE-CLEAN", "PASS" if not dirty else "FAIL", "package worktree is clean" if not dirty else "package worktree is dirty", entries=dirty.splitlines()))

    try:
        version = (root / "VERSION").read_text(encoding="utf-8-sig").strip()
        package_path = root / "package-manifest.json"
        runtime_path = root / "assets" / "project-control" / "runtime" / "runtime-manifest.json"
        matrix_path = root / "references" / "controller-assurance-matrix.json"
        package = json.loads(package_path.read_text(encoding="utf-8-sig"))
        runtime = json.loads(runtime_path.read_text(encoding="utf-8-sig"))
        matrix = json.loads(matrix_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        checks.append(_check("PKG-AUDIT-CONTENT", "FAIL", "package release inputs are missing or malformed", error=str(exc)))
        return _report(checks)

    # Tag existence/type and byte inventory are independent, safely observable
    # prerequisites.  Collect both before returning so a report never mistakes
    # the first failed check for the only blocker.
    release_tag = f"v{version}"
    audit_tag = f"vibe-control-audit/v{version}"
    receipt, tag_checks = _load_tag_json(root, release_tag, "PKG-AUDIT-RELEASE")
    checks.extend(tag_checks)
    audit_ref = f"refs/tags/{audit_tag}"
    audit_code, audit_type, _ = _git(root, "cat-file", "-t", audit_ref)
    if audit_code != 0:
        checks.append(_check("PKG-AUDIT-REPORT-TAG-MISSING", "BLOCKED", f"required audit tag is missing: {audit_tag}"))
    elif audit_type != "tag":
        checks.append(_check("PKG-AUDIT-REPORT-TAG-ANNOTATED", "FAIL", "audit tag must be annotated", actualType=audit_type))
    else:
        checks.append(_check("PKG-AUDIT-REPORT-TAG-ANNOTATED", "PASS", "audit tag is annotated"))

    inventory_checks = _manifest_inventory_checks(root, package, runtime)
    checks.extend(inventory_checks)
    if any(item["status"] != "PASS" for item in inventory_checks):
        return _report(checks)

    if isinstance(package, dict) and package.get("maturity") == "DEVELOPMENT_DIAGNOSTIC":
        installation = validate_development_package(root)
        installation_usable = installation.get("status") == "PASS"
        checks.append(_check(
            "PKG-DEVELOPMENT-NOT-SEAL-CANDIDATE",
            "BLOCKED",
            "the development package may be installation-usable, but it is not a formal seal candidate; run validate_installation.py for installation status",
            installationStatus=installation.get("status"),
            installationBlockers=installation.get("blockers", []),
        ))
        has_failure = any(item["status"] == "FAIL" for item in checks) or installation.get("status") == "FAIL"
        return {
            "status": "FAIL" if has_failure else "BLOCKED",
            "readiness": "DEVELOPMENT_INSTALLABLE_NOT_SEAL_CANDIDATE" if installation_usable else "DIAGNOSTIC",
            "packageMode": "DEVELOPMENT",
            "installationUsable": installation_usable,
            "formalClaimsAllowed": False,
            "maxClaimLevel": "DEVELOPMENT_CHECKED" if installation_usable else "DIAGNOSTIC",
            "checks": checks,
            "blockers": [item["id"] for item in checks if item["status"] != "PASS"],
        }

    expected = {
        "version": version,
        "candidateCommit": head,
        "candidateTree": tree,
        "packageManifestSha256": _sha256_file(package_path),
        "runtimeManifestSha256": _sha256_file(runtime_path),
        "assuranceMatrixSha256": _sha256_file(matrix_path),
    }
    package_content_ready = (
        isinstance(package, dict)
        and package.get("version") == version
        and package.get("maturity") == "AWAITING_EXTERNAL_VALIDATION"
        and package.get("assuranceValidation") == {
            "status": "PASS",
            "readiness": "CONTROL_IMPLEMENTATION_READY",
            "formalClaimsAllowed": False,
        }
        and isinstance(matrix, dict)
        and matrix.get("formalClaimsAllowed") is False
    )
    checks.append(_check("PKG-AUDIT-CONTENT-CLOSURE", "PASS" if package_content_ready else "FAIL", "content inventory and implementation closure are ready for external audit" if package_content_ready else "package content closure is not ready"))

    if receipt is None or audit_code != 0 or audit_type != "tag":
        return _report(checks)
    _, release_target, _ = _git(root, "rev-parse", f"refs/tags/{release_tag}^{{}}")
    checks.append(_check("PKG-AUDIT-CANDIDATE", "PASS" if release_target == head else "FAIL", "release tag targets current HEAD" if release_target == head else "release tag does not target current HEAD", expected=head, actual=release_target))

    receipt_required = {
        "schemaVersion", "receiptType", "releaseTag", "auditTag", "version",
        "candidateCommit", "candidateTree", "packageManifestSha256",
        "runtimeManifestSha256", "assuranceMatrixSha256", "auditTagObject",
        "auditBundleTree", "auditReportBlob", "auditReportSha256",
        "evidenceManifestBlob", "evidenceManifestSha256", "auditor", "implementer",
        "auditedAt", "result", "findingCounts", "validatedControlIds",
        "enableFormalClaims",
    }
    receipt_shape = receipt_required.issubset(receipt) and receipt.get("schemaVersion") == "3.2" and receipt.get("receiptType") == "vibe-control-package-audit"
    checks.append(_check("PKG-AUDIT-RECEIPT-SCHEMA", "PASS" if receipt_shape else "FAIL", "package audit receipt has the required shape" if receipt_shape else "package audit receipt is incomplete", missing=sorted(receipt_required - set(receipt))))
    if not receipt_shape:
        return _report(checks)
    checks.append(_schema_check(root, "package-audit-receipt", receipt, "PKG-AUDIT-RECEIPT-JSON-SCHEMA"))

    binding_mismatches = {key: {"expected": value, "actual": receipt.get(key)} for key, value in expected.items() if receipt.get(key) != value}
    if receipt.get("releaseTag") != release_tag:
        binding_mismatches["releaseTag"] = {"expected": release_tag, "actual": receipt.get("releaseTag")}
    if receipt.get("auditTag") != audit_tag:
        binding_mismatches["auditTag"] = {"expected": audit_tag, "actual": receipt.get("auditTag")}
    checks.append(_check("PKG-AUDIT-RECEIPT-BINDING", "PASS" if not binding_mismatches else "FAIL", "receipt binds the current package candidate" if not binding_mismatches else "receipt candidate or manifest bindings drifted", mismatches=binding_mismatches))

    _, audit_tag_object, _ = _git(root, "rev-parse", audit_ref)
    _, bundle_tree, _ = _git(root, "rev-parse", f"{audit_ref}^{{}}")
    _, bundle_type, _ = _git(root, "cat-file", "-t", bundle_tree)
    tree_entries, tree_error = _tree_index(root, bundle_tree) if bundle_type == "tree" else ({}, "audit tag does not target a tree")
    report_entry = tree_entries.get("report.json", {})
    report_blob = report_entry.get("oid", "")
    tag_binding_ok = (
        audit_type == "tag"
        and bundle_type == "tree"
        and tree_error is None
        and receipt.get("auditTagObject") == audit_tag_object
        and receipt.get("auditBundleTree") == bundle_tree
        and report_entry.get("type") == "blob"
        and receipt.get("auditReportBlob") == report_blob
    )
    checks.append(_check(
        "PKG-AUDIT-BUNDLE-TREE",
        "PASS" if tag_binding_ok else "FAIL",
        "audit tag seals a content-addressed report/evidence tree" if tag_binding_ok else "audit tag bundle tree or report binding drifted",
        auditTagObject=audit_tag_object,
        bundleTree=bundle_tree,
        bundleType=bundle_type,
        error=tree_error,
    ))
    if not tag_binding_ok:
        return _report(checks)
    code, report_bytes, error = _git_bytes(root, "cat-file", "blob", report_blob)
    if code != 0:
        checks.append(_check("PKG-AUDIT-REPORT-READ", "FAIL", "audit report blob cannot be read", error=error))
        return _report(checks)
    report_hash_ok = _sha256_bytes(report_bytes) == receipt.get("auditReportSha256")
    checks.append(_check("PKG-AUDIT-REPORT-HASH", "PASS" if report_hash_ok else "FAIL", "audit report bytes match the receipt" if report_hash_ok else "audit report hash drifted"))
    try:
        report = json.loads(report_bytes.decode("utf-8-sig"))
    except json.JSONDecodeError as exc:
        checks.append(_check("PKG-AUDIT-REPORT-JSON", "FAIL", "audit report blob is not valid JSON", line=exc.lineno, column=exc.colno))
        return _report(checks)
    report_required = {
        "schemaVersion", "reportType", "reportId", "version", "candidateCommit",
        "candidateTree", "packageManifestSha256", "runtimeManifestSha256",
        "assuranceMatrixSha256", "auditor", "implementer", "auditedAt",
        "result", "findingCounts", "validatedControlIds",
        "evidenceManifestBlob", "evidenceManifestSha256",
    }
    report_shape = isinstance(report, dict) and report_required.issubset(report) and report.get("schemaVersion") == "3.2" and report.get("reportType") == "vibe-control-package-audit"
    checks.append(_check("PKG-AUDIT-REPORT-SCHEMA", "PASS" if report_shape else "FAIL", "audit report has the required shape" if report_shape else "audit report is incomplete"))
    if not report_shape:
        return _report(checks)
    checks.append(_schema_check(root, "package-audit-report", report, "PKG-AUDIT-REPORT-JSON-SCHEMA"))
    report_mismatches = {key: {"expected": value, "actual": report.get(key)} for key, value in expected.items() if report.get(key) != value}
    for key in (
        "auditor", "implementer", "auditedAt", "result", "findingCounts",
        "validatedControlIds", "evidenceManifestBlob", "evidenceManifestSha256",
    ):
        if receipt.get(key) != report.get(key):
            report_mismatches[key] = {"expected": report.get(key), "actual": receipt.get(key)}
    checks.append(_check("PKG-AUDIT-REPORT-BINDING", "PASS" if not report_mismatches else "FAIL", "audit report binds the exact current candidate and receipt" if not report_mismatches else "audit report bindings drifted", mismatches=report_mismatches))

    auditor = report.get("auditor", {})
    implementer = report.get("implementer", {})
    independent = (
        isinstance(auditor, dict) and isinstance(implementer, dict)
        and isinstance(auditor.get("actorId"), str) and isinstance(auditor.get("sessionId"), str)
        and auditor.get("actorId") != implementer.get("actorId")
        and auditor.get("sessionId") != implementer.get("sessionId")
    )
    checks.append(_check("PKG-AUDIT-INDEPENDENCE", "PASS" if independent else "FAIL", "auditor actor and session are independent from implementation" if independent else "auditor overlaps the implementation actor or session"))
    counts = report.get("findingCounts", {})
    result_ok = report.get("result") == "PASS" and counts.get("P0") == 0 and counts.get("P1") == 0 and receipt.get("enableFormalClaims") is True
    checks.append(_check("PKG-AUDIT-RESULT", "PASS" if result_ok else "FAIL", "audit passed with no P0/P1 and explicitly enables formal claims" if result_ok else "audit result, high-severity counts, or enablement is not eligible", result=report.get("result"), findingCounts=counts))
    controls = report.get("validatedControlIds")
    control_ok = isinstance(controls, list) and set(controls) == set(REQUIRED_PACKAGE_CONTROL_IDS) and len(controls) == len(REQUIRED_PACKAGE_CONTROL_IDS)
    checks.append(_check("PKG-AUDIT-CONTROL-COVERAGE", "PASS" if control_ok else "FAIL", "audit covers the fixed assurance control universe" if control_ok else "audit control coverage is incomplete"))
    checks.extend(_validate_audit_evidence(
        root,
        tree_entries=tree_entries,
        report=report,
        receipt=receipt,
        expected=expected,
    ))
    return _report(checks, receipt=receipt)


def _report(checks: list[dict[str, Any]], *, receipt: dict[str, Any] | None = None) -> dict[str, Any]:
    failing = [item for item in checks if item["status"] in {"FAIL", "BLOCKED"}]
    if any(item["status"] == "FAIL" for item in failing):
        status, readiness = "FAIL", "DIAGNOSTIC"
    elif failing:
        status, readiness = "BLOCKED", "AWAITING_EXTERNAL_VALIDATION"
    else:
        status, readiness = "PASS", "FORMAL_GATE_READY"
    value: dict[str, Any] = {
        "status": status,
        "readiness": readiness,
        "formalClaimsAllowed": not failing,
        "checks": checks,
        "blockers": [item["id"] for item in failing],
    }
    if receipt is not None and not failing:
        value["receipt"] = receipt
    return value


def validate_materialized_receipt(receipt: Any, *, version: str, package_sha: str, runtime_sha: str, matrix_sha: str) -> list[dict[str, Any]]:
    if not isinstance(receipt, dict):
        return [_check("HC-PACKAGE-AUDIT-RECEIPT-SCHEMA", "FAIL", "materialized package audit receipt must be an object")]
    expected = {
        "schemaVersion": "3.2",
        "receiptType": "vibe-control-package-audit",
        "version": version,
        "packageManifestSha256": package_sha,
        "runtimeManifestSha256": runtime_sha,
        "assuranceMatrixSha256": matrix_sha,
        "result": "PASS",
        "enableFormalClaims": True,
    }
    mismatches = {key: {"expected": value, "actual": receipt.get(key)} for key, value in expected.items() if receipt.get(key) != value}
    counts = receipt.get("findingCounts", {})
    if not isinstance(counts, dict) or counts.get("P0") != 0 or counts.get("P1") != 0:
        mismatches["findingCounts"] = {"expected": {"P0": 0, "P1": 0}, "actual": counts}
    controls = receipt.get("validatedControlIds")
    if not isinstance(controls, list) or set(controls) != set(REQUIRED_PACKAGE_CONTROL_IDS) or len(controls) != len(REQUIRED_PACKAGE_CONTROL_IDS):
        mismatches["validatedControlIds"] = {"expected": sorted(REQUIRED_PACKAGE_CONTROL_IDS), "actual": controls}
    for key in ("auditBundleTree", "auditReportBlob", "auditReportSha256", "evidenceManifestBlob", "evidenceManifestSha256"):
        value = receipt.get(key)
        expected_length = 40 if key.endswith(("Tree", "Blob")) else 64
        if (
            not isinstance(value, str)
            or len(value) != expected_length
            or any(character not in "0123456789abcdef" for character in value)
        ):
            mismatches[key] = {"expected": f"{expected_length}-character lowercase hex", "actual": value}
    return [_check("HC-PACKAGE-AUDIT-RECEIPT-BINDING", "PASS" if not mismatches else "INVALIDATED", "materialized package audit receipt binds current governance content" if not mismatches else "materialized package audit receipt drifted", mismatches=mismatches)]
