from __future__ import annotations

import datetime as dt
import fnmatch
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from . import VERSION
from .common import (
    ControlError, canonical_bytes, check, clean_status, envelope, file_ref, git,
    git_root, load_json, now_iso, safe_relative, sha256_bytes, sha256_file,
    verify_dependencies, verify_ref, write_json_atomic,
)
from .crypto import verify_signature
from .checkpoint_control import (
    AUDIT_POLICY, checkpoint_contract_checks, checkpoint_ids_for_case,
    checkpoint_set_sha256, evaluate_case_oracle, finding_structure_checks, normalize_statement,
    owner_checkpoint_checks, positioning_checkpoint_source_checks,
    review_checkpoint_checks, statement_id, validate_statement_objects,
)
from .automation_control import materialize_policy, policy_scope_binding, verify_policy
from .package_release import validate_development_package, validate_materialized_receipt, validate_package_release
from .positioning_control import (
    compile_for_project, compiler_checks, coverage_check, fail_on_compile_issues,
    positioning_summary, rule_compiler_binding, verify_positioning,
)
from .project_rules import canonical_rule_bytes
from .schema import validate_object

PHASES = ["DRAFT", "CONTRACT_LOCKED", "IMPLEMENTING", "CANDIDATE_FROZEN", "VERIFIED", "AUDITED", "ACCEPTED", "RELEASE_READY"]
CLAIMS = ["DIAGNOSTIC", "DEVELOPMENT_CHECKED", "VERIFIED", "ACCEPTED", "RELEASE_READY"]
SCHEMA_VERSION = "3.2"
ADAPTER_TOOL_PROBE_TIMEOUT_SECONDS = 180
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
OBJECTIVE_ID = re.compile(r"\b(?:KO|KF|NG)-[A-Z0-9][A-Z0-9._-]*\b")
DIRECT_BLOCKING_FINDING_CLASSES = {"CURRENT_GOAL_DEFECT", "MINIMUM_CORE_VIOLATION", "SAFETY_OVERRIDE"}
RELEASE_INTENT_CAPS = {
    "LOCAL_EXPERIMENT": "VERIFIED",
    "PRIVATE_OPERATION": "ACCEPTED",
    "EXTERNAL_RELEASE": "RELEASE_READY",
}
R3_KEY_ROLES = {"executor", "auditor", "release-auditor", "owner"}
REQUIRED_ASSURANCE_CONTROL_IDS = frozenset({
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
def required_assurance_control_ids(package_version: str) -> frozenset[str]:
    confirmed_ceiling = {"0.3.4": 29, "0.3.5": 30, "0.3.6": 33}.get(package_version, 36)
    return frozenset(
        item for item in REQUIRED_ASSURANCE_CONTROL_IDS
        if not item.startswith("CTRL-CONFIRMED-") or int(item.rsplit("-", 1)[1]) <= confirmed_ceiling
    )


def paths(project: Path) -> dict[str, Path]:
    root = project.resolve()
    control = root / ".vibe-control"
    return {
        "root": root, "control": control, "lock": control / "project-governance-lock.json",
        "automation_policy": control / "automation-policy.json",
        "state": control / "stage-state.json", "cases": control / "case-catalog.json",
        "key_objectives": control / "key-objectives-lock.json",
        "positioning": control / "project-positioning.json", "rule_inputs": control / "rule-inputs.json",
        "resolved_rules": control / "resolved-rule-set.json", "skill_bindings": control / "skill-bindings",
        "tasks": control / "tasks", "task_locks": control / "task-locks",
        "candidates": control / "candidates", "evidence": control / "evidence",
        "reviews": control / "reviews", "audit_closures": control / "reviews" / "audit-closures",
        "decisions": control / "decisions",
        "external_audits": control / "external-audits",
        "handoffs": control / "handoffs", "runtime": control / "runtime" / VERSION,
        "legacy": control / "legacy", "governance": control / "governance",
        "package_receipt": control / "governance" / "package-audit-receipt.json",
        "objective_revisions": control / "governance" / "objective-revisions",
        "evidence_byte_policy": control / ".gitattributes",
    }


EVIDENCE_BYTE_POLICY = b"evidence/** -text -filter -working-tree-encoding\n"
EVIDENCE_ATTRIBUTE_PROBE = ".vibe-control/evidence/.byte-policy-probe"


def content_ref(root: Path, path: Path) -> dict[str, Any]:
    return {"path": path.resolve().relative_to(root.resolve()).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path), "tracked": True}


def write_evidence_byte_policy(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(EVIDENCE_BYTE_POLICY)


def evidence_byte_policy_check(root: Path, p: dict[str, Path]) -> dict[str, Any]:
    policy_path = p["evidence_byte_policy"]
    relative_policy = policy_path.resolve().relative_to(root.resolve()).as_posix()
    content_ok = policy_path.is_file() and policy_path.read_bytes() == EVIDENCE_BYTE_POLICY
    tracked = bool(git(root, "ls-files", "--error-unmatch", "--", relative_policy, required=False))
    attr_run = subprocess.run(
        ["git", "-C", str(root), "check-attr", "text", "filter", "working-tree-encoding", "--", EVIDENCE_ATTRIBUTE_PROBE],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    attributes: dict[str, str] = {}
    if attr_run.returncode == 0:
        for line in attr_run.stdout.splitlines():
            parts = line.split(": ", 2)
            if len(parts) == 3:
                attributes[parts[1]] = parts[2]
    effective = all(attributes.get(name) == "unset" for name in ("text", "filter", "working-tree-encoding"))
    valid = content_ok and tracked and attr_run.returncode == 0 and effective
    return check(
        "HC-EVIDENCE-GIT-BYTE-POLICY", "PASS" if valid else "BLOCKED",
        "evidence subtree has a tracked byte-preserving Git policy" if valid else "evidence Git byte policy is missing, untracked or ineffective",
        path=relative_policy, contentMatches=content_ok, tracked=tracked, attributes=attributes,
    )


def require_evidence_byte_policy(root: Path, p: dict[str, Path]) -> None:
    result = evidence_byte_policy_check(root, p)
    if result["status"] != "PASS":
        raise ControlError(result["id"], result["message"], status=result["status"], details=result.get("details"))


def git_blob_ref_check(root: Path, ref: dict[str, Any]) -> dict[str, Any]:
    path = safe_relative(root, ref["path"])
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"HEAD:{relative}"],
        capture_output=True,
    )
    blob = result.stdout if result.returncode == 0 else b""
    valid = (
        result.returncode == 0
        and len(blob) == ref.get("bytes")
        and sha256_bytes(blob) == ref.get("sha256")
        and path.is_file()
        and path.read_bytes() == blob
    )
    return check(
        "HC-EVIDENCE-GIT-BYTE-POLICY", "PASS" if valid else "FAIL",
        "working-copy evidence bytes equal the committed Git blob" if valid else "working-copy evidence bytes differ from the committed Git blob",
        path=relative, workingSha256=sha256_file(path) if path.is_file() else None,
        blobSha256=sha256_bytes(blob) if result.returncode == 0 else None,
    )


def case_lifecycle(case: dict[str, Any]) -> str:
    return case.get("lifecycle", "CANDIDATE_EXECUTION")


def assert_candidate_case_lifecycle(cases: list[dict[str, Any]]) -> None:
    invalid = sorted(item.get("id", "unknown") for item in cases if case_lifecycle(item) != "CANDIDATE_EXECUTION")
    if invalid:
        raise ControlError(
            "HC-CASE-LIFECYCLE-SCOPE",
            "bootstrap diagnostic cases cannot enter a candidate task",
            status="BLOCKED", details=invalid,
        )


def require_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SAFE_IDENTIFIER.fullmatch(value):
        raise ControlError("HC-IDENTIFIER-SAFETY", f"unsafe managed identifier for {field}")
    return value


def get_case(catalog: dict[str, Any], case_id: str) -> dict[str, Any]:
    require_identifier(case_id, "caseId")
    for item in catalog["cases"]:
        if item["id"] == case_id:
            return item
    raise ControlError("HC-TASK-CASE-CLOSURE", f"unknown case: {case_id}")


def runtime_root() -> Path:
    return Path(__file__).resolve().parents[1]


def copy_runtime_bundle(destination: Path) -> None:
    """Copy only manifest-bound runtime files, never local caches."""
    source = runtime_root().resolve()
    manifest_path = source / "runtime-manifest.json"
    manifest = load_json(manifest_path)
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list):
        raise ControlError("HC-RUNTIME-MANIFEST-SHAPE", "runtime manifest files must be an array")
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    shutil.copy2(manifest_path, destination / "runtime-manifest.json")
    for index, item in enumerate(files):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ControlError("HC-RUNTIME-MANIFEST-SHAPE", f"runtime manifest file entry {index} is invalid")
        source_file = safe_relative(source, item["path"])
        if not source_file.is_file():
            raise ControlError("HC-RUNTIME-MANIFEST-FILE", f"runtime manifest file is missing: {item['path']}")
        target = destination / Path(item["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target)


def dependency_checks() -> list[dict[str, Any]]:
    return verify_dependencies(runtime_root())


def assert_dependencies() -> None:
    failed = [item for item in dependency_checks() if item["status"] != "PASS"]
    if failed:
        raise ControlError("DEPENDENCY_BLOCKED", "runtime dependencies do not match dependency-lock.json", status="BLOCKED", details=failed)


def inspect(project: Path) -> dict[str, Any]:
    p = paths(project)
    root = git_root(p["root"])
    if p["control"].exists(): _guard_v3_control_plane(p, allow_missing=True)
    data = {
        "project": str(p["root"]), "gitRoot": str(root), "head": git(root, "rev-parse", "HEAD", required=False) or None,
        "dirtyEntries": clean_status(root), "controlPlaneExists": p["control"].is_dir(),
        "runtimeInstalled": p["runtime"].is_dir(), "commands": ["inspect", "resolve-rules", "bootstrap", "reposition", "revise-objectives", "automation", "dashboard", "lock-task", "validate", "freeze", "execute", "ingest", "audit", "accept", "release-check", "handoff", "migrate", "upgrade", "risk"],
    }
    if p["lock"].is_file():
        try:
            lock = load_json(p["lock"])
            ref = lock.get("automationPolicy") if isinstance(lock, dict) else None
            if isinstance(ref, dict) and isinstance(ref.get("path"), str):
                policy = load_json(safe_relative(root, ref["path"]))
                data["automationMode"] = policy.get("mode", "MANUAL_STAGE_CONFIRMATION")
                data["automationPolicySource"] = "LOCKED_POLICY"
            else:
                data["automationMode"] = "MANUAL_STAGE_CONFIRMATION"
                data["automationPolicySource"] = "BACKWARD_COMPATIBLE_DEFAULT"
        except ControlError:
            data["automationMode"] = "UNKNOWN"
            data["automationPolicySource"] = "INVALID_OR_UNREADABLE"
    return envelope(status="PASS", checks=dependency_checks(), data=data)


def initial_state(project_id: str, positioning_id: str | None = None, rule_set_id: str | None = None) -> dict[str, Any]:
    positioning_id = positioning_id or f"positioning-{project_id}-unresolved"
    rule_set_id = rule_set_id or f"rules-{project_id}-unresolved"
    return {"schemaVersion": SCHEMA_VERSION, "projectId": project_id, "positioningId": positioning_id, "ruleSetId": rule_set_id, "phase": "DRAFT", "health": "BLOCKED", "claimLevel": "DIAGNOSTIC", "taskId": None, "candidateId": None, "revision": 0, "phaseHistory": [], "updatedAt": now_iso()}


def transition(p: dict[str, Path], phase: str, health: str, claim: str, reason: str, *, task_id: str | None = None, candidate_id: str | None = None) -> dict[str, Any]:
    state = load_json(p["state"])
    validate_object("stage-state", state)
    old = state["phase"]
    if old != phase:
        if phase != "SUPERSEDED" and (old not in PHASES or phase not in PHASES or PHASES.index(phase) != PHASES.index(old) + 1):
            raise ControlError("HC-PHASE-TRANSITION", f"illegal controller transition {old} -> {phase}")
        state["phaseHistory"].append({"from": old, "to": phase, "at": now_iso(), "reason": reason})
    state.update({"phase": phase, "health": health, "claimLevel": claim, "revision": state["revision"] + 1, "updatedAt": now_iso()})
    if task_id is not None:
        state["taskId"] = task_id
    if candidate_id is not None:
        state["candidateId"] = candidate_id
    validate_object("stage-state", state)
    write_json_atomic(p["state"], state)
    return state


def assert_trusted_key_separation(lock: dict[str, Any]) -> None:
    """A declared role is not independent when it reuses another role's credential/actor."""
    keys = lock.get("trustedKeys", {})
    if not isinstance(keys, dict):
        raise ControlError("HC-ROLE-KEY-SEPARATION", "trustedKeys must be an object")
    by_public: dict[str, tuple[str, str]] = {}
    by_actor: dict[str, tuple[str, str]] = {}
    for key_id, value in keys.items():
        if not isinstance(value, dict):
            raise ControlError("HC-ROLE-KEY-SEPARATION", f"invalid trusted key record: {key_id}")
        role = value.get("role"); public_key = value.get("publicKey"); actor_id = value.get("actorId")
        if not isinstance(role, str) or not isinstance(public_key, str) or not isinstance(actor_id, str):
            raise ControlError("HC-ROLE-KEY-SEPARATION", f"incomplete trusted key record: {key_id}")
        if public_key in by_public and by_public[public_key][0] != role:
            raise ControlError("HC-ROLE-KEY-SEPARATION", f"roles {by_public[public_key][0]} and {role} reuse one public key")
        if actor_id in by_actor and by_actor[actor_id][0] != role:
            raise ControlError("HC-ROLE-ACTOR-SEPARATION", f"roles {by_actor[actor_id][0]} and {role} reuse actor {actor_id}")
        by_public[public_key] = (role, key_id)
        by_actor[actor_id] = (role, key_id)


def release_intent_cap(lock: dict[str, Any]) -> str:
    intent = lock.get("releaseIntent")
    if intent not in RELEASE_INTENT_CAPS:
        raise ControlError("HC-RELEASE-INTENT", f"unknown project release intent: {intent}")
    return RELEASE_INTENT_CAPS[intent]


def requires_external_release_crypto(lock: dict[str, Any], contract: dict[str, Any]) -> bool:
    """Only an actual external R3 release requires the public-key release chain.

    A local experiment or private operation can still contain an R3 task.  That
    task keeps its human authorization, recovery, and role-separation controls,
    but does not turn the local Skill into a private-key management system.
    """
    return (
        lock.get("releaseIntent") == "EXTERNAL_RELEASE"
        and contract.get("risk") == "R3"
        and contract.get("maxClaimLevel") == "RELEASE_READY"
    )


def require_r3_trusted_keys(lock: dict[str, Any]) -> None:
    """R3 cryptographic roles are a project-release control, never a Skill installation prerequisite."""
    roles = {value.get("role") for value in lock.get("trustedKeys", {}).values() if isinstance(value, dict)}
    missing = sorted(R3_KEY_ROLES - roles)
    if missing:
        raise ControlError("HC-R3-TRUSTED-KEYS", "external R3 RELEASE_READY requires distinct externally managed public keys", details={"missingRoles": missing})


def has_signature_fields(value: dict[str, Any]) -> bool:
    return "keyId" in value or "signature" in value


def verify_record_signature(value: dict[str, Any], lock: dict[str, Any], role: str, actor_id: str, *, required: bool, check_id: str) -> bool:
    """Verify external-release signatures; other paths may use a tracked human attestation."""
    present = has_signature_fields(value)
    if not required and not present:
        return False
    if not isinstance(value.get("keyId"), str) or not isinstance(value.get("signature"), dict):
        raise ControlError(check_id, "keyId and signature must be supplied together when a signature is required")
    verify_signature(value, require_key_actor(lock, value["keyId"], role, actor_id), check_id)
    return True


def _guard_v3_control_plane(p: dict[str, Path], *, allow_missing: bool = False) -> None:
    if not p["control"].exists():
        if allow_missing: return
        raise ControlError("VC-CONTROL-PLANE-MISSING", ".vibe-control does not exist", status="BLOCKED")
    governance_lock: dict[str, Any] | None = None
    for path, kind in ((p["lock"], "project-governance-lock"), (p["state"], "stage-state")):
        if path.is_file():
            value = load_json(path)
            if not isinstance(value, dict):
                validate_object(kind, value)
            if value.get("schemaVersion") != SCHEMA_VERSION:
                if value.get("schemaVersion") == "3.1":
                    raise ControlError("VC-MIGRATION-REQUIRED", "Schema 3.1 control planes remain pinned until migrate --plan establishes a recoverable Schema 3.2 conversion", status="BLOCKED")
                raise ControlError("VC-REINSTALL-REQUIRED", "only Schema 3.1 has a supported automatic migration path; older control planes require a fresh bootstrap", status="BLOCKED")
            if kind == "project-governance-lock":
                validate_object(kind, value)
                governance_lock = value
    if governance_lock is not None:
        runtime_ref = governance_lock.get("runtime")
        if not isinstance(runtime_ref, dict) or not isinstance(runtime_ref.get("path"), str):
            raise ControlError("HC-RUNTIME-MANIFEST", "governance lock does not identify a bound runtime manifest")
        manifest_path = safe_relative(p["root"], runtime_ref["path"])
        if manifest_path.name != "runtime-manifest.json":
            raise ControlError("HC-RUNTIME-MANIFEST", "governance runtime reference must identify runtime-manifest.json")
        bound_runtime = manifest_path.parent
        expected_refs = (
            ("runtime", bound_runtime / "runtime-manifest.json", "HC-RUNTIME-MANIFEST"),
            ("ruleCompiler", bound_runtime / "vibe_runtime" / "project_rules.py", "HC-RULESET-BINDING"),
            ("profileDirectory", bound_runtime / "rules" / "v1" / "profiles.json", "HC-RULESET-BINDING"),
            ("adapterDirectory", bound_runtime / "rules" / "v1" / "adapters.json", "HC-ADAPTER-CAPABILITY"),
        )
        for name, expected_path, check_id in expected_refs:
            ref = governance_lock.get(name)
            if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
                raise ControlError(check_id, f"governance lock omits the bound {name} reference")
            if safe_relative(p["root"], ref["path"]) != expected_path.resolve():
                raise ControlError(check_id, f"governance lock {name} reference does not belong to the bound runtime")
            verified = verify_ref(p["root"], ref, check_id)
            if verified["status"] != "PASS":
                raise ControlError(verified["id"], verified["message"], status=verified["status"], details=verified.get("details"))
        package_runtime_ref = governance_lock.get("packageBinding", {}).get("runtimeManifest")
        if package_runtime_ref != runtime_ref:
            raise ControlError("HC-RUNTIME-MANIFEST", "package and governance runtime manifest bindings differ", status="INVALIDATED")
        p["runtime"] = bound_runtime


def _key_objective_id_sets(value: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    return set(value["objectiveIds"]), set(value["failureModeIds"]), set(value["nonGoalIds"])


def _key_objectives_from_spec(root: Path, spec: dict[str, Any]) -> dict[str, Any]:
    source = spec["keyObjectives"]
    if source["document"].replace("\\", "/") != "KEY_OBJECTIVES.md":
        raise ControlError("HC-OBJECTIVES-ROOT", "KEY_OBJECTIVES.md must be a project-root tracked file")
    document_path = safe_relative(root, source["document"])
    document_ref = file_ref(root, document_path)
    text = document_path.read_text(encoding="utf-8-sig")
    observed = set(OBJECTIVE_ID.findall(text))
    expected = set(source["objectiveIds"]) | set(source["failureModeIds"]) | set(source["nonGoalIds"])
    if observed != expected:
        raise ControlError("HC-OBJECTIVES-ID-CLOSURE", "declared objective IDs must exactly match KEY_OBJECTIVES.md", details={"missing": sorted(expected - observed), "undeclared": sorted(observed - expected)})
    confirmation = source["confirmation"]
    summary_hash = sha256_bytes(confirmation["summary"].encode("utf-8"))
    if summary_hash != confirmation["summarySha256"]:
        raise ControlError("HC-OBJECTIVES-CONFIRMATION", "objective confirmation summary hash is invalid")
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "lockId": f"objectives-{source['documentId']}-r{source['revision']}",
        "documentId": source["documentId"], "revision": source["revision"], "status": source["status"],
        "document": document_ref,
        "sourceDocuments": [file_ref(root, safe_relative(root, item)) for item in source["sourceDocuments"]],
        "confirmation": {
            "actorId": confirmation["actorId"], "summary": confirmation["summary"],
            "summarySha256": confirmation["summarySha256"],
            "record": file_ref(root, safe_relative(root, confirmation["record"])),
        },
        "objectiveIds": source["objectiveIds"], "failureModeIds": source["failureModeIds"], "nonGoalIds": source["nonGoalIds"],
        "lockedAt": now_iso(),
    }
    validate_object("key-objectives-lock", value)
    return value


def key_objective_checks(root: Path, value: dict[str, Any]) -> list[dict[str, Any]]:
    validate_object("key-objectives-lock", value)
    checks = [verify_ref(root, value["document"], "HC-OBJECTIVES-DOCUMENT")]
    checks.extend(verify_ref(root, ref, f"HC-OBJECTIVES-SOURCE-{index+1}") for index, ref in enumerate(value["sourceDocuments"]))
    checks.append(verify_ref(root, value["confirmation"]["record"], "HC-OBJECTIVES-CONFIRMATION"))
    try:
        text = safe_relative(root, value["document"]["path"]).read_text(encoding="utf-8-sig")
        observed = set(OBJECTIVE_ID.findall(text))
        expected = set().union(*_key_objective_id_sets(value))
        checks.append(check("HC-OBJECTIVES-ID-CLOSURE", "PASS" if observed == expected else "INVALIDATED", "objective ID set matches the locked document" if observed == expected else "objective IDs drifted", missing=sorted(expected - observed), undeclared=sorted(observed - expected)))
    except (OSError, UnicodeError) as exc:
        checks.append(check("HC-OBJECTIVES-ID-CLOSURE", "INVALIDATED", "KEY_OBJECTIVES.md cannot be read", error=str(exc)))
    return checks


def assert_objective_refs(contract: dict[str, Any], objective_lock: dict[str, Any]) -> None:
    allowed = set(objective_lock["objectiveIds"]) | set(objective_lock["failureModeIds"])
    unknown = sorted(set(contract["objectiveRefs"]) - allowed)
    if unknown:
        raise ControlError("HC-TASK-OBJECTIVE-CLOSURE", "task references unknown or non-actionable objective IDs", details={"unknown": unknown})


def finding_blocks_claim(finding: dict[str, Any], claim: str) -> bool:
    """Compatibility helper: direct blockers only affect explicitly listed claims."""
    return (
        finding.get("status") == "OPEN"
        and claim in finding.get("affectedClaims", [])
        and finding.get("classification") in (DIRECT_BLOCKING_FINDING_CLASSES | {"HUMAN_DECISION"})
    )


def review_finding_checks(
    root: Path, review: dict[str, Any], objective_lock: dict[str, Any], claim: str,
    contract: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if contract is None:
        raise ControlError("HC-FINDING-TASK-SCOPE", "finding admission requires the current task contract")
    checks: list[dict[str, Any]] = []
    for finding in review["findings"]:
        checks.extend(finding_structure_checks(finding, contract, objective_lock, claim))
        for index, ref in enumerate(finding["evidenceRefs"]):
            checks.append(verify_ref(root, ref, f"HC-FINDING-EVIDENCE-{finding['id']}-{index+1}"))
    return checks


def _positioning_from_spec(root: Path, spec: dict[str, Any]) -> dict[str, Any]:
    source = {key: spec[key] for key in (
        "primaryExperience", "capabilityDomains", "deliveryObjective", "releaseIntent",
        "runtimeTargets", "targetEnvironments", "distributionChannels", "humanQualityGates",
        "nonGoals", "firstVerticalSlice",
    )}
    summary_hash = sha256_bytes(canonical_bytes(positioning_summary(source)))
    confirmation = spec["confirmation"]
    record = file_ref(root, safe_relative(root, confirmation["record"]))
    value = {
        "schemaVersion": SCHEMA_VERSION, "positioningId": f"positioning-{spec['projectId']}-{summary_hash[:12]}",
        **source,
        "confirmation": {
            "actorId": confirmation["actorId"], "summary": confirmation["summary"],
            "summarySha256": confirmation["summarySha256"], "record": record,
        },
        "confirmedAt": now_iso(),
    }
    positioning_checkpoint_source_checks(value)
    checks = verify_positioning(root, value)
    fail_on_compile_issues(checks)
    return value


def _adapter_binding(compiled: dict[str, Any], requested: Any) -> dict[str, Any]:
    adapter_id = requested if isinstance(requested, str) else requested.get("id") if isinstance(requested, dict) else None
    adapter_id = adapter_id or "generic-command"
    descriptor = next((item for item in compiled["canonical"]["runtimeAdapters"] if item.get("id") == adapter_id), None)
    if descriptor is None:
        raise ControlError("HC-ADAPTER-CAPABILITY", f"case requests unresolved adapter: {adapter_id}", status="BLOCKED")
    return {"id": adapter_id, "version": descriptor["version"], "sha256": sha256_bytes(canonical_bytes(descriptor))}


def adapter_requires_artifacts(descriptor: dict[str, Any] | None) -> bool:
    return isinstance(descriptor, dict) and descriptor.get("localExecution", {}).get("mode") == "playwright"


def command_invokes_playwright(command: Any) -> bool:
    return _playwright_version_command(command) is not None


def _playwright_version_command(command: Any) -> list[str] | None:
    """Return the exact wrapper invocation for the observed Playwright binary.

    This deliberately accepts only a direct ``playwright test`` command. Paths,
    package scripts and unrelated Playwright subcommands are not proof of a test
    execution boundary.
    """
    if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
        return None
    executable_names = {"playwright", "playwright.cmd", "playwright.exe"}
    non_executing_arguments = {
        "--help", "-h", "--version", "-v", "--list", "--ui", "--ui-host",
        "--pass-with-no-tests",
    }
    package_manager_names = {
        "pnpm": "pnpm", "pnpm.cmd": "pnpm", "pnpm.exe": "pnpm",
        "npm": "npm", "npm.cmd": "npm", "npm.exe": "npm",
        "npx": "npx", "npx.cmd": "npx", "npx.exe": "npx",
        "yarn": "yarn", "yarn.cmd": "yarn", "yarn.exe": "yarn",
        "bunx": "bunx", "bunx.exe": "bunx",
    }
    executable = command[0].lower()
    def is_test(index: int) -> bool:
        tail = [item.lower() for item in command[index + 1 :]]
        return command[index].lower() == "test" and not any(
            item in non_executing_arguments or any(item.startswith(f"{flag}=") for flag in non_executing_arguments if flag.startswith("--"))
            for item in tail
        )

    if executable in executable_names:
        return [command[0], "--version"] if len(command) > 1 and is_test(1) else None
    manager = package_manager_names.get(executable)
    if manager == "pnpm":
        index = 1
        while index < len(command) and command[index].lower() != "exec":
            argument = command[index]
            lowered = argument.lower()
            if argument in {"--filter", "-F"}:
                if index + 1 >= len(command) or not command[index + 1].strip() or command[index + 1].startswith("-"):
                    return None
                index += 2
                continue
            if lowered.startswith("--filter=") and argument.partition("=")[2].strip():
                index += 1
                continue
            return None
        tool_index = index + 1
        test_index = index + 2
        if test_index < len(command) and command[tool_index].lower() in executable_names and is_test(test_index):
            return [*command[: tool_index + 1], "--version"]
        return None
    if manager == "npm":
        index = 2 if len(command) > 2 and command[1].lower() == "exec" else -1
        if index >= 0 and command[index] == "--":
            index += 1
        if index >= 0 and index + 1 < len(command) and command[index].lower() in executable_names and is_test(index + 1):
            return [*command[: index + 1], "--version"]
        return None
    if manager in {"npx", "yarn", "bunx"}:
        if len(command) > 2 and command[1].lower() in executable_names and is_test(2):
            return [command[0], command[1], "--version"]
    return None


def clear_declared_artifacts(execution_root: Path, artifacts: list[dict[str, Any]]) -> None:
    """Remove candidate-carried artifacts so the locked command must recreate them."""
    for requirement in artifacts:
        path = safe_relative(execution_root, requirement["path"])
        if not path.exists():
            continue
        if not path.is_file():
            raise ControlError(
                "HC-ADAPTER-CAPABILITY",
                f"declared artifact is not a regular file before execution: {requirement['path']}",
            )
        path.unlink()


def validate_adapter_case_contract(case_id: str, case: dict[str, Any], descriptor: dict[str, Any]) -> None:
    """Fail closed before execution when a runtime case exceeds its adapter contract."""
    if not adapter_requires_artifacts(descriptor):
        return
    if not case.get("artifacts"):
        raise ControlError("HC-ADAPTER-CAPABILITY", f"Playwright case {case_id} must declare screenshot/report/trace/log artifacts")
    for requirement in case["artifacts"]:
        value = requirement.get("path") if isinstance(requirement, dict) else None
        if not isinstance(value, str) or not value:
            raise ControlError("HC-PATH-SAFETY", f"Playwright case {case_id} has an invalid artifact path")
        normalized_value = value.replace("\\", "/")
        posix_path = PurePosixPath(normalized_value)
        windows_path = PureWindowsPath(value)
        if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive or ".." in posix_path.parts:
            raise ControlError("HC-PATH-SAFETY", f"Playwright case {case_id} has an unsafe artifact path: {value}")
    if not command_invokes_playwright(case.get("command")):
        raise ControlError("HC-ADAPTER-CAPABILITY", f"Playwright case {case_id} must execute a locked Playwright command")


def _artifact_ref_matches_requirement(ref: Any, requirement: dict[str, Any], evidence_id: str) -> bool:
    if not isinstance(ref, dict) or not isinstance(ref.get("path"), str) or not isinstance(ref.get("bytes"), int):
        return False
    declared_path = requirement.get("path")
    if not isinstance(declared_path, str) or not isinstance(requirement.get("minBytes"), int):
        return False
    relative = declared_path.replace("\\", "/")
    normalized = PurePosixPath(relative)
    expected_parts = normalized.parts
    canonical_relative = "/".join(expected_parts)
    expected_path = f".vibe-control/evidence/artifacts/{evidence_id}/{canonical_relative}"
    return (
        bool(expected_parts)
        and ".." not in expected_parts
        and not normalized.is_absolute()
        and not PureWindowsPath(declared_path).drive
        and ref["path"].replace("\\", "/") == expected_path
        and ref["bytes"] >= requirement["minBytes"]
    )


def evidence_adapter_contract_matches(
    evidence: dict[str, Any], case: dict[str, Any], descriptor: dict[str, Any] | None,
    invocation: dict[str, Any] | None,
) -> bool:
    if descriptor is None:
        return False
    declared_capabilities = set(case.get("capabilities", []))
    observed_capabilities = set(evidence.get("capabilitiesObserved", []))
    descriptor_capabilities = set(descriptor.get("provesCaseCapabilities", []))
    requirements = case.get("artifacts", [])
    artifact_refs = evidence.get("artifacts", [])
    executable_resolution = invocation.get("executableResolution") if isinstance(invocation, dict) else None
    invocation_ok = (
        isinstance(invocation, dict)
        and invocation.get("schemaVersion") == SCHEMA_VERSION
        and invocation.get("evidenceId") == evidence.get("evidenceId")
        and invocation.get("candidateCommit") == evidence.get("candidateCommit")
        and invocation.get("caseId") == case.get("id") == evidence.get("caseId")
        and invocation.get("adapter") == case.get("adapter") == evidence.get("adapter")
        and invocation.get("command") == case.get("command") == evidence.get("command")
        and invocation.get("requestedArtifacts") == requirements
        and isinstance(executable_resolution, dict)
        and executable_resolution.get("requestedExecutable") == case.get("command", [None])[0]
        and isinstance(executable_resolution.get("resolvedExecutable"), str)
        and bool(executable_resolution["resolvedExecutable"].strip())
        and Path(executable_resolution["resolvedExecutable"]).is_absolute()
        and isinstance(executable_resolution.get("hostPlatform"), str)
        and bool(executable_resolution["hostPlatform"].strip())
    )
    if evidence.get("observation") == "runtime-observed":
        oracle = invocation.get("oracleObservation") if isinstance(invocation, dict) else None
        runtime_observation = invocation.get("runtimeObservation") if isinstance(invocation, dict) else None
        invocation_ok = invocation_ok and (
            invocation.get("operation") == "execute-locked-case"
            and invocation.get("executionRoot") == "detached-candidate-worktree"
            and isinstance(oracle, dict)
            and oracle.get("expectedExitCode") == case.get("oracle", {}).get("exitCode")
            and oracle.get("observedExitCode") == evidence.get("exitCode")
            and (evidence.get("result") != "PASS" or not any(oracle.get(key) for key in ("missingStdout", "forbiddenStderr", "artifactFailures")))
        )
        if adapter_requires_artifacts(descriptor):
            invocation_ok = invocation_ok and (
                isinstance(invocation.get("toolVersion"), str)
                and bool(invocation["toolVersion"].strip())
                and isinstance(runtime_observation, dict)
                and runtime_observation.get("mode") == "playwright"
                and runtime_observation.get("commandKind") == "playwright-test"
                and runtime_observation.get("artifactProvenance") == "fresh-after-pre-execution-cleanup"
            )
    else:
        invocation_ok = invocation_ok and invocation.get("operation") == evidence.get("operation") and invocation.get("toolVersion") == evidence.get("toolVersion")
    return (
        evidence.get("adapter") == case.get("adapter")
        and case["adapter"].get("version") == descriptor.get("version")
        and case["adapter"].get("sha256") == sha256_bytes(canonical_bytes(descriptor))
        and evidence.get("command") == case.get("command")
        and observed_capabilities == declared_capabilities
        and observed_capabilities.issubset(descriptor_capabilities)
        and len(artifact_refs) == len(requirements)
        and isinstance(evidence.get("evidenceId"), str)
        and all(_artifact_ref_matches_requirement(ref, requirement, evidence["evidenceId"]) for ref, requirement in zip(artifact_refs, requirements))
        and invocation_ok
    )


def evidence_id_binding_matches(evidence_path: Path | None, evidence: dict[str, Any]) -> bool:
    evidence_id = evidence.get("evidenceId")
    invocation_ref = evidence.get("adapterInvocation")
    if not isinstance(evidence_id, str) or not isinstance(invocation_ref, dict):
        return False
    expected_invocation = f".vibe-control/evidence/{evidence_id}.adapter-invocation.json"
    filename_ok = evidence_path is None or evidence_path.name == f"{evidence_id}.json"
    return filename_ok and invocation_ref.get("path", "").replace("\\", "/") == expected_invocation


def _catalog_from_spec(spec: dict[str, Any], compiled: dict[str, Any]) -> dict[str, Any]:
    cases = []
    for raw in spec["cases"]:
        if not isinstance(raw, dict):
            raise ControlError("HC-SCHEMA-CASE_CATALOG", "bootstrap case must be an object")
        item = dict(raw)
        item["adapter"] = _adapter_binding(compiled, raw.get("adapter", raw.get("adapterId", "generic-command")))
        item.pop("adapterId", None)
        if item.get("lifecycle", "CANDIDATE_EXECUTION") not in {"CANDIDATE_EXECUTION", "BOOTSTRAP_DIAGNOSTIC"}:
            raise ControlError("HC-CASE-LIFECYCLE-SCOPE", f"case {item.get('id', 'unknown')} has an unsupported lifecycle")
        descriptor = next(value for value in compiled["canonical"]["runtimeAdapters"] if value.get("id") == item["adapter"]["id"])
        validate_adapter_case_contract(str(item.get("id", "unknown")), item, descriptor)
        item.setdefault("satisfiesRuleIds", [])
        item.setdefault("capabilities", [])
        cases.append(item)
    catalog = {"schemaVersion": SCHEMA_VERSION, "catalogId": spec.get("catalogId", f"{spec['projectId']}-cases"), "cases": cases}
    validate_object("case-catalog", catalog)
    coverage = coverage_check(compiled, cases)
    if coverage["status"] != "PASS":
        raise ControlError(coverage["id"], coverage["message"], details=coverage.get("details"))
    return catalog


def _rule_input_record(spec: dict[str, Any]) -> dict[str, Any]:
    omitted = {"cases", "authorityFiles", "trustedKeys", "keyObjectives", "automationPolicy"}
    return {key: value for key, value in spec.items() if key not in omitted}


def _resolved_rule_object(root: Path, p: dict[str, Path], positioning: dict[str, Any], compiled: dict[str, Any]) -> dict[str, Any]:
    compiler = rule_compiler_binding(p["runtime"])
    value = {
        "schemaVersion": SCHEMA_VERSION, "ruleSetId": f"rules-{compiled['canonicalSha256'][:16]}",
        "positioning": content_ref(root, p["positioning"]),
        "compiler": compiler,
        "canonical": compiled["canonical"], "canonicalSha256": compiled["canonicalSha256"],
        "conflicts": compiled["conflicts"], "warnings": compiled["warnings"], "investigations": compiled["investigations"],
        "installRequests": compiled["installRequests"], "blockers": compiled["blockers"], "canApprove": False, "compiledAt": now_iso(),
    }
    validate_object("resolved-rule-set", value)
    return value


def resolve_rules(project: Path, spec_path: Path) -> dict[str, Any]:
    assert_dependencies(); p = paths(project); root = git_root(p["root"])
    spec = load_json(spec_path.resolve())
    if not isinstance(spec, dict) or "automationPolicy" not in spec:
        raise ControlError("HC-AUTOMATION-POLICY-REQUIRED", "new projects must explicitly select an automation advancement mode")
    validate_object("bootstrap-spec", spec)
    objective_lock = _key_objectives_from_spec(root, spec)
    positioning = _positioning_from_spec(root, spec)
    policy = materialize_policy(root, spec["automationPolicy"], project_id=spec["projectId"], scope_binding=policy_scope_binding(objective_lock, positioning))
    compiled = compile_for_project(spec, root, runtime_root())
    checks = key_objective_checks(root, objective_lock) + positioning_checkpoint_source_checks(positioning) + verify_positioning(root, positioning) + compiler_checks(compiled)
    try:
        catalog = _catalog_from_spec(spec, compiled)
        checks.append(coverage_check(compiled, catalog["cases"]))
    except ControlError as exc:
        checks.append(check(exc.check_id, exc.status, exc.message, details=exc.details))
    priorities = {"PASS": 0, "BLOCKED": 1, "INVALIDATED": 2, "FAIL": 3}
    status = max((item["status"] for item in checks), key=lambda item: priorities[item])
    return envelope(status=status, checks=checks, data={
        "positioning": positioning_summary(positioning), "ruleSet": {"canonicalSha256": compiled["canonicalSha256"], "rules": compiled["canonical"]["layers"]},
        "positioningSummary": positioning_summary(positioning), "positioningId": positioning["positioningId"],
        "canonicalRuleSetSha256": compiled["canonicalSha256"], "profiles": compiled["canonical"]["capabilityProfiles"],
        "adapters": compiled["canonical"]["runtimeAdapters"], "skillBindings": compiled["canonical"]["skillBindings"],
        "warnings": compiled["warnings"], "investigations": compiled["investigations"], "installRequests": compiled["installRequests"],
        "keyObjectives": {"documentId": objective_lock["documentId"], "revision": objective_lock["revision"], "objectiveIds": objective_lock["objectiveIds"], "failureModeIds": objective_lock["failureModeIds"]},
        "automation": {"policyId": policy["policyId"], "mode": policy["mode"]},
    })


def bootstrap(project: Path, spec_path: Path) -> dict[str, Any]:
    assert_dependencies(); p = paths(project); root = git_root(p["root"])
    if p["control"].exists():
        _guard_v3_control_plane(p, allow_missing=True)
        raise ControlError("VC-BOOTSTRAP-EXISTS", ".vibe-control already exists")
    if clean_status(root):
        raise ControlError("HC-WORKTREE-CLEAN", "bootstrap requires a clean worktree", status="BLOCKED")
    skill_root = runtime_root().parents[2]
    package_manifest = load_json(skill_root / "package-manifest.json")
    if package_manifest.get("maturity") == "DEVELOPMENT_DIAGNOSTIC":
        package_release = validate_development_package(skill_root)
        package_mode = "DEVELOPMENT"
        if package_release.get("status") != "PASS":
            raise ControlError("HC-DEVELOPMENT-PACKAGE-INTEGRITY", "the installed development Skill package does not have a valid scoped installation identity and content closure", status="BLOCKED" if package_release.get("status") == "BLOCKED" else "FAIL", details={"readiness": package_release.get("readiness"), "blockers": package_release.get("blockers", [])})
    else:
        package_release = validate_package_release(skill_root)
        package_mode = "SEALED"
        if package_release.get("formalClaimsAllowed") is not True:
            raise ControlError("HC-PACKAGE-AUDIT-CLOSURE", "the installed sealed Skill package is not bound to an exact independently audited release candidate", status="BLOCKED" if package_release.get("status") == "BLOCKED" else "FAIL", details={"readiness": package_release.get("readiness"), "blockers": package_release.get("blockers", [])})
    spec = load_json(spec_path.resolve())
    if not isinstance(spec, dict) or "automationPolicy" not in spec:
        raise ControlError("HC-AUTOMATION-POLICY-REQUIRED", "new projects must explicitly select an automation advancement mode")
    validate_object("bootstrap-spec", spec)
    objective_lock = _key_objectives_from_spec(root, spec)
    positioning = _positioning_from_spec(root, spec)
    automation_policy = materialize_policy(root, spec["automationPolicy"], project_id=spec["projectId"], scope_binding=policy_scope_binding(objective_lock, positioning))
    compiled = compile_for_project(spec, root, runtime_root())
    compile_checks = compiler_checks(compiled); fail_on_compile_issues(compile_checks)
    catalog = _catalog_from_spec(spec, compiled)
    p["control"].mkdir(parents=True)
    write_evidence_byte_policy(p["evidence_byte_policy"])
    copy_runtime_bundle(p["runtime"])
    p["governance"].mkdir(parents=True)
    shutil.copy2(skill_root / "package-manifest.json", p["governance"] / "package-manifest.json")
    shutil.copy2(skill_root / "references" / "controller-assurance-matrix.json", p["governance"] / "controller-assurance-matrix.json")
    if package_mode == "SEALED":
        write_json_atomic(p["package_receipt"], package_release["receipt"])
    write_json_atomic(p["key_objectives"], objective_lock)
    write_json_atomic(p["automation_policy"], automation_policy)
    write_json_atomic(p["positioning"], positioning)
    write_json_atomic(p["rule_inputs"], _rule_input_record(spec))
    resolved = _resolved_rule_object(root, p, positioning, compiled); write_json_atomic(p["resolved_rules"], resolved)
    write_json_atomic(p["cases"], catalog)
    binding_refs = []
    for binding in compiled["canonical"]["skillBindings"]:
        binding_id = require_identifier(binding["skillId"], "skillId")
        normalized = {
            "schemaVersion": SCHEMA_VERSION, "skillId": binding_id, "version": binding["version"], "treeSha256": binding["treeSha256"],
            "requirement": binding["requirement"], "role": binding["role"],
            "triggerConditions": binding["triggerConditions"], "writePermissions": binding["writePermissions"], "canApprove": False, "path": binding["path"],
        }
        validate_object("skill-binding", normalized)
        binding_path = p["skill_bindings"] / f"{binding_id}.json"; write_json_atomic(binding_path, normalized); binding_refs.append(content_ref(root, binding_path))
    authorities = [file_ref(root, safe_relative(root, value)) for value in spec["authorityFiles"]]
    key_ids = [item.get("keyId") for item in spec["trustedKeys"] if isinstance(item, dict)]
    if len(key_ids) != len(spec["trustedKeys"]) or len(set(key_ids)) != len(key_ids) or any(not isinstance(value, str) or not value for value in key_ids):
        raise ControlError("HC-ROLE-KEY-IDENTITY", "bootstrap trustedKeys must have unique nonempty keyId values")
    trusted = {item["keyId"]: {"actorId": item["actorId"], "role": item["role"], "publicKey": item["publicKey"]} for item in spec["trustedKeys"]}
    binding = package_release["binding"] if package_mode == "DEVELOPMENT" else {
        "version": package_release["receipt"]["version"], "commit": package_release["receipt"]["candidateCommit"], "tree": package_release["receipt"]["candidateTree"],
    }
    package_binding = {
        "version": binding["version"],
        "packageManifest": content_ref(root, p["governance"] / "package-manifest.json"),
        "runtimeManifest": content_ref(root, p["runtime"] / "runtime-manifest.json"),
        "assuranceMatrix": content_ref(root, p["governance"] / "controller-assurance-matrix.json"),
    }
    if package_mode == "DEVELOPMENT":
        package_binding["sourceKind"] = binding["sourceKind"]
    if "commit" in binding and "tree" in binding:
        package_binding.update({"commit": binding["commit"], "tree": binding["tree"]})
    lock = {
        "schemaVersion": SCHEMA_VERSION, "lockId": f"lock-{spec['projectId']}-v32", "projectId": spec["projectId"],
        "packageMode": package_mode, "packageBinding": package_binding,
        "skill": content_ref(root, p["governance"] / "package-manifest.json"), "runtime": content_ref(root, p["runtime"] / "runtime-manifest.json"),
        "keyObjectives": content_ref(root, p["key_objectives"]), "automationPolicy": content_ref(root, p["automation_policy"]), "caseCatalog": content_ref(root, p["cases"]),
        "evidenceBytePolicy": content_ref(root, p["evidence_byte_policy"]),
        "authorityFiles": authorities, "ruleInputs": content_ref(root, p["rule_inputs"]), "positioning": content_ref(root, p["positioning"]),
        "resolvedRuleSet": content_ref(root, p["resolved_rules"]), "ruleCompiler": content_ref(root, p["runtime"] / "vibe_runtime" / "project_rules.py"),
        "profileDirectory": content_ref(root, p["runtime"] / "rules" / "v1" / "profiles.json"),
        "adapterDirectory": content_ref(root, p["runtime"] / "rules" / "v1" / "adapters.json"), "skillBindings": binding_refs,
        "releaseIntent": positioning["releaseIntent"], "trustedKeys": trusted, "lockedAt": now_iso(),
    }
    if package_mode == "SEALED":
        lock["packageAuditReceipt"] = content_ref(root, p["package_receipt"])
    validate_object("project-governance-lock", lock); assert_trusted_key_separation(lock); write_json_atomic(p["lock"], lock)
    write_json_atomic(p["state"], initial_state(spec["projectId"], positioning["positioningId"], resolved["ruleSetId"]))
    package_cap = "DEVELOPMENT_CHECKED" if package_mode == "DEVELOPMENT" else RELEASE_INTENT_CAPS[positioning["releaseIntent"]]
    return envelope(status="BLOCKED", checks=[*key_objective_checks(root, objective_lock), *positioning_checkpoint_source_checks(positioning), *verify_positioning(root, positioning), *compile_checks, check("HC-AUTOMATION-POLICY", "PASS", "explicit automation advancement mode is content-bound"), check("HC-RULE-CASE-COVERAGE", "PASS", "fixed cases cover every applicable rule"), check("VC-BOOTSTRAP-COMMIT-REQUIRED", "BLOCKED", "commit generated Schema 3.2 control files before lock-task")], data={"created": str(p["control"]), "packageMode": package_mode, "releaseIntent": positioning["releaseIntent"], "automationMode": automation_policy["mode"], "maxClaimLevel": package_cap, "positioningId": positioning["positioningId"], "ruleSetId": resolved["ruleSetId"], "warnings": compiled["warnings"], "investigations": compiled["investigations"], "next": "commit-and-lock-task"})


def lock_task(project: Path, contract_path: Path) -> dict[str, Any]:
    assert_dependencies()
    p = paths(project); _guard_v3_control_plane(p); root = git_root(p["root"])
    if clean_status(root):
        raise ControlError("HC-WORKTREE-CLEAN", "lock-task requires a clean worktree", status="BLOCKED")
    contract_path = contract_path.resolve(); contract = load_json(contract_path)
    validate_object("task-contract", contract)
    require_identifier(contract["taskId"], "taskId")
    lock = load_json(p["lock"]); validate_object("project-governance-lock", lock)
    automation_ref = lock.get("automationPolicy")
    if automation_ref is not None:
        result = verify_ref(root, automation_ref, "HC-AUTOMATION-POLICY-DRIFT")
        if result["status"] != "PASS":
            raise ControlError(result["id"], result["message"], status=result["status"])
        automation_policy = load_json(safe_relative(root, automation_ref["path"]))
        verify_policy(root, automation_policy, project_id=lock["projectId"], expected_scope_binding=policy_scope_binding(load_json(p["key_objectives"]), load_json(p["positioning"])))
    objective_lock = load_json(p["key_objectives"]); validate_object("key-objectives-lock", objective_lock)
    objective_checks = key_objective_checks(root, objective_lock)
    if any(item["status"] != "PASS" for item in objective_checks):
        first = next(item for item in objective_checks if item["status"] != "PASS")
        raise ControlError(first["id"], first["message"], status=first["status"])
    assert_objective_refs(contract, objective_lock)
    positioning = load_json(p["positioning"]); validate_object("project-positioning", positioning)
    source_checks = positioning_checkpoint_source_checks(positioning)
    resolved = load_json(p["resolved_rules"]); validate_object("resolved-rule-set", resolved)
    rule_inputs = load_json(p["rule_inputs"])
    positioning_checks = verify_positioning(root, positioning)
    compiled = compile_for_project(rule_inputs, root, p["runtime"], expected_runtime_manifest_sha256=lock["runtime"]["sha256"])
    rule_checks = compiler_checks(compiled)
    binding_ok = resolved["canonicalSha256"] == compiled["canonicalSha256"] and resolved["compiler"] == rule_compiler_binding(p["runtime"])
    rule_checks.append(check("HC-RULESET-BINDING", "PASS" if binding_ok else "INVALIDATED", "resolved rule set matches a fresh compilation" if binding_ok else "rule inputs or runtime catalogs drifted"))
    fail_on_compile_issues([*positioning_checks, *rule_checks])
    intent_cap = release_intent_cap(lock)
    if CLAIMS.index(contract["maxClaimLevel"]) > CLAIMS.index(intent_cap):
        raise ControlError(
            "HC-RELEASE-INTENT-CEILING",
            f"task max claim {contract['maxClaimLevel']} exceeds project release intent {lock['releaseIntent']}",
        )
    if lock["releaseIntent"] == "EXTERNAL_RELEASE" and contract["maxClaimLevel"] == "RELEASE_READY" and contract["risk"] != "R3":
        raise ControlError("HC-EXTERNAL-RELEASE-R3", "EXTERNAL_RELEASE requires an R3 task before RELEASE_READY")
    if requires_external_release_crypto(lock, contract):
        require_r3_trusted_keys(lock)
    catalog = load_json(p["cases"]); validate_object("case-catalog", catalog)
    known = {item["id"] for item in catalog["cases"]}
    missing = sorted(set(contract["requiredCaseIds"]) - known)
    if missing:
        raise ControlError("HC-TASK-CASE-CLOSURE", "task references unknown cases", details=missing)
    selected_cases = [get_case(catalog, case_id) for case_id in contract["requiredCaseIds"]]
    assert_candidate_case_lifecycle(selected_cases)
    checkpoint_checks = checkpoint_contract_checks(contract, positioning, catalog, intent_cap)
    task_coverage = coverage_check(compiled, selected_cases)
    if task_coverage["status"] != "PASS":
        raise ControlError(task_coverage["id"], task_coverage["message"], details=task_coverage.get("details"))
    authorities = [file_ref(root, safe_relative(root, value)) for value in contract["authorityRefs"]]
    checkpoint_confirmation_ref = file_ref(root, safe_relative(root, contract["checkpointConfirmation"]["record"]))
    checkpoint_hash = checkpoint_set_sha256(contract)
    head = git(root, "rev-parse", "HEAD")
    applicable_rule_ids = sorted(item["id"] for item in compiled["canonical"]["layers"])
    required_capabilities = sorted({capability for item in compiled["canonical"]["layers"] for capability in item["rule"].get("caseCapabilities", [])})
    task_lock = {
        "schemaVersion": SCHEMA_VERSION, "taskId": contract["taskId"], "contract": file_ref(root, contract_path),
        "governanceLock": file_ref(root, p["lock"]), "caseCatalog": file_ref(root, p["cases"]),
        "keyObjectives": file_ref(root, p["key_objectives"]),
        "positioning": file_ref(root, p["positioning"]), "resolvedRuleSet": file_ref(root, p["resolved_rules"]),
        "authorityBindings": authorities, "applicableRuleIds": applicable_rule_ids, "requiredCaseCapabilities": required_capabilities,
        "checkpointSetSha256": checkpoint_hash, "checkpointConfirmation": checkpoint_confirmation_ref,
        "baselineCommit": head, "baselineTree": git(root, "show", "-s", "--format=%T", head), "lockedAt": now_iso(),
    }
    if automation_ref is not None:
        task_lock["automationPolicy"] = automation_ref
    validate_object("task-lock", task_lock)
    output = p["task_locks"] / f"{contract['taskId']}.json"; write_json_atomic(output, task_lock)
    state = transition(p, "CONTRACT_LOCKED", "CLEAR", "DEVELOPMENT_CHECKED", "task contract locked", task_id=contract["taskId"])
    return envelope(status="PASS", checks=[*objective_checks, *source_checks, *positioning_checks, *rule_checks, *checkpoint_checks, task_coverage, check("HC-TASK-OBJECTIVE-CLOSURE", "PASS", "task references current actionable objectives"), check("HC-TASK-LOCK", "PASS", "task inputs, objectives, positioning, checkpoints and rules locked")], state={"declared": state, "derived": state}, data={"taskLock": str(output), "objectiveRefs": contract["objectiveRefs"], "checkpointSetSha256": checkpoint_hash, "packageMode": lock["packageMode"], "releaseIntent": lock["releaseIntent"], "releaseIntentMaxClaimLevel": intent_cap, "applicableRuleIds": applicable_rule_ids, "requiredCaseCapabilities": required_capabilities})


def path_matches(path: str, pattern: str) -> bool:
    normalized = path.replace("\\", "/")
    pattern = pattern.replace("\\", "/")
    return fnmatch.fnmatchcase(normalized, pattern) or (pattern.endswith("/**") and normalized.startswith(pattern[:-3].rstrip("/") + "/"))


def objective_path_changes(paths: list[str]) -> list[str]:
    return [value for value in paths if value.replace("\\", "/").lower() == "key_objectives.md"]


def freeze(project: Path, actor_id: str, session_id: str, contract_path: Path | None = None) -> dict[str, Any]:
    assert_dependencies()
    p = paths(project); _guard_v3_control_plane(p); root = git_root(p["root"]); state = load_json(p["state"])
    if clean_status(root):
        raise ControlError("HC-WORKTREE-CLEAN", "freeze requires a clean worktree", status="BLOCKED")
    task_id = state.get("taskId")
    if not task_id:
        raise ControlError("HC-TASK-LOCK-REQUIRED", "freeze requires lock-task", status="BLOCKED")
    require_identifier(task_id, "taskId")
    task_lock_path = p["task_locks"] / f"{task_id}.json"; task_lock = load_json(task_lock_path); validate_object("task-lock", task_lock)
    locked_contract_path = safe_relative(root, task_lock["contract"]["path"])
    if contract_path and contract_path.resolve() != locked_contract_path:
        raise ControlError("HC-CANDIDATE-CONTRACT-IDENTITY", "requested contract differs from task lock")
    if sha256_file(locked_contract_path) != task_lock["contract"]["sha256"]:
        raise ControlError("HC-CANDIDATE-CONTRACT-IDENTITY", "contract drifted after lock", status="INVALIDATED")
    contract = load_json(locked_contract_path); validate_object("task-contract", contract)
    if contract["risk"] == "R0":
        raise ControlError("HC-R0-NO-CANDIDATE", "R0 is read-only and cannot freeze a candidate", status="BLOCKED")
    head = git(root, "rev-parse", "HEAD"); changed = [x for x in git(root, "diff", "--name-only", f"{task_lock['baselineCommit']}..{head}").splitlines() if x]
    product = [x for x in changed if not x.startswith(".vibe-control/")]
    objective_changes = objective_path_changes(product)
    if objective_changes:
        raise ControlError("HC-OBJECTIVES-WRITE-BOUNDARY", "ordinary tasks cannot modify KEY_OBJECTIVES.md; use revise-objectives", details={"paths": objective_changes})
    forbidden = [x for x in product if any(path_matches(x, pat) for pat in contract["forbiddenPaths"])]
    outside = [x for x in product if not any(path_matches(x, pat) for pat in contract["allowedPaths"])]
    if forbidden or outside:
        raise ControlError("HC-FREEZE-PATH-ENVELOPE", "Git diff escapes the task path envelope", details={"forbidden": forbidden, "outsideAllowed": outside})
    if task_lock["checkpointSetSha256"] != checkpoint_set_sha256(contract):
        raise ControlError("HC-CHECKPOINT-HASH", "checkpoint set drifted after task lock", status="INVALIDATED")
    bindings = [task_lock["contract"], task_lock["governanceLock"], task_lock["keyObjectives"], task_lock["caseCatalog"], task_lock["positioning"], task_lock["resolvedRuleSet"], task_lock["checkpointConfirmation"], *task_lock["authorityBindings"]]
    if "automationPolicy" in task_lock:
        bindings.append(task_lock["automationPolicy"])
    for ref in bindings:
        result = verify_ref(root, ref, "HC-CANDIDATE-INPUT-BINDING")
        if result["status"] != "PASS":
            raise ControlError(result["id"], result["message"], status=result["status"])
    candidate = {
        "schemaVersion": SCHEMA_VERSION, "candidateId": f"candidate-{task_id}-{head[:12]}", "taskId": task_id,
        "taskLock": file_ref(root, task_lock_path), "commit": head, "tree": git(root, "show", "-s", "--format=%T", head),
        "keyObjectives": task_lock["keyObjectives"], "requirementSources": load_json(p["key_objectives"])["sourceDocuments"],
        "positioning": task_lock["positioning"], "resolvedRuleSet": task_lock["resolvedRuleSet"],
        "checkpointSetSha256": task_lock["checkpointSetSha256"],
        "implementer": {"actorId": actor_id, "sessionId": session_id}, "changedPaths": product, "inputBindings": bindings, "frozenAt": now_iso(),
    }
    if "automationPolicy" in task_lock:
        candidate["automationPolicy"] = task_lock["automationPolicy"]
    validate_object("candidate-manifest", candidate)
    output = p["candidates"] / f"{candidate['candidateId']}.json"; write_json_atomic(output, candidate)
    # CONTRACT_LOCKED -> IMPLEMENTING is materialized here if implementation was committed without a controller command.
    if state["phase"] == "CONTRACT_LOCKED":
        transition(p, "IMPLEMENTING", "CLEAR", "DEVELOPMENT_CHECKED", "implementation commit detected", task_id=task_id)
    new_state = transition(p, "CANDIDATE_FROZEN", "CLEAR", "DEVELOPMENT_CHECKED", "candidate frozen", task_id=task_id, candidate_id=candidate["candidateId"])
    return envelope(status="PASS", checks=[check("HC-FREEZE-PATH-ENVELOPE", "PASS", "real Git diff is inside the task envelope")], state={"declared": new_state, "derived": new_state}, data={"candidate": candidate, "path": str(output)})


def current_objects(p: dict[str, Path]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    _guard_v3_control_plane(p)
    state = load_json(p["state"]); validate_object("stage-state", state)
    lock = load_json(p["lock"]); validate_object("project-governance-lock", lock); assert_trusted_key_separation(lock)
    objective_lock = load_json(p["key_objectives"]); validate_object("key-objectives-lock", objective_lock)
    objective_checks = key_objective_checks(p["root"], objective_lock)
    if any(item["status"] != "PASS" for item in objective_checks):
        first = next(item for item in objective_checks if item["status"] != "PASS")
        raise ControlError(first["id"], first["message"], status=first["status"])
    catalog = load_json(p["cases"]); validate_object("case-catalog", catalog)
    if not state.get("taskId"):
        raise ControlError("HC-TASK-LOCK-REQUIRED", "no active task", status="BLOCKED")
    require_identifier(state["taskId"], "taskId")
    task_lock = load_json(p["task_locks"] / f"{state['taskId']}.json"); validate_object("task-lock", task_lock)
    automation_ref = lock.get("automationPolicy")
    if task_lock.get("automationPolicy") != automation_ref:
        raise ControlError("HC-AUTOMATION-POLICY-DRIFT", "task automation binding differs from the current governance policy", status="INVALIDATED")
    if automation_ref is not None:
        result = verify_ref(p["root"], automation_ref, "HC-AUTOMATION-POLICY-DRIFT")
        if result["status"] != "PASS":
            raise ControlError(result["id"], result["message"], status=result["status"])
        verify_policy(p["root"], load_json(safe_relative(p["root"], automation_ref["path"])), project_id=lock["projectId"], expected_scope_binding=policy_scope_binding(load_json(p["key_objectives"]), load_json(p["positioning"])))
    contract = load_json(safe_relative(p["root"], task_lock["contract"]["path"])); validate_object("task-contract", contract)
    assert_candidate_case_lifecycle([get_case(catalog, case_id) for case_id in contract["requiredCaseIds"]])
    assert_objective_refs(contract, objective_lock)
    positioning = load_json(p["positioning"]); validate_object("project-positioning", positioning)
    positioning_checkpoint_source_checks(positioning)
    checkpoint_contract_checks(contract, positioning, catalog, release_intent_cap(lock))
    if task_lock["checkpointSetSha256"] != checkpoint_set_sha256(contract):
        raise ControlError("HC-CHECKPOINT-HASH", "task-lock checkpoint hash differs from the current contract", status="INVALIDATED")
    return state, lock, catalog, task_lock, contract


def candidate_for(p: dict[str, Path], state: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    if not state.get("candidateId"):
        raise ControlError("HC-CANDIDATE-REQUIRED", "candidate is required", status="BLOCKED")
    require_identifier(state["candidateId"], "candidateId")
    path = p["candidates"] / f"{state['candidateId']}.json"; value = load_json(path); validate_object("candidate-manifest", value)
    require_identifier(value["candidateId"], "candidateId"); require_identifier(value["taskId"], "taskId")
    return path, value


def _execution_worktree_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    command = ["git"]
    if os.name == "nt":
        # This is deliberately process-local. The controller never changes global Git settings.
        command.extend(["-c", "core.longpaths=true"])
    command.extend(["-C", str(root), "worktree", *args])
    return subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _execution_temp_base() -> Path:
    if os.name != "nt":
        return Path(tempfile.gettempdir()).resolve()
    drive = os.environ.get("SystemDrive") or Path.cwd().drive or "C:"
    preferred = Path(f"{drive}\\vce")
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        return preferred.resolve()
    except OSError:
        fallback = Path(tempfile.gettempdir()).resolve() / "vce"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback.resolve()


def _extended_windows_path(path: Path) -> str:
    resolved = str(path.resolve())
    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved.lstrip("\\")
    return "\\\\?\\" + resolved


def _remove_tree_longpath(path: Path) -> None:
    native_path = _extended_windows_path(path)
    if not os.path.exists(native_path):
        return

    def make_writable(function: Any, target: str, _error: Any) -> None:
        try:
            os.chmod(target, 0o700)
            function(target)
        except OSError:
            return

    shutil.rmtree(native_path, onerror=make_writable)


def _path_exists_long(path: Path) -> bool:
    return os.path.exists(_extended_windows_path(path))


def create_execution_worktree(root: Path, candidate_commit: str) -> tuple[Path, Path]:
    """Execute local cases from the immutable candidate tree, never the caller's worktree."""
    try:
        parent = Path(tempfile.mkdtemp(prefix="e-", dir=str(_execution_temp_base())))
    except OSError as exc:
        raise ControlError(
            "HC-EXECUTION-WORKTREE", f"unable to allocate candidate execution root: {exc}", status="BLOCKED",
        ) from exc
    worktree = parent / "candidate"
    result = _execution_worktree_git(root, "add", "--detach", str(worktree), candidate_commit)
    if result.returncode:
        cleanup_details = None
        try:
            remove_execution_worktree(root, parent, worktree)
        except ControlError as cleanup_error:
            cleanup_details = {
                "id": cleanup_error.check_id, "status": cleanup_error.status,
                "message": cleanup_error.message, "details": cleanup_error.details,
            }
        raise ControlError(
            "HC-EXECUTION-WORKTREE",
            result.stderr.strip() or "unable to create immutable candidate execution worktree",
            status="BLOCKED", details={"cleanup": cleanup_details} if cleanup_details else None,
        )
    return parent, worktree


def resolve_executable(command: list[str], execution_root: Path) -> tuple[list[str], dict[str, str]]:
    if not command or not all(isinstance(item, str) and item for item in command):
        raise ControlError("HC-EXECUTABLE-RESOLUTION", "locked command must be a nonempty string array", status="BLOCKED")
    requested = command[0]
    requested_path = Path(requested)
    if requested_path.is_absolute():
        resolved = shutil.which(str(requested_path)) or (str(requested_path) if requested_path.is_file() else None)
    elif any(separator in requested for separator in ("/", "\\")):
        candidate = (execution_root / requested_path).resolve()
        resolved = shutil.which(str(candidate)) or (str(candidate) if candidate.is_file() else None)
    else:
        resolved = shutil.which(requested)
    if not resolved:
        raise ControlError(
            "HC-EXECUTABLE-RESOLUTION", f"locked executable cannot be resolved: {requested}",
            status="BLOCKED", details={"requestedExecutable": requested, "hostPlatform": platform.platform()},
        )
    return [str(Path(resolved).resolve()), *command[1:]], {
        "requestedExecutable": requested,
        "resolvedExecutable": str(Path(resolved).resolve()),
        "hostPlatform": platform.platform(),
    }


def run_locked_command(command: list[str], execution_root: Path, *, timeout: int | None = None) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    resolved_command, resolution = resolve_executable(command, execution_root)
    try:
        result = subprocess.run(
            resolved_command, cwd=execution_root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ControlError(
            "HC-EXECUTION-TIMEOUT", f"locked executable exceeded its timeout: {resolution['requestedExecutable']}",
            status="BLOCKED", details={**resolution, "timeoutSeconds": timeout},
        ) from exc
    except (FileNotFoundError, OSError) as exc:
        raise ControlError(
            "HC-EXECUTABLE-RESOLUTION", f"resolved executable could not be started: {resolution['resolvedExecutable']}",
            status="BLOCKED", details={**resolution, "error": str(exc)},
        ) from exc
    return result, resolution


def run_adapter_tool_probe(command: list[str], execution_root: Path) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    """Observe the locked adapter tool with a bounded cold-materialization budget."""
    try:
        return run_locked_command(command, execution_root, timeout=ADAPTER_TOOL_PROBE_TIMEOUT_SECONDS)
    except ControlError as exc:
        if exc.check_id != "HC-EXECUTION-TIMEOUT":
            raise
        raise ControlError(
            "HC-ADAPTER-TOOL-PROBE",
            f"adapter tool-version probe exceeded {ADAPTER_TOOL_PROBE_TIMEOUT_SECONDS} seconds",
            status="BLOCKED",
            details={
                "command": command,
                "timeoutSeconds": ADAPTER_TOOL_PROBE_TIMEOUT_SECONDS,
                "cause": exc.details,
            },
        ) from exc


def remove_execution_worktree(root: Path, parent: Path, worktree: Path) -> None:
    result = _execution_worktree_git(root, "remove", "--force", str(worktree))
    cleanup_errors: list[str] = []
    if result.returncode:
        cleanup_errors.append(result.stderr.strip() or "Git could not remove the candidate worktree")
    try:
        _remove_tree_longpath(worktree)
        prune = _execution_worktree_git(root, "prune", "--expire", "now")
        if prune.returncode:
            cleanup_errors.append(prune.stderr.strip() or "Git could not prune candidate worktree metadata")
        _remove_tree_longpath(parent)
    except OSError as exc:
        cleanup_errors.append(str(exc))
    registered = _execution_worktree_git(root, "list", "--porcelain")
    if registered.returncode:
        cleanup_errors.append(registered.stderr.strip() or "Git worktree registration could not be verified")
    normalized = os.path.normcase(str(worktree.absolute()))
    still_registered = any(
        line.startswith("worktree ") and os.path.normcase(str(Path(line[9:]).absolute())) == normalized
        for line in registered.stdout.splitlines()
    )
    residue = _path_exists_long(worktree) or _path_exists_long(parent) or still_registered or registered.returncode != 0
    if residue:
        cleanup_errors.append("candidate execution worktree or its Git registration remains after cleanup")
    if cleanup_errors and residue:
        raise ControlError(
            "HC-EXECUTION-WORKTREE-CLEANUP", "; ".join(dict.fromkeys(cleanup_errors)), status="BLOCKED",
        )


def attach_execution_cleanup_error(primary_error: BaseException, cleanup_error: ControlError) -> None:
    if isinstance(primary_error, ControlError):
        if isinstance(primary_error.details, dict):
            details = dict(primary_error.details)
        elif primary_error.details is None:
            details = {}
        else:
            details = {"primaryDetails": primary_error.details}
        details["executionWorktreeCleanup"] = {
            "id": cleanup_error.check_id, "status": cleanup_error.status,
            "message": cleanup_error.message, "details": cleanup_error.details,
        }
        primary_error.details = details
    elif hasattr(primary_error, "add_note"):
        primary_error.add_note(
            f"candidate cleanup also failed [{cleanup_error.check_id}]: {cleanup_error.message}"
        )


def execution_result(outputs: list[str], results: list[str], cleanup_error: ControlError | None) -> dict[str, Any]:
    status = "PASS" if results and all(value == "PASS" for value in results) else "FAIL"
    if cleanup_error is not None and status == "PASS":
        raise cleanup_error
    checks = [check("HC-EXECUTE", status, "controller executed requested cases with conserved results")]
    if cleanup_error is not None:
        checks.append(check(cleanup_error.check_id, cleanup_error.status, cleanup_error.message, details=cleanup_error.details))
    return envelope(status=status, checks=checks, data={"evidence": outputs, "next": "commit evidence and validate"})


def execute(project: Path, actor_id: str, session_id: str, case_ids: list[str] | None) -> dict[str, Any]:
    assert_dependencies()
    p = paths(project); root = git_root(p["root"]); state, _, catalog, _, contract = current_objects(p); _, candidate = candidate_for(p, state)
    require_evidence_byte_policy(root, p)
    resolved = load_json(p["resolved_rules"]); validate_object("resolved-rule-set", resolved)
    selected = case_ids or contract["requiredCaseIds"]
    unknown = sorted(set(selected) - set(contract["requiredCaseIds"]))
    if unknown:
        raise ControlError("HC-TASK-CASE-CLOSURE", "execute cannot expand beyond the task's locked cases", details=unknown)
    outputs = []; results = []
    parent, execution_root = create_execution_worktree(root, candidate["commit"])
    primary_error: BaseException | None = None
    cleanup_error: ControlError | None = None
    try:
        for case_id in selected:
            case = get_case(catalog, case_id)
            assert_candidate_case_lifecycle([case])
            if case["observation"] != "runtime-observed":
                raise ControlError("HC-CASE-OBSERVATION-ELIGIBILITY", f"case {case_id} requires external ingest")
            adapter = case["adapter"]
            descriptor = next((item for item in resolved["canonical"]["runtimeAdapters"] if item.get("id") == adapter["id"]), None)
            if descriptor is None or adapter["version"] != descriptor.get("version") or adapter["sha256"] != sha256_bytes(canonical_bytes(descriptor)):
                raise ControlError("HC-ADAPTER-CAPABILITY", f"case {case_id} adapter binding drifted", status="INVALIDATED")
            validate_adapter_case_contract(case_id, case, descriptor)
            evidence_id = f"evidence-{case_id}-{uuid.uuid4().hex[:12]}"
            tool_version = None
            if adapter["id"] == "godot-runtime":
                if not (execution_root / "project.godot").is_file() or "godot" not in Path(case["command"][0]).name.lower():
                    raise ControlError("HC-ADAPTER-CAPABILITY", f"Godot case {case_id} must bind project.godot and a Godot executable")
                version_run, _ = run_adapter_tool_probe([case["command"][0], "--version"], execution_root)
                tool_version = (version_run.stdout or version_run.stderr).strip()
                if version_run.returncode or not tool_version:
                    raise ControlError("HC-ADAPTER-CAPABILITY", f"Godot executable/version cannot be observed for {case_id}", status="BLOCKED")
            if adapter_requires_artifacts(descriptor):
                version_command = _playwright_version_command(case["command"])
                if version_command is None:
                    raise ControlError("HC-ADAPTER-CAPABILITY", f"Playwright case {case_id} must execute a locked Playwright test command")
                version_run, _ = run_adapter_tool_probe(version_command, execution_root)
                tool_version = (version_run.stdout or version_run.stderr).strip()
                if version_run.returncode or not tool_version:
                    raise ControlError("HC-ADAPTER-CAPABILITY", f"Playwright executable/version cannot be observed for {case_id}", status="BLOCKED")
                clear_declared_artifacts(execution_root, case.get("artifacts", []))
            started = now_iso()
            run, executable_resolution = run_locked_command(case["command"], execution_root)
            finished = now_iso()
            transcript_path = p["evidence"] / f"{evidence_id}.transcript.txt"
            transcript_path.parent.mkdir(parents=True, exist_ok=True)
            transcript_path.write_text(f"CANDIDATE_COMMIT={candidate['commit']}\nCOMMAND={json.dumps(case['command'])}\nEXIT={run.returncode}\nSTDOUT\n{run.stdout}\nSTDERR\n{run.stderr}", encoding="utf-8", newline="\n")
            artifact_refs = []
            artifact_sizes: dict[str, int | None] = {}
            for artifact in case.get("artifacts", []):
                relative = artifact["path"]
                source = safe_relative(execution_root, relative)
                artifact_sizes[relative] = source.stat().st_size if source.is_file() else None
                if artifact_sizes[relative] is None or artifact_sizes[relative] < artifact["minBytes"]:
                    continue
                target = p["evidence"] / "artifacts" / evidence_id / Path(relative)
                target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target); artifact_refs.append(content_ref(root, target))
            passed, oracle_observation = evaluate_case_oracle(
                case, exit_code=run.returncode, stdout=run.stdout, stderr=run.stderr,
                artifact_sizes=artifact_sizes,
            )
            invocation_path = p["evidence"] / f"{evidence_id}.adapter-invocation.json"
            invocation = {
                "schemaVersion": SCHEMA_VERSION, "evidenceId": evidence_id, "candidateCommit": candidate["commit"], "caseId": case_id,
                "adapter": adapter, "command": case["command"], "operation": "execute-locked-case",
                "executableResolution": executable_resolution,
                "requestedArtifacts": case.get("artifacts", []), "executionRoot": "detached-candidate-worktree", "toolVersion": tool_version,
                "oracleObservation": oracle_observation,
            }
            if adapter_requires_artifacts(descriptor):
                invocation["runtimeObservation"] = {
                    "mode": "playwright", "commandKind": "playwright-test",
                    "hostPlatform": platform.platform(),
                    "artifactProvenance": "fresh-after-pre-execution-cleanup",
                    "environmentBoundary": "browser, viewport and WebGL details are covered only when recorded by locked artifacts",
                }
            validate_object("adapter-invocation", invocation)
            write_json_atomic(invocation_path, invocation)
            evidence = {
                "schemaVersion": SCHEMA_VERSION, "evidenceId": evidence_id, "taskId": contract["taskId"], "candidateId": candidate["candidateId"], "candidateCommit": candidate["commit"],
                "positioning": candidate["positioning"], "resolvedRuleSet": candidate["resolvedRuleSet"],
                "caseId": case_id, "caseHash": sha256_bytes(canonical_bytes(case)), "oracleHash": sha256_bytes(canonical_bytes(case["oracle"])),
                "inputHash": sha256_bytes(canonical_bytes(candidate["inputBindings"])), "checkpointSetSha256": candidate["checkpointSetSha256"],
                "checkpointIds": checkpoint_ids_for_case(contract, case_id), "executor": {"actorId": actor_id, "sessionId": session_id},
                "observation": "runtime-observed", "adapter": adapter, "capabilitiesObserved": case["capabilities"],
                "adapterInvocation": content_ref(root, invocation_path), "command": case["command"], "startedAt": started, "finishedAt": finished, "exitCode": run.returncode,
                "counters": {"executed": 1, "passed": 1 if passed else 0, "failed": 0 if passed else 1, "skipped": 0},
                "transcript": content_ref(p["root"], transcript_path), "artifacts": artifact_refs, "result": "PASS" if passed else "FAIL",
            }
            if tool_version is not None: evidence["toolVersion"] = tool_version
            validate_object("execution-evidence", evidence); output = p["evidence"] / f"{evidence_id}.json"; write_json_atomic(output, evidence); outputs.append(str(output)); results.append(evidence["result"])
    except BaseException as exc:
        primary_error = exc
    finally:
        try:
            remove_execution_worktree(root, parent, execution_root)
        except ControlError as exc:
            cleanup_error = exc
    if primary_error is not None:
        if cleanup_error is not None:
            attach_execution_cleanup_error(primary_error, cleanup_error)
        raise primary_error.with_traceback(primary_error.__traceback__)
    return execution_result(outputs, results, cleanup_error)


def trusted_key(lock: dict[str, Any], key_id: str, role: str) -> str:
    key = lock.get("trustedKeys", {}).get(key_id)
    if not key or key.get("role") != role:
        raise ControlError(f"HC-{role.upper()}-KEY", f"untrusted {role} key: {key_id}")
    return key["publicKey"]


def require_key_actor(lock: dict[str, Any], key_id: str, role: str, actor_id: str) -> str:
    key = lock.get("trustedKeys", {}).get(key_id)
    if not key or key.get("role") != role or key.get("actorId") != actor_id:
        raise ControlError(f"HC-{role.upper()}-KEY", f"{role} key is not bound to actor {actor_id}")
    return key["publicKey"]


def ingest(project: Path, attestation_path: Path) -> dict[str, Any]:
    assert_dependencies(); p = paths(project); state, lock, catalog, _, contract = current_objects(p); _, candidate = candidate_for(p, state)
    root = git_root(p["root"])
    require_evidence_byte_policy(root, p)
    resolved = load_json(p["resolved_rules"]); validate_object("resolved-rule-set", resolved)
    attestation = load_json(attestation_path.resolve()); validate_object("external-evidence-attestation", attestation)
    evidence = attestation["evidence"]; validate_object("execution-evidence", evidence)
    require_identifier(evidence["evidenceId"], "evidenceId")
    signature_required = requires_external_release_crypto(lock, contract)
    if signature_required:
        if not isinstance(attestation.get("keyId"), str) or not isinstance(attestation.get("signature"), dict):
            raise ControlError("HC-EXECUTOR-SIGNATURE", "external R3 release evidence requires keyId and signature")
        verify_signature(attestation, require_key_actor(lock, attestation["keyId"], "executor", evidence["executor"]["actorId"]), "HC-EXECUTOR-SIGNATURE")
    elif has_signature_fields(attestation):
        if not isinstance(attestation.get("keyId"), str) or not isinstance(attestation.get("signature"), dict):
            raise ControlError("HC-EXECUTOR-SIGNATURE", "keyId and signature must be supplied together when an optional signature is present")
        verify_signature(attestation, require_key_actor(lock, attestation["keyId"], "executor", evidence["executor"]["actorId"]), "HC-EXECUTOR-SIGNATURE")
    case = get_case(catalog, evidence["caseId"])
    assert_candidate_case_lifecycle([case])
    if case["observation"] != "blackbox-observed" or evidence["observation"] != "blackbox-observed":
        raise ControlError("HC-CASE-OBSERVATION-ELIGIBILITY", "external evidence must cover a blackbox-observed case")
    if evidence["taskId"] != contract["taskId"] or evidence["candidateId"] != candidate["candidateId"] or evidence["candidateCommit"] != candidate["commit"] or ref_key(evidence["positioning"]) != ref_key(candidate["positioning"]) or ref_key(evidence["resolvedRuleSet"]) != ref_key(candidate["resolvedRuleSet"]):
        raise ControlError("HC-CASE-PROVENANCE", "external evidence identity does not match current candidate")
    if not evidence.get("externalTranscript") or not evidence.get("toolVersion") or not evidence.get("operation"):
        raise ControlError("HC-ADAPTER-CAPABILITY", "external/MCP evidence requires tool version, operation and raw external transcript")
    if not evidence_id_binding_matches(None, evidence):
        raise ControlError("HC-EVIDENCE-ID-BINDING", "evidence ID, invocation path and evidence filename namespace must match")
    descriptor = next((item for item in resolved["canonical"]["runtimeAdapters"] if item.get("id") == case["adapter"]["id"]), None)
    if descriptor is None:
        raise ControlError("HC-ADAPTER-CAPABILITY", f"case {case['id']} adapter descriptor is unresolved")
    validate_adapter_case_contract(case["id"], case, descriptor)
    invocation_result = verify_ref(root, evidence["adapterInvocation"], "HC-ADAPTER-INVOCATION")
    if invocation_result["status"] != "PASS":
        raise ControlError(invocation_result["id"], invocation_result["message"], status=invocation_result["status"])
    invocation = load_json(safe_relative(root, evidence["adapterInvocation"]["path"])); validate_object("adapter-invocation", invocation)
    if not evidence_adapter_contract_matches(evidence, case, descriptor, invocation):
        raise ControlError("HC-ADAPTER-CAPABILITY", "external evidence does not bind the locked adapter, invocation, case and artifacts")
    for index, ref in enumerate([evidence["externalTranscript"], evidence["transcript"], *evidence["artifacts"]], start=1):
        result = verify_ref(root, ref, f"HC-INGEST-CONTENT-{index}")
        if result["status"] != "PASS":
            raise ControlError(result["id"], result["message"], status=result["status"])
    output = p["evidence"] / f"{evidence['evidenceId']}.json"; write_json_atomic(output, evidence)
    sig_output = p["evidence"] / f"{evidence['evidenceId']}.attestation.json"; write_json_atomic(sig_output, attestation)
    check_id = "HC-EXECUTOR-SIGNATURE" if signature_required or has_signature_fields(attestation) else "HC-EXTERNAL-EVIDENCE-ATTESTATION"
    message = "trusted external executor signature verified" if check_id == "HC-EXECUTOR-SIGNATURE" else "candidate-bound external evidence attestation recorded for human review"
    return envelope(status="PASS", checks=[check(check_id, "PASS", message)], data={"evidence": str(output), "attestation": str(sig_output)})


def _current_audit_closure(p: dict[str, Path], contract: dict[str, Any], candidate: dict[str, Any]) -> tuple[Path, dict[str, Any] | None]:
    path = p["audit_closures"] / f"{require_identifier(candidate['candidateId'], 'candidateId')}.json"
    if not path.is_file():
        return path, None
    value = load_json(path); validate_object("audit-closure", value)
    bound = (
        value["taskId"] == contract["taskId"]
        and value["candidateId"] == candidate["candidateId"]
        and value["candidateCommit"] == candidate["commit"]
        and value["checkpointSetSha256"] == candidate["checkpointSetSha256"]
    )
    if not bound:
        raise ControlError("HC-AUDIT-STOP-CLOSURE", "audit closure record does not bind the current candidate", status="INVALIDATED")
    return path, value


def audit(project: Path, review_path: Path) -> dict[str, Any]:
    assert_dependencies(); p = paths(project); state, lock, _, _, contract = current_objects(p); _, candidate = candidate_for(p, state)
    if lock["packageMode"] == "DEVELOPMENT":
        raise ControlError("HC-DEVELOPMENT-PACKAGE-CLAIM-CAP", "development package cannot advance a project to AUDITED/VERIFIED", status="BLOCKED")
    if state["phase"] != "VERIFIED":
        raise ControlError("HC-AUDIT-PHASE", "audit requires VERIFIED state", status="BLOCKED")
    review = load_json(review_path.resolve()); validate_object("review-attestation", review)
    require_identifier(review["reviewId"], "reviewId")
    if (review["taskId"], review["candidateId"], review["candidateCommit"]) != (contract["taskId"], candidate["candidateId"], candidate["commit"]) or review["checkpointSetSha256"] != candidate["checkpointSetSha256"] or ref_key(review["keyObjectives"]) != ref_key(candidate["keyObjectives"]) or ref_key(review["positioning"]) != ref_key(candidate["positioning"]) or ref_key(review["resolvedRuleSet"]) != ref_key(candidate["resolvedRuleSet"]):
        raise ControlError("HC-REVIEW-CANDIDATE-BINDING", "review does not bind current candidate")
    closure_path, closure = _current_audit_closure(p, contract, candidate)
    if closure is not None:
        raise ControlError(
            "HC-AUDIT-STOP-CLOSURE",
            "exploratory audit is already closed for this candidate; create a new candidate or task before reopening it",
            status="BLOCKED", details={"auditClosure": closure_path.relative_to(p["root"]).as_posix()},
        )
    evidence_records = [(load_json(path), path) for path in sorted(p["evidence"].glob("*.json")) if not path.name.endswith(("attestation.json", "adapter-invocation.json"))]
    evidence = [value for value, _ in evidence_records]
    expected_evidence = {
        value["caseId"]: (value, path)
        for value, path in evidence_records
        if value.get("taskId") == contract["taskId"] and value.get("candidateId") == candidate["candidateId"] and value.get("candidateCommit") == candidate["commit"]
    }
    expected_ids = {value["evidenceId"] for value, _ in expected_evidence.values()}
    expected_refs = [content_ref(p["root"], path) for _, path in expected_evidence.values()]
    if set(review["evidenceIds"]) != expected_ids or not refs_equal(review["evidenceRefs"], expected_refs):
        raise ControlError("HC-REVIEW-EVIDENCE-BINDING", "review must bind the exact current candidate evidence files")
    checkpoint_checks = review_checkpoint_checks(review, contract, {case_id: value for case_id, (value, _) in expected_evidence.items()})
    if any(item["status"] != "PASS" for item in checkpoint_checks):
        first = next(item for item in checkpoint_checks if item["status"] != "PASS")
        if first["id"] == "HC-AUDIT-EXPLORATION-BUDGET":
            exploratory = [
                item for item in review["findings"]
                if item["classification"] in {"PROCESS_WARNING", "INVESTIGATION", "FUTURE_PROPOSAL", "OUT_OF_SCOPE"}
            ]
            closure = {
                "schemaVersion": SCHEMA_VERSION,
                "closureId": f"audit-closure-{candidate['candidateId']}",
                "taskId": contract["taskId"], "candidateId": candidate["candidateId"],
                "candidateCommit": candidate["commit"], "checkpointSetSha256": candidate["checkpointSetSha256"],
                "reason": "EXPLORATION_BUDGET_EXHAUSTED", "reviewId": review["reviewId"],
                "reviewSha256": sha256_file(review_path.resolve()),
                "findingIds": [item["id"] for item in exploratory], "closedAt": now_iso(),
            }
            validate_object("audit-closure", closure); write_json_atomic(closure_path, closure)
            raise ControlError(
                first["id"], first["message"], status=first["status"],
                details={"auditClosure": closure_path.relative_to(p["root"]).as_posix(), "count": len(exploratory), "maximum": contract["auditPolicy"]["maxExploratoryFindings"]},
            )
        raise ControlError(first["id"], first["message"], status=first["status"], details=first.get("details"))
    if review["result"] != "PASS":
        raise ControlError("HC-REVIEW-RESULT", "a review with result=FAIL cannot advance the candidate")
    for index, ref in enumerate(review["evidenceRefs"]):
        result = verify_ref(p["root"], ref, f"HC-REVIEW-EVIDENCE-REF-{index+1}")
        if result["status"] != "PASS":
            raise ControlError(result["id"], result["message"], status=result["status"])
    transcript_result = verify_ref(p["root"], review["transcript"], "HC-REVIEW-TRANSCRIPT")
    if transcript_result["status"] != "PASS":
        raise ControlError(transcript_result["id"], transcript_result["message"], status=transcript_result["status"])
    executors = {(item["executor"]["actorId"], item["executor"]["sessionId"]) for item in evidence}
    auditor = (review["auditor"]["actorId"], review["auditor"]["sessionId"])
    implementer = (candidate["implementer"]["actorId"], candidate["implementer"]["sessionId"])
    if auditor in executors or auditor == implementer or review["auditor"]["actorId"] == candidate["implementer"]["actorId"]:
        raise ControlError("HC-REVIEW-INDEPENDENCE", "auditor must be distinct from implementer and executors")
    objective_lock = load_json(p["key_objectives"])
    finding_checks = review_finding_checks(p["root"], review, objective_lock, "VERIFIED", contract)
    if any(item["status"] != "PASS" for item in finding_checks):
        first = next(item for item in finding_checks if item["status"] != "PASS")
        raise ControlError(first["id"], first["message"], status=first["status"], details=first.get("details"))
    verify_record_signature(
        review, lock, "auditor", review["auditor"]["actorId"],
        required=requires_external_release_crypto(lock, contract), check_id="HC-AUDITOR-SIGNATURE",
    )
    output = p["reviews"] / f"{review['reviewId']}.json"; write_json_atomic(output, review)
    new_state = transition(p, "AUDITED", "CLEAR", "VERIFIED", "independent review accepted")
    return envelope(status="PASS", checks=[*checkpoint_checks, *finding_checks, check("HC-REVIEW-REQUIRED", "PASS", "candidate-bound independent review accepted")], state={"declared": new_state, "derived": new_state}, data={"review": str(output)})


def accept(project: Path, decision_path: Path) -> dict[str, Any]:
    assert_dependencies(); p = paths(project); state, lock, _, _, contract = current_objects(p); _, candidate = candidate_for(p, state)
    if lock["packageMode"] == "DEVELOPMENT":
        raise ControlError("HC-DEVELOPMENT-PACKAGE-CLAIM-CAP", "development package cannot advance a project to ACCEPTED", status="BLOCKED")
    if state["phase"] != "AUDITED":
        raise ControlError("HC-ACCEPT-PHASE", "accept requires AUDITED state", status="BLOCKED")
    review_paths = sorted(p["reviews"].glob("*.json"))
    if not review_paths:
        raise ControlError("HC-REVIEW-REQUIRED", "accept requires the current candidate review", status="BLOCKED")
    review = load_json(review_paths[-1]); validate_object("review-attestation", review)
    finding_checks = review_finding_checks(p["root"], review, load_json(p["key_objectives"]), "ACCEPTED", contract)
    if any(item["status"] != "PASS" for item in finding_checks):
        first = next(item for item in finding_checks if item["status"] != "PASS")
        raise ControlError(first["id"], first["message"], status=first["status"], details=first.get("details"))
    decision_source = decision_path.resolve()
    decision = load_json(decision_source); validate_object("approval-signature", decision)
    require_identifier(decision["decisionId"], "decisionId")
    if (decision["taskId"], decision["candidateId"], decision["candidateCommit"]) != (contract["taskId"], candidate["candidateId"], candidate["commit"]) or decision["checkpointSetSha256"] != candidate["checkpointSetSha256"] or ref_key(decision["positioning"]) != ref_key(candidate["positioning"]) or ref_key(decision["resolvedRuleSet"]) != ref_key(candidate["resolvedRuleSet"]):
        raise ControlError("HC-DECISION-CANDIDATE-BINDING", "decision does not bind current candidate")
    checkpoint_decision_checks = owner_checkpoint_checks(decision, contract)
    if any(item["status"] != "PASS" for item in checkpoint_decision_checks):
        first = next(item for item in checkpoint_decision_checks if item["status"] != "PASS")
        raise ControlError(first["id"], first["message"], status=first["status"], details=first.get("details"))
    if sorted(decision["scope"]) != sorted(candidate["changedPaths"]):
        raise ControlError("HC-DECISION-SCOPE", "decision scope differs from candidate changes")
    if decision.get("expiresAt") and dt.datetime.fromisoformat(decision["expiresAt"]) <= dt.datetime.now(dt.timezone.utc).astimezone():
        raise ControlError("HC-DECISION-EXPIRED", "approval has expired", status="INVALIDATED")
    if CLAIMS.index("ACCEPTED") > CLAIMS.index(release_intent_cap(lock)):
        raise ControlError("HC-RELEASE-INTENT-CEILING", "project release intent does not permit ACCEPTED", status="BLOCKED")
    verify_record_signature(
        decision, lock, "owner", decision["owner"]["actorId"],
        required=requires_external_release_crypto(lock, contract), check_id="HC-OWNER-SIGNATURE",
    )
    output = p["decisions"] / f"{decision['decisionId']}.json"
    # On case-insensitive filesystems a managed input such as decisions/d.json
    # and the canonical decisions/D.json name can identify the same file. Keep
    # the caller's existing path spelling in that case so the atomic replace
    # cannot silently change the directory entry's case while Git still tracks
    # the old spelling.
    if (
        decision_source.parent.exists()
        and p["decisions"].exists()
        and os.path.samefile(decision_source.parent, p["decisions"])
        and output.exists()
        and decision_source.exists()
        and os.path.samefile(output, decision_source)
    ):
        output = decision_source
    write_json_atomic(output, decision)
    new_state = transition(p, "ACCEPTED", "CLEAR", "ACCEPTED", "owner decision accepted")
    return envelope(status="PASS", checks=[*finding_checks, *checkpoint_decision_checks, check("HC-DECISION-REQUIRED", "PASS", "candidate-bound owner decision accepted")], state={"declared": new_state, "derived": new_state}, data={"decision": str(output), "humanBoundary": "non-external-release owner authenticity is an explicit human responsibility"})


def phase_history_checks(state: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []; previous = "DRAFT"; last_time: dt.datetime | None = None; continuous = True; monotonic = True
    for item in state["phaseHistory"]:
        if item["from"] != previous or item["from"] not in PHASES or item["to"] not in PHASES or PHASES.index(item["to"]) != PHASES.index(item["from"]) + 1:
            continuous = False
        previous = item["to"]
        try: current = dt.datetime.fromisoformat(item["at"])
        except ValueError: monotonic = False; continue
        if last_time and current < last_time: monotonic = False
        last_time = current
    checks.append(check("HC-PHASE-HISTORY-CONTINUITY", "PASS" if continuous else "FAIL", "phase history is continuous" if continuous else "phase history is discontinuous"))
    tail_ok = previous == state["phase"] if state["phaseHistory"] else state["phase"] == "DRAFT"
    checks.append(check("HC-PHASE-HISTORY-TAIL", "PASS" if tail_ok else "FAIL", "phase history tail matches state" if tail_ok else "phase history tail differs from state"))
    checks.append(check("HC-PHASE-HISTORY-MONOTONIC", "PASS" if monotonic else "FAIL", "phase timestamps are monotonic" if monotonic else "phase timestamps are not monotonic"))
    return checks


def ref_key(value: dict[str, Any]) -> tuple[str, int, str, bool]:
    return (value.get("path", ""), value.get("bytes", -1), value.get("sha256", ""), value.get("tracked", False))


def refs_equal(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    return {ref_key(item) for item in left} == {ref_key(item) for item in right} and len(left) == len(right)


def summary_check(checks: list[dict[str, Any]]) -> dict[str, Any]:
    priorities = {"PASS": 0, "BLOCKED": 1, "INVALIDATED": 2, "FAIL": 3}
    status = max((item["status"] for item in checks), key=lambda value: priorities[value]) if checks else "PASS"
    return check("HC-RELEASE-RECEIPT", status, "signed release receipt is fully candidate-bound" if status == "PASS" else "release receipt chain is incomplete or invalid")


def receipt_checks(
    p: dict[str, Path], lock: dict[str, Any], contract: dict[str, Any], candidate_path: Path | None,
    candidate: dict[str, Any] | None, review_path: Path | None, valid_review: dict[str, Any] | None,
    decision_path: Path | None, valid_decision: dict[str, Any] | None, evidence_refs: list[dict[str, Any]],
    executors: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if not candidate_path or not candidate:
        checks.append(check("HC-RELEASE-RECEIPT-CANDIDATE", "BLOCKED", "a frozen candidate is required before a release receipt can qualify"))
        checks.append(summary_check(checks)); return checks
    path = p["runtime"] / "release-receipt.json"
    if not path.is_file():
        checks.append(check("HC-RELEASE-RECEIPT", "BLOCKED", "signed release receipt is missing")); return checks
    try:
        receipt_ref = content_ref(p["root"], path)
        receipt_tracked = verify_ref(p["root"], receipt_ref, "HC-RELEASE-RECEIPT-TRACKED")
        checks.append(receipt_tracked)
        if receipt_tracked["status"] != "PASS":
            checks.append(summary_check(checks)); return checks
        receipt = load_json(path); validate_object("release-receipt", receipt)
        verify_signature(receipt, require_key_actor(lock, receipt["keyId"], "owner", receipt["owner"]["actorId"]), "HC-RELEASE-RECEIPT-SIGNATURE")
        checks.append(check("HC-RELEASE-RECEIPT-SIGNATURE", "PASS", "receipt signature binds the configured owner"))
        expected = {
            "packageManifestSha256": sha256_file(p["governance"] / "package-manifest.json"),
            "runtimeManifestSha256": sha256_file(p["runtime"] / "runtime-manifest.json"),
            "assuranceMatrixSha256": sha256_file(p["governance"] / "controller-assurance-matrix.json"),
        }
        mismatch = {key: {"expected": value, "actual": receipt.get(key)} for key, value in expected.items() if receipt.get(key) != value}
        checks.append(check("HC-RELEASE-RECEIPT-MANIFESTS", "PASS" if not mismatch else "INVALIDATED", "receipt binds current package/runtime/matrix manifests" if not mismatch else "receipt manifest bindings drifted", mismatch=mismatch))
        expected_candidate_ref = content_ref(p["root"], candidate_path)
        candidate_bound = (
            receipt["taskId"] == contract["taskId"] and receipt["candidateId"] == candidate["candidateId"]
            and receipt["candidateCommit"] == candidate["commit"] and receipt["candidateTree"] == candidate["tree"]
            and ref_key(receipt["candidate"]) == ref_key(expected_candidate_ref)
            and ref_key(receipt["positioning"]) == ref_key(candidate["positioning"])
            and ref_key(receipt["resolvedRuleSet"]) == ref_key(candidate["resolvedRuleSet"])
        )
        checks.append(check("HC-RELEASE-RECEIPT-CANDIDATE", "PASS" if candidate_bound else "INVALIDATED", "receipt is bound to the current candidate" if candidate_bound else "receipt cannot be replayed for this candidate"))
        if not valid_decision or not decision_path:
            checks.append(check("HC-RELEASE-RECEIPT-DECISION", "BLOCKED", "a valid owner decision is required before receipt qualification"))
        else:
            expected_decision_ref = content_ref(p["root"], decision_path)
            decision_bound = ref_key(receipt["decision"]) == ref_key(expected_decision_ref)
            checks.append(check("HC-RELEASE-RECEIPT-DECISION", "PASS" if decision_bound else "INVALIDATED", "receipt binds the current owner decision" if decision_bound else "receipt decision binding drifted"))
        audit_ref_result = verify_ref(p["root"], receipt["auditReport"], "HC-RELEASE-AUDIT-REPORT")
        checks.append(audit_ref_result)
        if audit_ref_result["status"] == "PASS":
            audit_path = safe_relative(p["root"], receipt["auditReport"]["path"])
            audit_path_ok = audit_path.relative_to(p["root"]).as_posix().startswith(".vibe-control/external-audits/")
            checks.append(check("HC-RELEASE-AUDIT-PATH", "PASS" if audit_path_ok else "FAIL", "audit report is stored in the managed external-audits directory" if audit_path_ok else "audit report must be stored under .vibe-control/external-audits/"))
            report = load_json(audit_path); validate_object("external-release-audit", report)
            verify_signature(report, require_key_actor(lock, report["keyId"], "release-auditor", report["auditor"]["actorId"]), "HC-RELEASE-AUDIT-SIGNATURE")
            checks.append(check("HC-RELEASE-AUDIT-SIGNATURE", "PASS", "external release audit has a trusted release-auditor signature"))
            report_identity = (
                report["taskId"] == contract["taskId"] and report["candidateId"] == candidate["candidateId"]
                and report["candidateCommit"] == candidate["commit"] and report["candidateTree"] == candidate["tree"]
                and ref_key(report["candidate"]) == ref_key(expected_candidate_ref)
                and ref_key(report["keyObjectives"]) == ref_key(candidate["keyObjectives"])
                and ref_key(report["positioning"]) == ref_key(candidate["positioning"])
                and ref_key(report["resolvedRuleSet"]) == ref_key(candidate["resolvedRuleSet"])
            )
            checks.append(check("HC-RELEASE-AUDIT-CANDIDATE", "PASS" if report_identity else "INVALIDATED", "external audit binds the current candidate" if report_identity else "external audit candidate binding drifted"))
            if not valid_review or not review_path:
                checks.append(check("HC-RELEASE-AUDIT-REVIEW", "BLOCKED", "a valid signed review is required before external audit qualification"))
            else:
                expected_review_ref = content_ref(p["root"], review_path)
                review_bound = ref_key(report["review"]) == ref_key(expected_review_ref)
                evidence_bound = refs_equal(report["evidenceRefs"], evidence_refs)
                review_independent = report["auditor"]["actorId"] != valid_review["auditor"]["actorId"] and report["auditor"]["sessionId"] != valid_review["auditor"]["sessionId"]
                checks.append(check("HC-RELEASE-AUDIT-REVIEW", "PASS" if review_bound and evidence_bound and review_independent else ("INVALIDATED" if not review_bound or not evidence_bound else "FAIL"), "external audit binds the accepted review/evidence and uses a distinct audit role" if review_bound and evidence_bound and review_independent else "external audit review/evidence binding drifted or auditor is not independent"))
            report_transcript = verify_ref(p["root"], report["transcript"], "HC-RELEASE-AUDIT-TRANSCRIPT")
            checks.append(report_transcript)
            report_manifest_mismatch = {key: {"expected": value, "actual": report.get(key)} for key, value in expected.items() if report.get(key) != value}
            checks.append(check("HC-RELEASE-AUDIT-MANIFESTS", "PASS" if not report_manifest_mismatch else "INVALIDATED", "external audit binds current package/runtime/matrix manifests" if not report_manifest_mismatch else "external audit manifest bindings drifted", mismatch=report_manifest_mismatch))
            report_independent = (report["auditor"]["actorId"], report["auditor"]["sessionId"]) not in executors and report["auditor"]["actorId"] != candidate["implementer"]["actorId"]
            checks.append(check("HC-RELEASE-AUDIT-INDEPENDENCE", "PASS" if report_independent else "FAIL", "external auditor is independent from implementation/execution" if report_independent else "external auditor overlaps implementation or execution"))
            audit_finding_checks = review_finding_checks(p["root"], report, load_json(p["key_objectives"]), "RELEASE_READY", contract)
            checks.extend(audit_finding_checks)
            report_clean = report["result"] == "PASS" and all(item["status"] == "PASS" for item in audit_finding_checks)
            checks.append(check("HC-RELEASE-AUDIT-RESULT", "PASS" if report_clean else "FAIL", "external audit result is PASS and every finding is admitted against the current objectives" if report_clean else "external audit result or finding admission cannot qualify"))
    except ControlError as exc:
        checks.append(check(exc.check_id, exc.status, exc.message))
    checks.append(summary_check(checks))
    return checks


def assurance_matrix_checks(p: dict[str, Path]) -> list[dict[str, Any]]:
    """Bind implementation closure and the separately sealed package audit receipt."""
    try:
        lock = load_json(p["lock"])
        package_mode = lock.get("packageMode")
        package = load_json(p["governance"] / "package-manifest.json")
        entry = next((item for item in package.get("files", []) if item.get("path") == "references/controller-assurance-matrix.json"), None)
        if not isinstance(entry, dict):
            return [check("HC-ASSURANCE-MATRIX-PACKAGE", "FAIL", "package manifest omits controller assurance matrix")]
        matrix_ref = {"path": ".vibe-control/governance/controller-assurance-matrix.json", "bytes": entry.get("bytes"), "sha256": entry.get("sha256"), "tracked": True}
        bound = verify_ref(p["root"], matrix_ref, "HC-ASSURANCE-MATRIX-PACKAGE")
        if bound["status"] != "PASS":
            return [bound]
        package_assurance = package.get("assuranceValidation", {})
        package_binding = lock.get("packageBinding", {})
        if package_mode == "DEVELOPMENT":
            source_kind = package_binding.get("sourceKind") if isinstance(package_binding, dict) else None
            binding_version_ok = isinstance(package_binding, dict) and package_binding.get("version") == package.get("version")
            has_git_identity = (
                isinstance(package_binding.get("commit"), str) and len(package_binding["commit"]) == 40
                and isinstance(package_binding.get("tree"), str) and len(package_binding["tree"]) == 40
            ) if isinstance(package_binding, dict) else False
            source_shape_ok = (
                source_kind in {"GIT_ROOT", "GIT_SUBDIRECTORY"} and has_git_identity
                or source_kind == "PORTABLE_COPY" and "commit" not in package_binding and "tree" not in package_binding
                or source_kind is None and package_binding.get("version") != VERSION and has_git_identity
            )
            source_check = check(
                "HC-DEVELOPMENT-PACKAGE-SOURCE",
                "PASS" if binding_version_ok and source_shape_ok else "FAIL",
                "development package source identity matches its package version and provenance limits" if binding_version_ok and source_shape_ok else "development package source identity is forged, incomplete, or bound to another package version",
                sourceKind=source_kind,
                packageVersion=package.get("version"),
                bindingVersion=package_binding.get("version") if isinstance(package_binding, dict) else None,
            )
        else:
            source_check = check("HC-DEVELOPMENT-PACKAGE-SOURCE", "PASS", "sealed package uses the separate Git/tag/audit identity chain")
        expected_maturity = "DEVELOPMENT_DIAGNOSTIC" if package_mode == "DEVELOPMENT" else "AWAITING_EXTERNAL_VALIDATION"
        allowed_readiness = {"CONTROL_IMPLEMENTATION_READY"} if package_mode == "SEALED" else {"CONTROL_IMPLEMENTATION_READY", "CONTROL_IMPLEMENTATION_PENDING_EXTERNAL_VALIDATION"}
        package_content_ready = (
            package.get("maturity") == expected_maturity
            and isinstance(package_assurance, dict)
            and package_assurance.get("status") == "PASS"
            and package_assurance.get("readiness") in allowed_readiness
            and package_assurance.get("formalClaimsAllowed") is False
        )
        maturity_check = check(
            "HC-ASSURANCE-PACKAGE-MATURITY",
            "PASS" if package_content_ready else "BLOCKED",
            "package content reports implementation closure without self-granting release readiness" if package_content_ready else "package implementation closure is not valid",
            maturity=package.get("maturity"),
            assuranceValidation=package_assurance,
        )
        matrix = load_json(p["governance"] / "controller-assurance-matrix.json")
        if not isinstance(matrix, dict):
            return [bound, maturity_check, check("HC-ASSURANCE-MATRIX-TYPE", "FAIL", "assurance matrix must be an object")]
        requirements = matrix.get("requirements", [])
        confirmed = matrix.get("confirmedControls", [])
        invalid_sections = [name for name, value in (("requirements", requirements), ("confirmedControls", confirmed)) if not isinstance(value, list)]
        shape_check = check(
            "HC-ASSURANCE-MATRIX-SHAPE",
            "PASS" if not invalid_sections else "FAIL",
            "assurance matrix sections are arrays" if not invalid_sections else "assurance matrix sections must be arrays",
            invalidSections=invalid_sections,
        )
        raw_items = [*(requirements if isinstance(requirements, list) else []), *(confirmed if isinstance(confirmed, list) else [])]
        invalid_items = [index for index, item in enumerate(raw_items) if not isinstance(item, dict)]
        item_type_check = check(
            "HC-ASSURANCE-MATRIX-ITEM-TYPE",
            "PASS" if not invalid_items else "FAIL",
            "assurance matrix items are objects" if not invalid_items else "assurance matrix contains non-object items",
            invalidItemIndexes=invalid_items,
        )
        items = [item for item in raw_items if isinstance(item, dict)]
        item_id_list = [item.get("id") for item in items]
        item_ids = {item_id for item_id in item_id_list if isinstance(item_id, str)}
        duplicate_ids = sorted({item_id for item_id in item_id_list if isinstance(item_id, str) and item_id_list.count(item_id) > 1})
        duplicate_check = check(
            "HC-ASSURANCE-MATRIX-DUPLICATE-ID",
            "PASS" if not duplicate_ids else "FAIL",
            "assurance matrix IDs are unique" if not duplicate_ids else "assurance matrix contains duplicate IDs",
            duplicateIds=duplicate_ids,
        )
        package_version = str(lock.get("packageBinding", {}).get("version", ""))
        missing_controls = sorted(required_assurance_control_ids(package_version) - item_ids)
        open_items = sorted(item.get("id", "UNKNOWN") for item in items if item.get("implementationStatus") != "IMPLEMENTED")
        pending = sorted(item.get("id", "UNKNOWN") for item in items if item.get("independentValidation") not in {"PASS", "NOT_REQUIRED"})
        declared_formal = matrix.get("formalClaimsAllowed") is True
        if package_mode == "SEALED":
            receipt = load_json(p["package_receipt"])
            try:
                validate_object("package-audit-receipt", receipt)
                receipt_checks = [check("HC-SCHEMA-PACKAGE-AUDIT-RECEIPT", "PASS", "package audit receipt satisfies Schema 3.2")]
                receipt_checks.extend(validate_materialized_receipt(
                    receipt,
                    version=VERSION,
                    package_sha=sha256_file(p["governance"] / "package-manifest.json"),
                    runtime_sha=sha256_file(p["runtime"] / "runtime-manifest.json"),
                    matrix_sha=sha256_file(p["governance"] / "controller-assurance-matrix.json"),
                ))
            except ControlError as exc:
                receipt_checks = [check(exc.check_id, exc.status, exc.message)]
            receipt_ready = all(item["status"] == "PASS" for item in receipt_checks)
        else:
            receipt_checks = [check("HC-DEVELOPMENT-PACKAGE-CLAIM-CAP", "BLOCKED", "development package integrity is usable but formal claims above DEVELOPMENT_CHECKED are disabled")]
            receipt_ready = False
        ready = package_mode == "SEALED" and package_content_ready and receipt_ready and not declared_formal and not invalid_sections and not invalid_items and not duplicate_ids and not missing_controls and not open_items and not pending
        coverage_check = check(
            "HC-ASSURANCE-MATRIX-CONTROL-COVERAGE",
            "PASS" if not missing_controls else "FAIL",
            "all required package assurance control IDs are present" if not missing_controls else "required package assurance controls are missing",
            missingControls=missing_controls,
        )
        implementation_check = check(
            "HC-ASSURANCE-MATRIX-IMPLEMENTATION",
            "PASS" if not open_items else "FAIL",
            "all package assurance controls are implemented" if not open_items else "package assurance controls remain open",
            openItems=open_items,
        )
        independent_check = check(
            "HC-ASSURANCE-MATRIX-INDEPENDENT",
            "PASS" if not pending else "BLOCKED",
            "all package assurance controls have independent validation" if not pending else "package assurance controls await independent validation",
            pendingItems=pending,
        )
        formal_status = "PASS" if ready else ("FAIL" if declared_formal else "BLOCKED")
        formal_message = "exact package audit receipt permits use of the implemented formal gate" if ready else ("the matrix cannot self-grant package release readiness" if declared_formal else "development mode or package audit closure prevents formal eligibility")
        formal_check = check("HC-ASSURANCE-MATRIX-FORMAL", formal_status, formal_message)
        return [bound, source_check, maturity_check, *receipt_checks, shape_check, item_type_check, duplicate_check, coverage_check, implementation_check, independent_check, formal_check]
    except ControlError as exc:
        return [check(exc.check_id, exc.status, exc.message)]


def validate(project: Path, *, mutate_state: bool = True) -> dict[str, Any]:
    p = paths(project); root = git_root(p["root"]); checks = dependency_checks()
    if any(item["status"] != "PASS" for item in checks):
        return envelope(status="BLOCKED", checks=checks, formal={"eligible": False, "maxClaimLevel": "DIAGNOSTIC", "blockers": ["DEPENDENCY_BLOCKED"]})
    try:
        state, lock, catalog, task_lock, contract = current_objects(p)
    except ControlError as exc:
        checks.append(check(exc.check_id, exc.status, exc.message)); status = exc.status
        return envelope(status=status, checks=checks, formal={"eligible": False, "maxClaimLevel": "DIAGNOSTIC", "blockers": [exc.check_id]})
    byte_policy = evidence_byte_policy_check(root, p)
    checks.append(byte_policy)
    positioning: dict[str, Any] = {"deliveryObjective": "UNKNOWN"}
    resolved: dict[str, Any] = {"warnings": [], "investigations": []}
    try:
        positioning = load_json(p["positioning"]); validate_object("project-positioning", positioning)
        checks.extend(positioning_checkpoint_source_checks(positioning))
        resolved = load_json(p["resolved_rules"]); validate_object("resolved-rule-set", resolved)
        rule_inputs = load_json(p["rule_inputs"])
        checks.extend(verify_positioning(root, positioning))
        compiled = compile_for_project(rule_inputs, root, p["runtime"], expected_runtime_manifest_sha256=lock["runtime"]["sha256"])
        checks.extend(compiler_checks(compiled))
        fresh_rule_hash = compiled["canonicalSha256"]
        fresh_compiler = rule_compiler_binding(p["runtime"])
        rules_match = resolved["canonicalSha256"] == fresh_rule_hash and resolved["compiler"] == fresh_compiler
        checks.append(check("HC-RULESET-BINDING", "PASS" if rules_match else "INVALIDATED", "resolved rules and compiler identity equal a fresh deterministic compilation" if rules_match else "positioning/Profile/adapter/Skill/overlay/catalog/compiler drift invalidated downstream objects", recordedSha256=resolved["canonicalSha256"], actualSha256=fresh_rule_hash, recordedCompiler=resolved.get("compiler"), actualCompiler=fresh_compiler))
        state_binding_ok = state["positioningId"] == positioning["positioningId"] and state["ruleSetId"] == resolved["ruleSetId"]
        checks.append(check("HC-RULESET-BINDING", "PASS" if state_binding_ok else "FAIL", "state identifies the locked positioning and rule set" if state_binding_ok else "state positioning/rule identity is stale or manually changed"))
        checks.append(coverage_check(compiled, [get_case(catalog, case_id) for case_id in contract["requiredCaseIds"]]))
        checks.extend(checkpoint_contract_checks(contract, positioning, catalog, release_intent_cap(lock)))
    except ControlError as exc:
        checks.append(check(exc.check_id, exc.status, exc.message))
        compiled = {"canonical": {"layers": [], "runtimeAdapters": []}, "canonicalSha256": ""}
    checks.extend(phase_history_checks(state))
    checks.append(check("HC-HEALTH-CLAIM-COMPATIBILITY", "PASS" if state["health"] == "CLEAR" else "FAIL", "health permits claims" if state["health"] == "CLEAR" else "BLOCKED/FAILED health cannot be eligible"))
    intent_cap = release_intent_cap(lock)
    release_task_requires_r3 = lock["releaseIntent"] == "EXTERNAL_RELEASE" and contract["maxClaimLevel"] == "RELEASE_READY"
    external_release_risk_ok = not release_task_requires_r3 or contract["risk"] == "R3"
    external_risk_message = "external RELEASE_READY path uses an R3 task" if release_task_requires_r3 and external_release_risk_ok else ("task does not request the external RELEASE_READY path" if external_release_risk_ok else "EXTERNAL_RELEASE cannot claim RELEASE_READY from a non-R3 task")
    checks.append(check("HC-EXTERNAL-RELEASE-R3", "PASS" if external_release_risk_ok else "FAIL", external_risk_message))
    external_release_crypto = requires_external_release_crypto(lock, contract)
    if external_release_crypto:
        try:
            require_r3_trusted_keys(lock)
            checks.append(check("HC-R3-TRUSTED-KEYS", "PASS", "external R3 release has all distinct externally managed public-key roles"))
        except ControlError as exc:
            checks.append(check(exc.check_id, exc.status, exc.message))
    checks.append(verify_ref(root, lock["skill"], "HC-SKILL-MANIFEST"))
    checks.append(verify_ref(root, lock["runtime"], "HC-RUNTIME-MANIFEST"))
    checks.append(verify_ref(root, lock["keyObjectives"], "HC-OBJECTIVES-LOCK"))
    for name, ref in lock["packageBinding"].items():
        if isinstance(ref, dict):
            checks.append(verify_ref(root, ref, f"HC-PACKAGE-BINDING-{name.upper()}"))
    if lock["packageMode"] == "SEALED":
        checks.append(verify_ref(root, lock["packageAuditReceipt"], "HC-PACKAGE-AUDIT-RECEIPT"))
    checks.append(verify_ref(root, lock["caseCatalog"], "HC-GOVERNANCE-CASE-CATALOG"))
    if "evidenceBytePolicy" in lock:
        checks.append(verify_ref(root, lock["evidenceBytePolicy"], "HC-EVIDENCE-GIT-BYTE-POLICY"))
    else:
        checks.append(check("HC-EVIDENCE-GIT-BYTE-POLICY", "BLOCKED", "governance lock does not bind the evidence Git byte policy"))
    checks.append(verify_ref(root, lock["ruleInputs"], "HC-RULESET-BINDING"))
    checks.append(verify_ref(root, lock["positioning"], "HC-POSITIONING-CONFIRMED"))
    checks.append(verify_ref(root, lock["resolvedRuleSet"], "HC-RULESET-BINDING"))
    checks.append(verify_ref(root, lock["ruleCompiler"], "HC-RULESET-BINDING"))
    checks.append(verify_ref(root, lock["profileDirectory"], "HC-RULESET-BINDING"))
    checks.append(verify_ref(root, lock["adapterDirectory"], "HC-ADAPTER-CAPABILITY"))
    for index, ref in enumerate(lock["skillBindings"]): checks.append(verify_ref(root, ref, f"HC-SKILL-BINDING-{index+1}"))
    for index, ref in enumerate(lock["authorityFiles"]): checks.append(verify_ref(root, ref, f"HC-GOVERNANCE-AUTHORITY-{index+1}"))
    runtime_manifest = load_json(p["runtime"] / "runtime-manifest.json")
    for index, ref in enumerate(runtime_manifest.get("files", [])):
        checks.append(verify_ref(p["runtime"], {**ref, "tracked": False}, f"HC-RUNTIME-FILE-{index+1}"))
    checks.extend(assurance_matrix_checks(p))
    for ident, ref in (("LOCK", task_lock["governanceLock"]), ("CONTRACT", task_lock["contract"]), ("OBJECTIVES", task_lock["keyObjectives"]), ("CASES", task_lock["caseCatalog"]), ("POSITIONING", task_lock["positioning"]), ("RULESET", task_lock["resolvedRuleSet"]), ("CHECKPOINT-CONFIRMATION", task_lock["checkpointConfirmation"])):
        checks.append(verify_ref(root, ref, f"HC-TASK-{ident}-BINDING"))
    if "automationPolicy" in task_lock:
        checks.append(verify_ref(root, task_lock["automationPolicy"], "HC-AUTOMATION-POLICY-DRIFT"))
    current_checkpoint_hash = checkpoint_set_sha256(contract)
    checks.append(check("HC-CHECKPOINT-HASH", "PASS" if task_lock["checkpointSetSha256"] == current_checkpoint_hash else "INVALIDATED", "task lock binds the current checkpoint set" if task_lock["checkpointSetSha256"] == current_checkpoint_hash else "checkpoint set changed after task lock", expected=current_checkpoint_hash, actual=task_lock["checkpointSetSha256"]))
    for index, ref in enumerate(task_lock["authorityBindings"]): checks.append(verify_ref(root, ref, f"HC-TASK-AUTHORITY-{index+1}"))
    known_cases = {item["id"] for item in catalog["cases"]}; missing = sorted(set(contract["requiredCaseIds"]) - known_cases)
    checks.append(check("HC-TASK-CASE-CLOSURE", "PASS" if not missing else "FAIL", "task cases close over catalog" if not missing else "task references unknown cases", missing=missing))
    actual_rule_ids = sorted(item["id"] for item in compiled["canonical"]["layers"])
    actual_capabilities = sorted({capability for item in compiled["canonical"]["layers"] for capability in item["rule"].get("caseCapabilities", [])})
    task_rules_ok = task_lock["applicableRuleIds"] == actual_rule_ids and task_lock["requiredCaseCapabilities"] == actual_capabilities
    checks.append(check("HC-RULESET-BINDING", "PASS" if task_rules_ok else "INVALIDATED", "task lock contains the complete applicable rule/capability closure" if task_rules_ok else "task lock omitted or changed applicable rules/capabilities"))
    derived_phase = "CONTRACT_LOCKED"; candidate = None; candidate_path: Path | None = None
    if state.get("candidateId"):
        try:
            candidate_path, candidate = candidate_for(p, state)
            tracked_candidate = bool(git(root, "ls-files", "--error-unmatch", "--", candidate_path.relative_to(root).as_posix(), required=False))
            expected_task_lock_ref = content_ref(root, p["task_locks"] / f"{state['taskId']}.json")
            objective_lock = load_json(p["key_objectives"])
            expected_input_bindings = [task_lock["contract"], task_lock["governanceLock"], task_lock["keyObjectives"], task_lock["caseCatalog"], task_lock["positioning"], task_lock["resolvedRuleSet"], task_lock["checkpointConfirmation"], *task_lock["authorityBindings"]]
            if "automationPolicy" in task_lock:
                expected_input_bindings.append(task_lock["automationPolicy"])
            checks.append(check("HC-CANDIDATE-TRACKED", "PASS" if tracked_candidate else "BLOCKED", "candidate record is tracked" if tracked_candidate else "candidate record is not tracked"))
            checks.append(check("HC-CANDIDATE-TASK-IDENTITY", "PASS" if candidate["taskId"] == state["taskId"] == contract["taskId"] else "FAIL", "candidate/task identities close" if candidate["taskId"] == state["taskId"] == contract["taskId"] else "candidate/task identity mismatch"))
            checks.append(check("HC-CANDIDATE-CONTRACT-IDENTITY", "PASS" if candidate["taskLock"]["sha256"] == sha256_file(p["task_locks"] / f"{state['taskId']}.json") else "FAIL", "candidate binds task lock" if candidate["taskLock"]["sha256"] == sha256_file(p["task_locks"] / f"{state['taskId']}.json") else "candidate/task-lock hash mismatch"))
            checks.append(check("HC-CANDIDATE-TASK-LOCK-REF", "PASS" if ref_key(candidate["taskLock"]) == ref_key(expected_task_lock_ref) else "FAIL", "candidate binds the exact current task-lock reference" if ref_key(candidate["taskLock"]) == ref_key(expected_task_lock_ref) else "candidate task-lock path/bytes/hash differs from current task lock"))
            checks.append(check("HC-CANDIDATE-INPUT-CLOSURE", "PASS" if refs_equal(candidate["inputBindings"], expected_input_bindings) else "FAIL", "candidate input bindings equal task-lock bindings" if refs_equal(candidate["inputBindings"], expected_input_bindings) else "candidate input bindings were substituted"))
            automation_bound = candidate.get("automationPolicy") == task_lock.get("automationPolicy")
            checks.append(check("HC-AUTOMATION-POLICY-DRIFT", "PASS" if automation_bound else "INVALIDATED", "candidate binds the current task automation policy" if automation_bound else "candidate automation policy differs from the task lock"))
            checks.append(check("HC-CHECKPOINT-HASH", "PASS" if candidate["checkpointSetSha256"] == task_lock["checkpointSetSha256"] else "INVALIDATED", "candidate binds the locked checkpoint set" if candidate["checkpointSetSha256"] == task_lock["checkpointSetSha256"] else "candidate checkpoint binding drifted"))
            objective_bound = ref_key(candidate["keyObjectives"]) == ref_key(task_lock["keyObjectives"]) and refs_equal(candidate["requirementSources"], objective_lock["sourceDocuments"])
            checks.append(check("HC-CANDIDATE-OBJECTIVE-BINDING", "PASS" if objective_bound else "FAIL", "candidate binds current objectives and requirement sources" if objective_bound else "candidate objective or requirement-source binding drifted"))
            positioning_bound = ref_key(candidate["positioning"]) == ref_key(task_lock["positioning"])
            rules_bound = ref_key(candidate["resolvedRuleSet"]) == ref_key(task_lock["resolvedRuleSet"])
            checks.append(check("HC-RULESET-BINDING", "PASS" if positioning_bound and rules_bound else "FAIL", "candidate directly binds positioning and resolved rules" if positioning_bound and rules_bound else "candidate positioning/rule binding differs from task lock"))
            commit_exists = bool(git(root, "cat-file", "-e", f"{candidate['commit']}^{{commit}}", required=False) == "")
            # cat-file -e is silent on success, so independently require a resolvable commit value.
            resolved_commit = git(root, "rev-parse", "--verify", f"{candidate['commit']}^{{commit}}", required=False)
            commit_exists = resolved_commit == candidate["commit"]
            actual_tree = git(root, "show", "-s", "--format=%T", candidate["commit"], required=False) if commit_exists else ""
            ancestor = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", task_lock["baselineCommit"], candidate["commit"]], capture_output=True).returncode == 0 if commit_exists else False
            head_ancestor = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", candidate["commit"], "HEAD"], capture_output=True).returncode == 0 if commit_exists else False
            actual_changed = [x for x in git(root, "diff", "--name-only", f"{task_lock['baselineCommit']}..{candidate['commit']}", required=False).splitlines() if x] if commit_exists else []
            actual_product = [x for x in actual_changed if not x.startswith(".vibe-control/")]
            objective_changes = objective_path_changes(actual_product)
            forbidden = [x for x in actual_product if any(path_matches(x, pat) for pat in contract["forbiddenPaths"])]
            outside = [x for x in actual_product if not any(path_matches(x, pat) for pat in contract["allowedPaths"])]
            checks.append(check("HC-CANDIDATE-COMMIT", "PASS" if commit_exists else "FAIL", "candidate commit exists" if commit_exists else "candidate commit is invalid"))
            checks.append(check("HC-CANDIDATE-TREE", "PASS" if actual_tree == candidate["tree"] else "FAIL", "candidate tree matches Git" if actual_tree == candidate["tree"] else "candidate tree mismatch", actualTree=actual_tree))
            checks.append(check("HC-CANDIDATE-ANCESTRY", "PASS" if ancestor and head_ancestor else "FAIL", "candidate is between baseline and HEAD" if ancestor and head_ancestor else "candidate ancestry is invalid"))
            checks.append(check("HC-CANDIDATE-DIFF", "PASS" if actual_product == candidate["changedPaths"] else "FAIL", "candidate changedPaths equals real Git diff" if actual_product == candidate["changedPaths"] else "candidate changedPaths is self-reported incorrectly", actual=actual_product, declared=candidate["changedPaths"]))
            checks.append(check("HC-CANDIDATE-PATH-ENVELOPE", "PASS" if not forbidden and not outside else "FAIL", "candidate real diff is inside path envelope" if not forbidden and not outside else "candidate real diff escapes path envelope", forbidden=forbidden, outsideAllowed=outside))
            checks.append(check("HC-OBJECTIVES-WRITE-BOUNDARY", "PASS" if not objective_changes else "FAIL", "ordinary candidate does not modify KEY_OBJECTIVES.md" if not objective_changes else "ordinary candidate modified KEY_OBJECTIVES.md", paths=objective_changes))
            for index, ref in enumerate(candidate["inputBindings"]): checks.append(verify_ref(root, ref, f"HC-CANDIDATE-INPUT-{index+1}"))
            post = [x for x in git(root, "diff", "--name-only", f"{candidate['commit']}..HEAD").splitlines() if x and not x.startswith(".vibe-control/")]
            checks.append(check("HC-CANDIDATE-HEAD", "PASS" if not post else "INVALIDATED", "post-candidate changes are control-only" if not post else "product changed after candidate", paths=post))
            derived_phase = "CANDIDATE_FROZEN"
        except ControlError as exc: checks.append(check(exc.check_id, exc.status, exc.message))
    evidence_by_case: dict[str, dict[str, Any]] = {}
    evidence_refs_by_case: dict[str, dict[str, Any]] = {}
    executors: set[tuple[str, str]] = set()
    if candidate:
        for evidence_path in sorted(p["evidence"].glob("*.json")):
            if evidence_path.name.endswith(("attestation.json", "adapter-invocation.json")): continue
            try:
                evidence = load_json(evidence_path); validate_object("execution-evidence", evidence)
                evidence_id_ok = evidence_id_binding_matches(evidence_path, evidence)
                checks.append(check(
                    "HC-EVIDENCE-ID-BINDING", "PASS" if evidence_id_ok else "FAIL",
                    "evidence filename, ID and invocation namespace match" if evidence_id_ok else "evidence filename, ID or invocation namespace differs",
                    path=str(evidence_path),
                ))
                tracked_evidence = bool(git(root, "ls-files", "--error-unmatch", "--", evidence_path.relative_to(root).as_posix(), required=False))
                checks.append(check("HC-EVIDENCE-TRACKED", "PASS" if tracked_evidence else "BLOCKED", "evidence record is tracked" if tracked_evidence else "evidence record is not tracked", path=str(evidence_path)))
                evidence_blob_check = git_blob_ref_check(root, content_ref(root, evidence_path))
                checks.append(evidence_blob_check)
                case = get_case(catalog, evidence["caseId"]); expected_case_hash = sha256_bytes(canonical_bytes(case)); expected_oracle_hash = sha256_bytes(canonical_bytes(case["oracle"])); expected_input_hash = sha256_bytes(canonical_bytes(candidate["inputBindings"]))
                identity_ok = evidence["taskId"] == contract["taskId"] and evidence["candidateId"] == candidate["candidateId"] and evidence["candidateCommit"] == candidate["commit"] and ref_key(evidence["positioning"]) == ref_key(candidate["positioning"]) and ref_key(evidence["resolvedRuleSet"]) == ref_key(candidate["resolvedRuleSet"])
                expected_checkpoint_ids = checkpoint_ids_for_case(contract, evidence["caseId"])
                provenance_ok = evidence["caseHash"] == expected_case_hash and evidence["oracleHash"] == expected_oracle_hash and evidence["inputHash"] == expected_input_hash and evidence["checkpointSetSha256"] == candidate["checkpointSetSha256"] and evidence["checkpointIds"] == expected_checkpoint_ids
                counters = evidence["counters"]; count_ok = counters["executed"] > 0 and counters["skipped"] == 0 and counters["executed"] == counters["passed"] + counters["failed"] + counters["skipped"] and counters["failed"] == 0 and evidence["result"] == "PASS" and evidence["exitCode"] == case["oracle"]["exitCode"]
                observation_ok = evidence["observation"] in {"runtime-observed", "blackbox-observed"} and evidence["observation"] == case["observation"]
                ref_ok = verify_ref(root, evidence["transcript"], f"HC-TRANSCRIPT-{evidence['evidenceId']}"); checks.append(ref_ok)
                invocation_ok = verify_ref(root, evidence["adapterInvocation"], f"HC-ADAPTER-INVOCATION-{evidence['evidenceId']}"); checks.append(invocation_ok)
                byte_refs = [evidence["transcript"], evidence["adapterInvocation"], *evidence["artifacts"]]
                if evidence.get("externalTranscript"):
                    byte_refs.append(evidence["externalTranscript"])
                byte_ref_checks = [git_blob_ref_check(root, ref) for ref in byte_refs]
                checks.extend(byte_ref_checks)
                invocation = None
                invocation_schema_ok = False
                if invocation_ok["status"] == "PASS":
                    try:
                        invocation = load_json(safe_relative(root, evidence["adapterInvocation"]["path"]))
                        validate_object("adapter-invocation", invocation)
                        invocation_schema_ok = True
                    except ControlError as exc:
                        checks.append(check(exc.check_id, exc.status, exc.message, caseId=evidence.get("caseId")))
                        invocation = None
                artifact_results = [verify_ref(root, ref, f"HC-ARTIFACT-{evidence['evidenceId']}-{index+1}") for index, ref in enumerate(evidence["artifacts"])]
                checks.extend(artifact_results)
                descriptor = next((item for item in resolved["canonical"]["runtimeAdapters"] if item.get("id") == case["adapter"]["id"]), None)
                adapter_ok = evidence_adapter_contract_matches(evidence, case, descriptor, invocation)
                checks.append(check("HC-ADAPTER-CAPABILITY", "PASS" if adapter_ok else "FAIL", "evidence stays inside the locked adapter proof boundary" if adapter_ok else "adapter identity, capabilities or required browser artifacts do not close", caseId=evidence["caseId"]))
                signature_ok = True
                external_attestation_required = evidence["observation"] == "blackbox-observed"
                if external_attestation_required:
                    attestation_path = p["evidence"] / f"{evidence['evidenceId']}.attestation.json"
                    try:
                        attestation = load_json(attestation_path); validate_object("external-evidence-attestation", attestation)
                        if attestation["evidence"] != evidence:
                            raise ControlError("HC-EXTERNAL-EVIDENCE-ATTESTATION", "attestation payload differs from evidence")
                        if external_release_crypto:
                            if not isinstance(attestation.get("keyId"), str) or not isinstance(attestation.get("signature"), dict):
                                raise ControlError("HC-EXECUTOR-SIGNATURE", "external R3 release evidence requires keyId and signature")
                            verify_signature(attestation, require_key_actor(lock, attestation["keyId"], "executor", evidence["executor"]["actorId"]), "HC-EXECUTOR-SIGNATURE")
                            checks.append(check("HC-EXECUTOR-SIGNATURE", "PASS", "external R3 release execution has a trusted executor signature", caseId=evidence["caseId"]))
                        elif has_signature_fields(attestation):
                            if not isinstance(attestation.get("keyId"), str) or not isinstance(attestation.get("signature"), dict):
                                raise ControlError("HC-EXECUTOR-SIGNATURE", "keyId and signature must be supplied together when an optional signature is present")
                            verify_signature(attestation, require_key_actor(lock, attestation["keyId"], "executor", evidence["executor"]["actorId"]), "HC-EXECUTOR-SIGNATURE")
                            checks.append(check("HC-EXECUTOR-SIGNATURE", "PASS", "optional external executor signature is valid", caseId=evidence["caseId"]))
                        else:
                            checks.append(check("HC-EXTERNAL-EVIDENCE-ATTESTATION", "PASS", "candidate-bound external evidence attestation is available for human review", caseId=evidence["caseId"]))
                    except ControlError as exc:
                        signature_ok = False; checks.append(check(exc.check_id, exc.status, exc.message, caseId=evidence["caseId"]))
                checks.append(check("HC-CASE-PROVENANCE", "PASS" if identity_ok and provenance_ok else "FAIL", "case provenance binds candidate/oracle/input" if identity_ok and provenance_ok else "case provenance mismatch", caseId=evidence["caseId"]))
                checks.append(check("HC-CASE-OBSERVATION-ELIGIBILITY", "PASS" if observation_ok else "FAIL", "observation source is eligible" if observation_ok else "declared/derived/human or mismatched observation is ineligible", caseId=evidence["caseId"]))
                checks.append(check("HC-CASE-COUNTERS", "PASS" if count_ok else "FAIL", "per-case counters qualify" if count_ok else "zero/skip/failure/non-conservation", caseId=evidence["caseId"]))
                byte_closed = byte_policy["status"] == "PASS" and evidence_blob_check["status"] == "PASS" and all(item["status"] == "PASS" for item in byte_ref_checks)
                if identity_ok and provenance_ok and count_ok and observation_ok and signature_ok and evidence_id_ok and tracked_evidence and ref_ok["status"] == "PASS" and invocation_ok["status"] == "PASS" and invocation_schema_ok and all(item["status"] == "PASS" for item in artifact_results) and adapter_ok and byte_closed:
                    evidence_by_case[evidence["caseId"]] = evidence
                    evidence_refs_by_case[evidence["caseId"]] = content_ref(root, evidence_path)
                executors.add((evidence["executor"]["actorId"], evidence["executor"]["sessionId"]))
            except ControlError as exc: checks.append(check(exc.check_id, exc.status, exc.message, path=str(evidence_path)))
        missing_coverage = sorted(set(contract["requiredCaseIds"]) - set(evidence_by_case))
        eligible_evidence_by_case = {
            case_id: evidence_by_case[case_id]["evidenceId"]
            for case_id in sorted(evidence_by_case)
        }
        checks.append(check(
            "HC-REQUIRED-CASE-COVERAGE",
            "PASS" if not missing_coverage else "FAIL",
            "each required case has eligible execution" if not missing_coverage else "required cases lack eligible execution",
            missing=missing_coverage,
            eligibleEvidenceByCase=eligible_evidence_by_case,
        ))
        if not missing_coverage: derived_phase = "VERIFIED"
        try:
            closure_path, audit_closure = _current_audit_closure(p, contract, candidate)
            if audit_closure is not None:
                closure_ref = content_ref(root, closure_path)
                checks.append(verify_ref(root, closure_ref, "HC-AUDIT-CLOSURE-TRACKED"))
                checks.append(check(
                    "HC-AUDIT-STOP-CLOSURE", "BLOCKED",
                    "candidate exploratory audit budget is closed; a new candidate or task is required",
                    auditClosure=closure_ref["path"],
                ))
        except ControlError as exc:
            checks.append(check(exc.check_id, exc.status, exc.message))
    reviews = sorted(p["reviews"].glob("*.json")); valid_review = None; valid_review_path: Path | None = None
    if reviews and derived_phase == "VERIFIED":
        try:
            review_path = reviews[-1]; review = load_json(review_path); validate_object("review-attestation", review)
            review_tracked = verify_ref(root, content_ref(root, review_path), "HC-REVIEW-TRACKED"); checks.append(review_tracked)
            evidence_refs = list(evidence_refs_by_case.values())
            review_evidence_refs_ok = refs_equal(review["evidenceRefs"], evidence_refs)
            for index, ref in enumerate(review["evidenceRefs"]): checks.append(verify_ref(root, ref, f"HC-REVIEW-EVIDENCE-REF-{index+1}"))
            evidence_ref_checks_ok = all(item["status"] == "PASS" for item in checks if item["id"].startswith("HC-REVIEW-EVIDENCE-REF-"))
            result_ok = review["result"] == "PASS"
            bound = review["taskId"] == contract["taskId"] and review["candidateId"] == candidate["candidateId"] and review["candidateCommit"] == candidate["commit"] and review["checkpointSetSha256"] == candidate["checkpointSetSha256"] and ref_key(review["keyObjectives"]) == ref_key(candidate["keyObjectives"]) and ref_key(review["positioning"]) == ref_key(candidate["positioning"]) and ref_key(review["resolvedRuleSet"]) == ref_key(candidate["resolvedRuleSet"]) and set(review["evidenceIds"]) == {item["evidenceId"] for item in evidence_by_case.values()} and review_evidence_refs_ok
            auditor_identity = (review["auditor"]["actorId"], review["auditor"]["sessionId"])
            implementer_identity = (candidate["implementer"]["actorId"], candidate["implementer"]["sessionId"])
            independent = auditor_identity not in executors and auditor_identity != implementer_identity and review["auditor"]["actorId"] != candidate["implementer"]["actorId"]
            checkpoint_review_checks = review_checkpoint_checks(review, contract, evidence_by_case)
            checks.extend(checkpoint_review_checks)
            checkpoint_review_ok = all(item["status"] == "PASS" for item in checkpoint_review_checks)
            finding_checks = review_finding_checks(root, review, load_json(p["key_objectives"]), "VERIFIED", contract)
            checks.extend(finding_checks)
            findings_ok = all(item["status"] == "PASS" for item in finding_checks)
            transcript_ok = verify_ref(root, review["transcript"], f"HC-REVIEW-TRANSCRIPT-{review['reviewId']}"); checks.append(transcript_ok)
            checks.append(check("HC-REVIEW-RESULT", "PASS" if result_ok else "FAIL", "review result is PASS" if result_ok else "review result is FAIL"))
            checks.append(check("HC-REVIEW-REQUIRED", "PASS" if bound and independent and checkpoint_review_ok and findings_ok and result_ok else "FAIL", "review is bound, independent, checkpoint-complete, and admitted against current objectives" if bound and independent and checkpoint_review_ok and findings_ok and result_ok else "review binding/independence/checkpoint/result/finding admission failed"))
            signature_ok = True
            try:
                signed = verify_record_signature(review, lock, "auditor", review["auditor"]["actorId"], required=external_release_crypto, check_id="HC-AUDITOR-SIGNATURE")
                checks.append(check("HC-AUDITOR-SIGNATURE" if signed else "HC-REVIEW-ATTESTATION", "PASS", "review signature binds trusted auditor" if signed else "tracked review is an explicit non-cryptographic attestation"))
            except ControlError as exc:
                signature_ok = False
                checks.append(check(exc.check_id, exc.status, exc.message))
            if bound and independent and checkpoint_review_ok and findings_ok and result_ok and signature_ok and review_tracked["status"] == "PASS" and transcript_ok["status"] == "PASS" and evidence_ref_checks_ok:
                valid_review = review; valid_review_path = review_path; derived_phase = "AUDITED"
        except ControlError as exc: checks.append(check(exc.check_id, exc.status, exc.message))
    elif PHASES.index(state["phase"]) >= PHASES.index("AUDITED"):
        checks.append(check("HC-REVIEW-REQUIRED", "FAIL", "audited-or-higher state lacks valid review"))
    decisions = sorted(p["decisions"].glob("*.json")); valid_decision = None; valid_decision_path: Path | None = None
    if decisions and derived_phase == "AUDITED":
        try:
            acceptance_finding_checks = review_finding_checks(root, valid_review, load_json(p["key_objectives"]), "ACCEPTED", contract) if valid_review else [check("HC-REVIEW-REQUIRED", "BLOCKED", "acceptance requires a valid review")]
            checks.extend(acceptance_finding_checks)
            acceptance_findings_ok = all(item["status"] == "PASS" for item in acceptance_finding_checks)
            decision_path = decisions[-1]; decision = load_json(decision_path); validate_object("approval-signature", decision)
            decision_tracked = verify_ref(root, content_ref(root, decision_path), "HC-DECISION-TRACKED"); checks.append(decision_tracked)
            checkpoint_decision_checks = owner_checkpoint_checks(decision, contract)
            checks.extend(checkpoint_decision_checks)
            checkpoint_decision_ok = all(item["status"] == "PASS" for item in checkpoint_decision_checks)
            bound = decision["taskId"] == contract["taskId"] and decision["candidateId"] == candidate["candidateId"] and decision["candidateCommit"] == candidate["commit"] and decision["checkpointSetSha256"] == candidate["checkpointSetSha256"] and ref_key(decision["positioning"]) == ref_key(candidate["positioning"]) and ref_key(decision["resolvedRuleSet"]) == ref_key(candidate["resolvedRuleSet"]) and sorted(decision["scope"]) == sorted(candidate["changedPaths"])
            expired = bool(decision.get("expiresAt") and dt.datetime.fromisoformat(decision["expiresAt"]) <= dt.datetime.now(dt.timezone.utc).astimezone())
            signature_ok = True
            try:
                signed = verify_record_signature(decision, lock, "owner", decision["owner"]["actorId"], required=external_release_crypto, check_id="HC-OWNER-SIGNATURE")
                checks.append(check("HC-OWNER-SIGNATURE" if signed else "HC-DECISION-ATTESTATION", "PASS", "approval signature binds trusted owner" if signed else "tracked owner decision is an explicit non-cryptographic attestation"))
            except ControlError as exc:
                signature_ok = False
                checks.append(check(exc.check_id, exc.status, exc.message))
            checks.append(check("HC-DECISION-REQUIRED", "PASS" if bound and not expired else ("INVALIDATED" if expired else "FAIL"), "decision binds candidate and scope" if bound and not expired else "decision mismatch or expired"))
            if bound and checkpoint_decision_ok and not expired and signature_ok and decision_tracked["status"] == "PASS" and acceptance_findings_ok: valid_decision = decision; valid_decision_path = decision_path; derived_phase = "ACCEPTED"
        except ControlError as exc: checks.append(check(exc.check_id, exc.status, exc.message))
    elif PHASES.index(state["phase"]) >= PHASES.index("ACCEPTED"):
        checks.append(check("HC-DECISION-REQUIRED", "FAIL", "accepted-or-higher state lacks valid decision"))
    if external_release_crypto:
        receipt_results = receipt_checks(p, lock, contract, candidate_path, candidate, valid_review_path, valid_review, valid_decision_path, valid_decision, list(evidence_refs_by_case.values()), executors)
        receipt_ok = next(item for item in receipt_results if item["id"] == "HC-RELEASE-RECEIPT")["status"] == "PASS"
    else:
        receipt_results = [check("HC-R3-RECEIPT-NOT-APPLICABLE", "PASS", "external R3 release receipt is not required by this project release path")]
        receipt_ok = True
    checks.extend(receipt_results)
    if lock["packageMode"] == "DEVELOPMENT" and PHASES.index(derived_phase) > PHASES.index("CANDIDATE_FROZEN"):
        derived_phase = "CANDIDATE_FROZEN"
    if lock["packageMode"] == "DEVELOPMENT":
        checks.append(check("HC-DEVELOPMENT-PACKAGE-CLAIM-CAP", "BLOCKED", "development package cannot derive VERIFIED, AUDITED, ACCEPTED, or RELEASE_READY"))
    review_gate_ok = contract["risk"] not in {"R2", "R3"} or PHASES.index(derived_phase) >= PHASES.index("AUDITED")
    checks.append(check("HC-RISK-REVIEW-GATE", "PASS" if review_gate_ok else "BLOCKED", "risk-level review prerequisite is closed" if review_gate_ok else "R2/R3 formal eligibility requires an independent review"))
    if valid_decision and receipt_ok and state["phase"] == "RELEASE_READY": derived_phase = "RELEASE_READY"
    phase_claim = {"DRAFT":"DIAGNOSTIC","CONTRACT_LOCKED":"DEVELOPMENT_CHECKED","IMPLEMENTING":"DEVELOPMENT_CHECKED","CANDIDATE_FROZEN":"DEVELOPMENT_CHECKED","VERIFIED":"VERIFIED","AUDITED":"VERIFIED","ACCEPTED":"ACCEPTED","RELEASE_READY":"RELEASE_READY"}[derived_phase]
    case_ceiling = min((CLAIMS.index(get_case(catalog, case_id)["maxClaimLevel"]) for case_id in contract["requiredCaseIds"]), default=0)
    package_cap = "DEVELOPMENT_CHECKED" if lock["packageMode"] == "DEVELOPMENT" else "RELEASE_READY"
    ceiling = CLAIMS[min(CLAIMS.index(phase_claim), CLAIMS.index(contract["maxClaimLevel"]), case_ceiling, CLAIMS.index(intent_cap), CLAIMS.index(package_cap))]
    checks.append(check("HC-CLAIM-TASK-CEILING", "PASS" if CLAIMS.index(state["claimLevel"]) <= CLAIMS.index(contract["maxClaimLevel"]) else "FAIL", "declared claim is within task ceiling" if CLAIMS.index(state["claimLevel"]) <= CLAIMS.index(contract["maxClaimLevel"]) else "declared claim exceeds task ceiling"))
    checks.append(check("HC-RELEASE-INTENT-CEILING", "PASS" if CLAIMS.index(state["claimLevel"]) <= CLAIMS.index(intent_cap) else "FAIL", "declared claim is within project release intent" if CLAIMS.index(state["claimLevel"]) <= CLAIMS.index(intent_cap) else "declared claim exceeds project release intent"))
    can_advance = state["phase"] in PHASES and derived_phase in PHASES and PHASES.index(derived_phase) == PHASES.index(state["phase"]) + 1
    prior_hard = [item for item in checks if item["status"] in {"FAIL", "INVALIDATED"}]
    if can_advance and not prior_hard and clean_status(root):
        can_advance = False
    declared_state = state
    effective_state = state
    projected_transition = False
    if can_advance and not prior_hard:
        if mutate_state:
            effective_state = transition(
                p,
                derived_phase,
                "CLEAR",
                ceiling,
                "state derived from closed fact objects",
            )
            declared_state = effective_state
        else:
            projected_transition = True
            effective_state = {
                **state,
                "phase": derived_phase,
                "health": "CLEAR",
                "claimLevel": ceiling,
            }
    state_matches = (
        effective_state["phase"] == derived_phase
        and effective_state["claimLevel"] == ceiling
    )
    checks.append(check(
        "HC-STATE-DERIVED-MISMATCH",
        "PASS" if state_matches else "FAIL",
        "declared state equals derived state"
        if state_matches and not projected_transition
        else "read-only projection identifies the next legal derived state"
        if state_matches
        else "manual/stale state differs from derived facts",
        declaredPhase=declared_state["phase"],
        derivedPhase=derived_phase,
        declaredClaim=declared_state["claimLevel"],
        derivedClaim=ceiling,
        readOnlyProjection=not mutate_state,
        projectedTransition=projected_transition,
    ))
    dirty = clean_status(root); checks.append(check("HC-WORKTREE-CLEAN", "PASS" if not dirty else "BLOCKED", "worktree is clean" if not dirty else "formal claims require a clean worktree", entries=dirty))
    priorities = {"PASS":0,"BLOCKED":1,"INVALIDATED":2,"FAIL":3}; status = max((item["status"] for item in checks), key=lambda x: priorities[x])
    eligible = status == "PASS" and lock["packageMode"] == "SEALED" and receipt_ok and effective_state["health"] == "CLEAR" and review_gate_ok
    blockers = [item["id"] for item in checks if item["status"] != "PASS"]
    reported_ceiling = ceiling if lock["packageMode"] == "SEALED" else "DEVELOPMENT_CHECKED"
    return envelope(status=status, checks=checks, formal={"eligible": eligible, "maxClaimLevel": reported_ceiling, "blockers": blockers}, state={"declared": declared_state, "derived": {"phase": derived_phase, "health": "CLEAR" if status == "PASS" else ("FAILED" if status == "FAIL" else "BLOCKED"), "claimLevel": ceiling}}, data={"packageMode": lock["packageMode"], "releaseIntent": lock["releaseIntent"], "releaseIntentMaxClaimLevel": intent_cap, "deliveryObjective": positioning["deliveryObjective"], "externalReleaseCryptoRequired": external_release_crypto, "warnings": resolved["warnings"], "investigations": resolved["investigations"]})


def release_check(project: Path) -> dict[str, Any]:
    report = validate(project)
    try:
        p = paths(project); state, lock, _, _, contract = current_objects(p)
    except ControlError:
        return report
    if lock["packageMode"] == "DEVELOPMENT":
        if "HC-DEVELOPMENT-PACKAGE-CLAIM-CAP" not in report["formal"]["blockers"]:
            report["formal"]["blockers"].append("HC-DEVELOPMENT-PACKAGE-CLAIM-CAP")
        report["status"] = "BLOCKED" if report["status"] == "PASS" else report["status"]
        report["formal"]["eligible"] = False
        report["formal"]["maxClaimLevel"] = "DEVELOPMENT_CHECKED"
        report.setdefault("data", {})["packageMode"] = "DEVELOPMENT"
        return report
    intent = lock["releaseIntent"]
    if intent == "LOCAL_EXPERIMENT":
        if "HC-RELEASE-INTENT-LOCAL" not in report["formal"]["blockers"]:
            report["formal"]["blockers"].append("HC-RELEASE-INTENT-LOCAL")
        report["status"] = "BLOCKED" if report["status"] == "PASS" else report["status"]
        report["formal"]["eligible"] = False
        report.setdefault("data", {})["releaseIntent"] = intent
        return report
    if intent == "PRIVATE_OPERATION":
        if report["status"] == "PASS" and report["formal"]["eligible"] and report["formal"]["maxClaimLevel"] == "ACCEPTED":
            report.setdefault("data", {})["releaseIntent"] = intent
            report["data"]["privateOperationReady"] = True
            return report
        if "HC-PRIVATE-OPERATION-PREREQUISITES" not in report["formal"]["blockers"]:
            report["formal"]["blockers"].append("HC-PRIVATE-OPERATION-PREREQUISITES")
        report["status"] = "BLOCKED" if report["status"] == "PASS" else report["status"]
        report["formal"]["eligible"] = False
        report.setdefault("data", {})["releaseIntent"] = intent
        return report
    if contract["maxClaimLevel"] != "RELEASE_READY":
        if "HC-RELEASE-TASK-CEILING" not in report["formal"]["blockers"]:
            report["formal"]["blockers"].append("HC-RELEASE-TASK-CEILING")
        report["status"] = "BLOCKED" if report["status"] == "PASS" else report["status"]
        report["formal"]["eligible"] = False
        report.setdefault("data", {})["releaseIntent"] = intent
        return report
    if contract["risk"] != "R3":
        if "HC-EXTERNAL-RELEASE-R3" not in report["formal"]["blockers"]:
            report["formal"]["blockers"].append("HC-EXTERNAL-RELEASE-R3")
        report["status"] = "BLOCKED" if report["status"] == "PASS" else report["status"]
        report["formal"]["eligible"] = False
        report.setdefault("data", {})["releaseIntent"] = intent
        return report
    if report["status"] == "PASS" and report["formal"]["eligible"] and report["formal"]["maxClaimLevel"] == "RELEASE_READY":
        return report
    if report["status"] != "PASS" or not report["formal"]["eligible"] or report["formal"]["maxClaimLevel"] != "ACCEPTED":
        if "HC-RELEASE-PREREQUISITES" not in report["formal"]["blockers"]: report["formal"]["blockers"].append("HC-RELEASE-PREREQUISITES")
        report["status"] = "BLOCKED" if report["status"] == "PASS" else report["status"]
        report["formal"]["eligible"] = False
        return report
    if state["phase"] == "ACCEPTED":
        new_state = transition(p, "RELEASE_READY", "CLEAR", "RELEASE_READY", "release prerequisites verified")
        report = validate(project)
        report["state"] = {"declared": new_state, "derived": report.get("state", {}).get("derived", new_state)}
    return report


def handoff(project: Path, output: Path | None) -> dict[str, Any]:
    p = paths(project); report = validate(project); state = load_json(p["state"])
    checkpoint_hash = None
    if state.get("taskId"):
        task_lock_path = p["task_locks"] / f"{state['taskId']}.json"
        if task_lock_path.is_file():
            checkpoint_hash = load_json(task_lock_path).get("checkpointSetSha256")
    evidence_ids = [load_json(path).get("evidenceId") for path in p["evidence"].glob("*.json") if not path.name.endswith(("attestation.json", "adapter-invocation.json"))]
    reviews = sorted(p["reviews"].glob("*.json")); decisions = sorted(p["decisions"].glob("*.json"))
    value = {"schemaVersion":SCHEMA_VERSION,"handoffId":f"handoff-{uuid.uuid4().hex[:12]}","taskId":state.get("taskId") or "NONE","candidateId":state.get("candidateId"),"checkpointSetSha256":checkpoint_hash,"positioningId":state["positioningId"],"ruleSetId":state["ruleSetId"],"phase":state["phase"],"health":state["health"],"claimLevel":state["claimLevel"],"evidenceIds":evidence_ids,"reviewId":load_json(reviews[-1])["reviewId"] if reviews else None,"decisionId":load_json(decisions[-1])["decisionId"] if decisions else None,"blockers":report["formal"]["blockers"],"createdAt":now_iso()}
    lock = load_json(p["lock"])
    if lock.get("automationPolicy") is not None:
        value["automationPolicy"] = lock["automationPolicy"]
    validate_object("handoff", value); target = output or p["handoffs"] / f"{value['handoffId']}.json"; write_json_atomic(target, value)
    return envelope(status=report["status"], checks=report["integrity"]["checks"], formal=report["formal"], state=report["state"], data={"handoff":str(target)})


def revise_objectives_plan(project: Path, spec_path: Path) -> dict[str, Any]:
    assert_dependencies(); p = paths(project); _guard_v3_control_plane(p); root = git_root(p["root"])
    current = load_json(p["key_objectives"]); validate_object("key-objectives-lock", current)
    spec = load_json(spec_path.resolve()); validate_object("key-objectives-revision", spec)
    lock = load_json(p["lock"]); validate_object("project-governance-lock", lock)
    if spec["projectId"] != lock["projectId"]:
        raise ControlError("HC-OBJECTIVES-PROJECT-IDENTITY", "objective revision targets a different project")
    proposed = _key_objectives_from_spec(root, spec)
    if proposed["revision"] <= current["revision"]:
        raise ControlError("HC-OBJECTIVES-REVISION", "objective revision must increase monotonically")
    current_ids = set().union(*_key_objective_id_sets(current))
    proposed_ids = set().union(*_key_objective_id_sets(proposed))
    invalidates = ["task-lock", "candidate", "execution-evidence", "review", "decision", "handoff", "release-receipt"]
    plan = {
        "schemaVersion": SCHEMA_VERSION, "operation": "revise-objectives", "projectId": lock["projectId"],
        "from": {"documentId": current["documentId"], "revision": current["revision"], "lockSha256": sha256_file(p["key_objectives"])},
        "to": {"documentId": proposed["documentId"], "revision": proposed["revision"], "documentSha256": proposed["document"]["sha256"]},
        "addedIds": sorted(proposed_ids - current_ids), "removedIds": sorted(current_ids - proposed_ids),
        "invalidates": invalidates, "specSha256": sha256_file(spec_path.resolve()), "governanceCost": "one re-lock plus complete downstream evidence regeneration",
    }
    plan["planHash"] = sha256_bytes(canonical_bytes(plan))
    return envelope(status="BLOCKED", checks=[*key_objective_checks(root, proposed), check("HC-OBJECTIVES-REVISION-APPROVAL", "BLOCKED", "apply requires the exact plan hash after one consolidated human confirmation")], data=plan)


def revise_objectives_apply(project: Path, spec_path: Path, plan_hash: str) -> dict[str, Any]:
    p = paths(project); root = git_root(p["root"])
    planned = revise_objectives_plan(project, spec_path)["data"]
    if planned["planHash"] != plan_hash:
        raise ControlError("HC-OBJECTIVES-PLAN-HASH", "objective revision plan hash mismatch", status="INVALIDATED")
    spec = load_json(spec_path.resolve()); proposed = _key_objectives_from_spec(root, spec)
    allowed = {spec["keyObjectives"]["document"].replace("\\", "/"), spec["keyObjectives"]["confirmation"]["record"].replace("\\", "/"), *(item.replace("\\", "/") for item in spec["keyObjectives"]["sourceDocuments"])}
    try:
        allowed.add(spec_path.resolve().relative_to(root).as_posix())
    except ValueError:
        pass
    dirty_paths = []
    for entry in clean_status(root):
        value = entry[3:].split(" -> ")[-1].replace("\\", "/") if len(entry) >= 4 else entry
        if value not in allowed:
            dirty_paths.append(value)
    if dirty_paths:
        raise ControlError("HC-OBJECTIVES-REVISION-SCOPE", "objective revision worktree contains unrelated changes", status="BLOCKED", details={"paths": dirty_paths})
    legacy = p["legacy"] / f"objectives-r{planned['from']['revision']}-{now_iso().replace(':', '-')}"
    legacy.mkdir(parents=True)
    shutil.copy2(p["key_objectives"], legacy / "key-objectives-lock.json")
    for name in ("task-locks", "candidates", "evidence", "reviews", "decisions", "external-audits", "handoffs"):
        source = p["control"] / name
        if source.exists():
            shutil.move(str(source), str(legacy / name))
    release_receipt = p["runtime"] / "release-receipt.json"
    if release_receipt.exists():
        shutil.move(str(release_receipt), str(legacy / "release-receipt.json"))
    write_json_atomic(p["key_objectives"], proposed)
    lock = load_json(p["lock"])
    lock.update({"keyObjectives": content_ref(root, p["key_objectives"]), "lockedAt": now_iso()})
    validate_object("project-governance-lock", lock); write_json_atomic(p["lock"], lock)
    revision_record = {**planned, "appliedAt": now_iso(), "legacy": legacy.relative_to(root).as_posix()}
    record_path = p["objective_revisions"] / f"revision-{proposed['revision']}-{plan_hash[:12]}.json"
    write_json_atomic(record_path, revision_record)
    positioning = load_json(p["positioning"]); resolved = load_json(p["resolved_rules"])
    state = initial_state(lock["projectId"], positioning["positioningId"], resolved["ruleSetId"]); write_json_atomic(p["state"], state)
    return envelope(status="BLOCKED", checks=[check("HC-OBJECTIVES-INVALIDATION", "BLOCKED", "objectives changed; downstream facts were archived and cannot be inherited")], formal={"eligible": False, "maxClaimLevel": "DIAGNOSTIC", "blockers": ["HC-OBJECTIVES-INVALIDATION"]}, state={"declared": state, "derived": state}, data={"revisionRecord": str(record_path), "legacy": str(legacy), "invalidated": planned["invalidates"], "next": "commit and create a fresh task contract"})


def reposition_plan(project: Path, spec_path: Path) -> dict[str, Any]:
    assert_dependencies(); p = paths(project); _guard_v3_control_plane(p); root = git_root(p["root"])
    lock = load_json(p["lock"]); validate_object("project-governance-lock", lock)
    current = load_json(p["positioning"]); validate_object("project-positioning", current)
    proposed = load_json(spec_path.resolve()); validate_object("project-positioning", proposed)
    checks = verify_positioning(root, proposed)
    current_inputs = load_json(p["rule_inputs"])
    updated_inputs = dict(current_inputs)
    for key in positioning_summary(proposed): updated_inputs[key] = proposed[key]
    updated_inputs["confirmation"] = {**proposed["confirmation"], "record": proposed["confirmation"]["record"]["path"]}
    compiled = compile_for_project(updated_inputs, root, p["runtime"], expected_runtime_manifest_sha256=lock["runtime"]["sha256"]); checks.extend(compiler_checks(compiled))
    catalog = load_json(p["cases"]); validate_object("case-catalog", catalog); checks.append(coverage_check(compiled, catalog["cases"]))
    invalidates = ["task-lock", "candidate", "execution-evidence", "review", "decision", "release-receipt", "handoff"]
    plan = {
        "schemaVersion": SCHEMA_VERSION, "operation": "reposition", "projectId": current_inputs["projectId"],
        "fromPositioningId": current["positioningId"], "toPositioningId": proposed["positioningId"],
        "fromSummarySha256": sha256_bytes(canonical_bytes(positioning_summary(current))),
        "toSummarySha256": sha256_bytes(canonical_bytes(positioning_summary(proposed))),
        "newCanonicalRuleSetSha256": compiled["canonicalSha256"], "invalidates": invalidates,
        "specPath": spec_path.resolve().relative_to(root).as_posix() if spec_path.resolve().is_relative_to(root) else str(spec_path.resolve()),
    }
    plan["planHash"] = sha256_bytes(canonical_bytes(plan))
    fail_on_compile_issues(checks)
    return envelope(status="BLOCKED", checks=[*checks, check("HC-REPOSITION-APPROVAL", "BLOCKED", "apply requires the exact plan hash and the same confirmed positioning spec")], data=plan)


def reposition_apply(project: Path, spec_path: Path, plan_hash: str) -> dict[str, Any]:
    p = paths(project); root = git_root(p["root"])
    _guard_v3_control_plane(p)
    lock = load_json(p["lock"]); validate_object("project-governance-lock", lock)
    if clean_status(root): raise ControlError("HC-WORKTREE-CLEAN", "reposition apply requires a clean worktree", status="BLOCKED")
    planned = reposition_plan(project, spec_path)["data"]
    if planned["planHash"] != plan_hash:
        raise ControlError("HC-REPOSITION-PLAN-HASH", "reposition plan hash mismatch", status="INVALIDATED")
    proposed = load_json(spec_path.resolve()); current_inputs = load_json(p["rule_inputs"])
    updated_inputs = dict(current_inputs)
    for key in positioning_summary(proposed): updated_inputs[key] = proposed[key]
    updated_inputs["confirmation"] = {**proposed["confirmation"], "record": proposed["confirmation"]["record"]["path"]}
    compiled = compile_for_project(updated_inputs, root, p["runtime"], expected_runtime_manifest_sha256=lock["runtime"]["sha256"]); fail_on_compile_issues(compiler_checks(compiled))
    legacy = p["legacy"] / f"reposition-{now_iso().replace(':', '-')}"; legacy.mkdir(parents=True)
    for name in ("task-locks", "candidates", "evidence", "reviews", "decisions", "external-audits", "handoffs"):
        source = p["control"] / name
        if source.exists(): shutil.move(str(source), str(legacy / name))
    write_json_atomic(p["positioning"], proposed); write_json_atomic(p["rule_inputs"], updated_inputs)
    resolved = _resolved_rule_object(root, p, proposed, compiled); write_json_atomic(p["resolved_rules"], resolved)
    lock.update({
        "positioning": content_ref(root, p["positioning"]), "resolvedRuleSet": content_ref(root, p["resolved_rules"]),
        "ruleInputs": content_ref(root, p["rule_inputs"]), "releaseIntent": proposed["releaseIntent"], "lockedAt": now_iso(),
    }); validate_object("project-governance-lock", lock); write_json_atomic(p["lock"], lock)
    state = initial_state(lock["projectId"], proposed["positioningId"], resolved["ruleSetId"]); write_json_atomic(p["state"], state)
    return envelope(status="BLOCKED", checks=[check("HC-REPOSITION-INVALIDATION", "BLOCKED", "positioning applied; every downstream object was archived as diagnostic-only")], state={"declared": state, "derived": state}, data={"legacy": str(legacy), "invalidated": planned["invalidates"], "next": "commit, then create a fresh task contract"})


def _migration_source(p: dict[str, Path]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    if not p["control"].is_dir():
        raise ControlError("VC-CONTROL-PLANE-MISSING", ".vibe-control does not exist", status="BLOCKED")
    lock = load_json(p["lock"]); state = load_json(p["state"]); positioning = load_json(p["positioning"]); catalog = load_json(p["cases"])
    versions = {value.get("schemaVersion") for value in (lock, state, positioning, catalog) if isinstance(value, dict)}
    if versions != {"3.1"}:
        raise ControlError("HC-MIGRATION-SOURCE-VERSION", "3.1 to 3.2 migration requires one consistent Schema 3.1 control plane", status="BLOCKED", details={"observed": sorted(str(value) for value in versions)})
    root = p["root"]
    objective_probe = {**load_json(p["key_objectives"]), "schemaVersion": SCHEMA_VERSION}
    source_checks = key_objective_checks(root, objective_probe)
    source_checks.extend(verify_ref(root, ref, f"HC-MIGRATION-AUTHORITY-{index + 1}") for index, ref in enumerate(lock.get("authorityFiles", [])))
    source_checks.append(verify_ref(root, positioning["confirmation"]["record"], "HC-MIGRATION-POSITIONING-CONFIRMATION"))
    drift = [item for item in source_checks if item["status"] != "PASS"]
    if drift:
        raise ControlError("HC-MIGRATION-SOURCE-DRIFT", "Schema 3.1 authority or confirmation bytes drifted before migration", status="INVALIDATED", details=drift)
    files = []
    for path in sorted((value for value in p["control"].rglob("*") if value.is_file()), key=lambda item: item.relative_to(p["control"]).as_posix()):
        files.append({"path": path.relative_to(p["control"]).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return lock, state, positioning, catalog, files


def _migration_conversion(positioning: dict[str, Any], catalog: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, Any]]]:
    signals = [{"id": statement_id("SIG", value), "statement": normalize_statement(value)} for value in positioning["firstVerticalSlice"]["successSignals"]]
    gates = [{"id": statement_id("HG", value), "statement": normalize_statement(value)} for value in positioning["humanQualityGates"]]
    validate_statement_objects(signals, "SIG")
    validate_statement_objects(gates, "HG")
    converted_cases = []
    for item in catalog["cases"]:
        oracle = item["oracle"]
        converted_cases.append({
            **item,
            "oracle": {
                "exitCode": oracle["exitCode"],
                "stdoutContainsAll": [oracle["stdoutContains"]] if oracle.get("stdoutContains") else [],
                "stderrContainsNone": [],
            },
            "artifacts": [{"path": value, "minBytes": 1} for value in item.get("artifacts", [])],
        })
    return signals, gates, converted_cases


def _migration_spec_checks(
    spec: dict[str, Any], lock: dict[str, Any], signals: list[dict[str, str]], gates: list[dict[str, str]],
    positioning: dict[str, Any], cases: list[dict[str, Any]], objective_lock: dict[str, Any],
) -> None:
    validate_object("migration-spec", spec)
    if spec["projectId"] != lock["projectId"]:
        raise ControlError("HC-MIGRATION-PROJECT-IDENTITY", "migration spec targets a different project")
    if spec["successSignals"] != signals or spec["humanQualityGates"] != gates:
        raise ControlError("HC-MIGRATION-ID-STABILITY", "migration spec changed deterministic signal/gate IDs or normalized text")
    payload = {"successSignals": signals, "humanQualityGates": gates, "checkpointDrafts": spec["checkpointDrafts"]}
    expected_summary = sha256_bytes(canonical_bytes(payload))
    if spec["confirmation"]["summarySha256"] != expected_summary:
        raise ControlError("HC-MIGRATION-CONFIRMATION", "migration confirmation does not bind the complete converted summary", details={"expected": expected_summary, "actual": spec["confirmation"]["summarySha256"]})
    migrated_positioning = {**positioning, "schemaVersion": SCHEMA_VERSION, "humanQualityGates": gates, "firstVerticalSlice": {**positioning["firstVerticalSlice"], "successSignals": signals}}
    draft_objectives = sorted({ref for item in spec["checkpointDrafts"] for ref in item.get("objectiveRefs", [])})
    max_index = max((CLAIMS.index(item.get("requiredForClaim", "DIAGNOSTIC")) for item in spec["checkpointDrafts"]), default=0)
    draft_contract = {
        "schemaVersion": SCHEMA_VERSION, "taskId": "MIGRATION-DRAFT", "goal": "validate migrated checkpoint draft",
        "objectiveRefs": draft_objectives, "allowedPaths": ["**"], "forbiddenPaths": [],
        "requiredCaseIds": [item["id"] for item in cases], "risk": "R2", "maxClaimLevel": CLAIMS[max_index],
        "authorityRefs": [], "acceptanceCheckpoints": spec["checkpointDrafts"],
        "checkpointConfirmation": {"actorId": spec["confirmation"]["actorId"], "summary": spec["confirmation"]["summary"], "checkpointSetSha256": "0" * 64, "record": "MIGRATION-SPEC.json", "confirmedAt": spec["confirmation"]["confirmedAt"]},
        "auditPolicy": AUDIT_POLICY,
    }
    draft_contract["checkpointConfirmation"]["checkpointSetSha256"] = checkpoint_set_sha256(draft_contract)
    validate_object("task-contract", draft_contract)
    assert_objective_refs(draft_contract, objective_lock)
    checkpoint_contract_checks(draft_contract, migrated_positioning, {"cases": cases}, RELEASE_INTENT_CAPS[positioning["releaseIntent"]])


def migration_plan(project: Path, spec_path: Path | None = None) -> dict[str, Any]:
    assert_dependencies(); p = paths(project); root = git_root(p["root"])
    lock, _, positioning, catalog, source_files = _migration_source(p)
    signals, gates, converted_cases = _migration_conversion(positioning, catalog)
    spec = None; spec_sha = None; checkpoint_drafts: list[dict[str, Any]] = []
    unresolved = [f"map {item['id']} to one task checkpoint" for item in signals] + [f"map {item['id']} to one HUMAN checkpoint when ACCEPTED applies" for item in gates]
    if spec_path is not None:
        spec_path = spec_path.resolve(); spec = load_json(spec_path)
        objective_lock = load_json(p["key_objectives"])
        _migration_spec_checks(spec, lock, signals, gates, positioning, converted_cases, objective_lock)
        spec_sha = sha256_file(spec_path); checkpoint_drafts = spec["checkpointDrafts"]; unresolved = []
    source_sha = sha256_bytes(canonical_bytes(source_files))
    base = {
        "schemaVersion": SCHEMA_VERSION,
        "planId": f"schema-3.2-{source_sha[:12]}", "project": str(root), "projectId": lock["projectId"],
        "fromSchemaVersion": "3.1", "toSchemaVersion": SCHEMA_VERSION, "toVersion": VERSION,
        "sourceSnapshotSha256": source_sha, "specSha256": spec_sha,
        "signalConversions": signals, "humanGateConversions": gates,
        "caseConversions": [{"caseId": item["id"], "oracle": item["oracle"], "artifacts": item.get("artifacts", [])} for item in converted_cases],
        "checkpointDrafts": checkpoint_drafts, "unresolvedMappings": unresolved,
        "actions": ["archive-schema-3.1", f"install-runtime-{VERSION}", "convert-positioning-sources", "convert-case-oracles", "invalidate-downstream", "reset-diagnostic-state"],
        "invalidates": ["task", "candidate", "evidence", "review", "decision", "receipt", "handoff"], "risk": "R2",
    }
    base["planHash"] = sha256_bytes(canonical_bytes(base)); validate_object("migration-plan", base)
    blocker = "HC-MIGRATION-APPROVAL" if spec else "HC-MIGRATION-SPEC-REQUIRED"
    message = "apply requires the exact content-bound plan hash" if spec else "review deterministic IDs, provide checkpoint mappings, and confirm one migration spec"
    return envelope(status="BLOCKED", checks=[check(blocker, "BLOCKED", message)], data=base)


def _staged_ref(staging: Path, relative: str) -> dict[str, Any]:
    path = staging / relative
    if not path.is_file():
        raise ControlError("HC-MIGRATION-STAGING", f"staged file is missing: {relative}")
    return {"path": f".vibe-control/{relative}", "bytes": path.stat().st_size, "sha256": sha256_file(path), "tracked": True}


def _archive_manifest(snapshot: Path) -> dict[str, Any]:
    files = []
    for path in sorted((value for value in snapshot.rglob("*") if value.is_file()), key=lambda item: item.relative_to(snapshot).as_posix()):
        files.append({"path": path.relative_to(snapshot).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return {"schemaVersion": SCHEMA_VERSION, "hashAlgorithm": "sha256", "files": files, "snapshotSha256": sha256_bytes(canonical_bytes(files))}


def _atomic_replace_control_plane(live: Path, staging: Path, backup: Path) -> None:
    """Swap a fully validated control plane and restore the old tree on failure."""
    os.replace(live, backup)
    try:
        os.replace(staging, live)
    except Exception:
        os.replace(backup, live)
        raise
    # The live swap is complete at this point. A locked/antivirus-held backup
    # is recoverable residue, not a reason to misreport the validated live tree
    # as rolled back.
    shutil.rmtree(backup, ignore_errors=True)


def migration_apply(project: Path, plan_hash: str, spec_path: Path) -> dict[str, Any]:
    p = paths(project); root = git_root(p["root"]); spec_path = spec_path.resolve()
    if clean_status(root):
        raise ControlError("HC-WORKTREE-CLEAN", "migration apply requires a clean worktree", status="BLOCKED")
    # A confirmed migration spec is itself a durable, tracked human record.
    spec_ref = file_ref(root, spec_path)
    planned = migration_plan(project, spec_path)["data"]
    if planned["planHash"] != plan_hash:
        raise ControlError("HC-MIGRATION-PLAN-HASH", "migration plan hash mismatch", status="INVALIDATED")
    lock, _, positioning, catalog, _ = _migration_source(p)
    signals, gates, converted_cases = _migration_conversion(positioning, catalog)
    spec = load_json(spec_path)
    skill_root = runtime_root().parents[2]
    package_release = validate_development_package(skill_root)
    if package_release.get("status") != "PASS":
        raise ControlError("HC-DEVELOPMENT-PACKAGE-INTEGRITY", f"migration requires an integrity-checked {VERSION} development package", status="BLOCKED", details=package_release.get("blockers", []))

    staging = root / f".vibe-control.migrate-{plan_hash[:12]}.tmp"
    backup = root / f".vibe-control.migrate-{plan_hash[:12]}.backup"
    if staging.exists() or backup.exists():
        raise ControlError("HC-MIGRATION-STAGING", "migration staging or backup path already exists", status="BLOCKED")
    try:
        shutil.copytree(p["control"], staging)
        archive_root = staging / "legacy" / "schema-3.1" / plan_hash
        snapshot = archive_root / "control-plane"
        shutil.copytree(p["control"], snapshot)
        write_json_atomic(archive_root / "manifest.json", _archive_manifest(snapshot))

        for name in ("tasks", "task-locks", "candidates", "evidence", "reviews", "decisions", "external-audits", "handoffs"):
            target = staging / name
            if target.exists():
                shutil.rmtree(target)
        old_receipt = staging / "runtime" / "0.3.2" / "release-receipt.json"
        if old_receipt.exists():
            old_receipt.unlink()

        new_runtime = staging / "runtime" / VERSION
        copy_runtime_bundle(new_runtime)
        write_evidence_byte_policy(staging / ".gitattributes")
        governance = staging / "governance"; governance.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill_root / "package-manifest.json", governance / "package-manifest.json")
        shutil.copy2(skill_root / "references" / "controller-assurance-matrix.json", governance / "controller-assurance-matrix.json")

        key_objectives = load_json(staging / "key-objectives-lock.json"); key_objectives["schemaVersion"] = SCHEMA_VERSION
        validate_object("key-objectives-lock", key_objectives); write_json_atomic(staging / "key-objectives-lock.json", key_objectives)
        migrated_catalog = {"schemaVersion": SCHEMA_VERSION, "catalogId": catalog["catalogId"], "cases": converted_cases}
        validate_object("case-catalog", migrated_catalog); write_json_atomic(staging / "case-catalog.json", migrated_catalog)

        migrated_positioning = {**positioning, "schemaVersion": SCHEMA_VERSION, "humanQualityGates": gates, "firstVerticalSlice": {**positioning["firstVerticalSlice"], "successSignals": signals}}
        migrated_positioning["positioningId"] = f"positioning-{lock['projectId']}-{sha256_bytes(canonical_bytes(positioning_summary(migrated_positioning)))[:12]}"
        migrated_positioning["confirmation"] = {"actorId": spec["confirmation"]["actorId"], "summary": spec["confirmation"]["summary"], "summarySha256": sha256_bytes(canonical_bytes(positioning_summary(migrated_positioning))), "record": spec_ref}
        migrated_positioning["confirmedAt"] = spec["confirmation"]["confirmedAt"]
        validate_object("project-positioning", migrated_positioning); positioning_checkpoint_source_checks(migrated_positioning)
        write_json_atomic(staging / "project-positioning.json", migrated_positioning)

        rule_inputs = load_json(staging / "rule-inputs.json"); rule_inputs.update({"schemaVersion": SCHEMA_VERSION, "humanQualityGates": gates, "firstVerticalSlice": migrated_positioning["firstVerticalSlice"], "confirmation": {**migrated_positioning["confirmation"], "record": spec_ref["path"]}})
        write_json_atomic(staging / "rule-inputs.json", rule_inputs)
        # The compiler is loaded from the staged runtime, not from this
        # controller's import path.  Bind that execution to the exact staged
        # manifest so the compatibility loader can distinguish an intentional
        # runtime copy from an unbound compiler substitution.
        compiled = compile_for_project(
            rule_inputs,
            root,
            new_runtime,
            expected_runtime_manifest_sha256=sha256_file(new_runtime / "runtime-manifest.json"),
        )
        fail_on_compile_issues(compiler_checks(compiled))
        resolved = {"schemaVersion": SCHEMA_VERSION, "ruleSetId": f"rules-{compiled['canonicalSha256'][:16]}", "positioning": _staged_ref(staging, "project-positioning.json"), "compiler": {"id": "vibe-control-project-rules", "version": VERSION, "sha256": sha256_file(new_runtime / "vibe_runtime" / "project_rules.py")}, "canonical": compiled["canonical"], "canonicalSha256": compiled["canonicalSha256"], "conflicts": compiled["conflicts"], "warnings": compiled["warnings"], "investigations": compiled["investigations"], "installRequests": compiled["installRequests"], "blockers": compiled["blockers"], "canApprove": False, "compiledAt": now_iso()}
        validate_object("resolved-rule-set", resolved); write_json_atomic(staging / "resolved-rule-set.json", resolved)

        for binding_path in (staging / "skill-bindings").glob("*.json") if (staging / "skill-bindings").is_dir() else []:
            binding = load_json(binding_path); binding["schemaVersion"] = SCHEMA_VERSION; validate_object("skill-binding", binding); write_json_atomic(binding_path, binding)
        binding = package_release["binding"]
        new_lock = {
            **lock, "schemaVersion": SCHEMA_VERSION, "lockId": f"lock-{lock['projectId']}-v32", "packageMode": "DEVELOPMENT",
            "packageBinding": {"version": binding["version"], "sourceKind": binding["sourceKind"], **({"commit": binding["commit"], "tree": binding["tree"]} if "commit" in binding and "tree" in binding else {}), "packageManifest": _staged_ref(staging, "governance/package-manifest.json"), "runtimeManifest": _staged_ref(staging, f"runtime/{VERSION}/runtime-manifest.json"), "assuranceMatrix": _staged_ref(staging, "governance/controller-assurance-matrix.json")},
            "skill": _staged_ref(staging, "governance/package-manifest.json"), "runtime": _staged_ref(staging, f"runtime/{VERSION}/runtime-manifest.json"),
            "keyObjectives": _staged_ref(staging, "key-objectives-lock.json"), "caseCatalog": _staged_ref(staging, "case-catalog.json"),
            "evidenceBytePolicy": _staged_ref(staging, ".gitattributes"),
            "ruleInputs": _staged_ref(staging, "rule-inputs.json"), "positioning": _staged_ref(staging, "project-positioning.json"), "resolvedRuleSet": _staged_ref(staging, "resolved-rule-set.json"),
            "ruleCompiler": _staged_ref(staging, f"runtime/{VERSION}/vibe_runtime/project_rules.py"), "profileDirectory": _staged_ref(staging, f"runtime/{VERSION}/rules/v1/profiles.json"), "adapterDirectory": _staged_ref(staging, f"runtime/{VERSION}/rules/v1/adapters.json"),
            "skillBindings": [_staged_ref(staging, path.relative_to(staging).as_posix()) for path in sorted((staging / "skill-bindings").glob("*.json"))] if (staging / "skill-bindings").is_dir() else [],
            "releaseIntent": migrated_positioning["releaseIntent"], "lockedAt": now_iso(),
        }
        new_lock.pop("packageAuditReceipt", None)
        validate_object("project-governance-lock", new_lock); write_json_atomic(staging / "project-governance-lock.json", new_lock)
        new_state = initial_state(lock["projectId"], migrated_positioning["positioningId"], resolved["ruleSetId"])
        validate_object("stage-state", new_state); write_json_atomic(staging / "stage-state.json", new_state)
        migration_record = {"schemaVersion": SCHEMA_VERSION, "plan": planned, "spec": spec_ref, "archiveManifest": _staged_ref(staging, f"legacy/schema-3.1/{plan_hash}/manifest.json"), "appliedAt": now_iso()}
        write_json_atomic(governance / f"migration-{plan_hash[:12]}.json", migration_record)

        # Validate the complete staged root before swapping a single live path.
        for name, kind in (("project-governance-lock.json", "project-governance-lock"), ("key-objectives-lock.json", "key-objectives-lock"), ("project-positioning.json", "project-positioning"), ("resolved-rule-set.json", "resolved-rule-set"), ("case-catalog.json", "case-catalog"), ("stage-state.json", "stage-state")):
            validate_object(kind, load_json(staging / name))
        _atomic_replace_control_plane(p["control"], staging, backup)
    except (OSError, shutil.Error) as exc:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise ControlError(
            "HC-MIGRATION-STAGING", f"migration staging failed before a complete replacement: {type(exc).__name__}",
            status="BLOCKED",
        ) from exc
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    return envelope(status="BLOCKED", checks=[check("HC-MIGRATION-INVALIDATION", "BLOCKED", "Schema 3.1 control objects were archived; no downstream claim was inherited")], formal={"eligible": False, "maxClaimLevel": "DIAGNOSTIC", "blockers": ["HC-MIGRATION-INVALIDATION"]}, state={"declared": new_state, "derived": new_state}, data={"planHash": plan_hash, "legacy": f".vibe-control/legacy/schema-3.1/{plan_hash}", "invalidated": planned["invalidates"], "next": "commit migration, then create and confirm a fresh Schema 3.2 task"})
