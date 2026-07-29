"""Controller-side Project Positioning and rule-closure helpers for Schema 3.2."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .common import ControlError, canonical_bytes, check, load_json, sha256_bytes, verify_ref
from .project_rules import compile_positioning
from .schema import validate_object


_COMPATIBLE_RULE_COMPILERS = {
    ("0.3.4", "6152ee606ab1292327df94474d1b6b0eb14a080a00f6622d2e0cd39bc067b293"),
    ("0.3.7", "95badd00860946382ed3ff9fa737ff91e1f925c63cbc0282ae9fbfebd82a9055"),
}
_COMPILER_PATH = "vibe_runtime/project_rules.py"
_RULE_CATALOG_PATHS = {
    "rules/v1/layers.json",
    "rules/v1/core.json",
    "rules/v1/profiles.json",
    "rules/v1/adapters.json",
}


def rule_compiler_binding(runtime_root: Path) -> dict[str, str]:
    runtime_root = runtime_root.resolve()
    manifest_path = runtime_root / "runtime-manifest.json"
    compiler_path = runtime_root / _COMPILER_PATH
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ControlError("HC-RULE-COMPILER-COMPATIBILITY", "bound runtime manifest is unreadable", status="BLOCKED") from exc
    version = manifest.get("runtimeVersion") if isinstance(manifest, dict) else None
    if not isinstance(version, str) or compiler_path.is_symlink() or not compiler_path.is_file():
        raise ControlError("HC-RULE-COMPILER-COMPATIBILITY", "bound runtime compiler identity is incomplete", status="BLOCKED")
    return {
        "id": "vibe-control-project-rules",
        "version": version,
        "sha256": hashlib.sha256(compiler_path.read_bytes()).hexdigest(),
    }


def _catalog_from_snapshots(namespace: dict[str, Any], snapshots: dict[str, bytes], blockers: list[dict[str, Any]]) -> dict[str, Any]:
    issue = namespace["_issue"]
    layers = json.loads(snapshots["rules/v1/layers.json"].decode("utf-8-sig"))
    core = json.loads(snapshots["rules/v1/core.json"].decode("utf-8-sig"))
    profiles = json.loads(snapshots["rules/v1/profiles.json"].decode("utf-8-sig"))
    adapters = json.loads(snapshots["rules/v1/adapters.json"].decode("utf-8-sig"))
    if not isinstance(layers, dict) or tuple(layers.get("layers", ())) != namespace["LAYERS"]:
        blockers.append(issue("blocker", "RULE-CATALOG-LAYERS", "versioned layer catalog must declare the six fixed layers"))
    adapter_items = adapters.get("adapters") if isinstance(adapters, dict) else None
    resolved_adapters: dict[str, dict[str, Any]] = {}
    if not isinstance(adapter_items, list):
        blockers.append(issue("blocker", "RULE-CATALOG-ADAPTERS", "adapter catalog must contain an adapters array"))
    else:
        for adapter in adapter_items:
            if not isinstance(adapter, dict) or not isinstance(adapter.get("id"), str):
                blockers.append(issue("blocker", "RULE-CATALOG-ADAPTERS", "adapter descriptor is invalid"))
                continue
            if adapter.get("canApprove") is not False or not isinstance(adapter.get("evidenceCapabilities"), list) or not isinstance(adapter.get("doesNotProve"), list) or not isinstance(adapter.get("provesCaseCapabilities"), list):
                blockers.append(issue("blocker", "RULE-CATALOG-ADAPTERS", "adapter descriptor must declare proof limits and canApprove=false", adapter=adapter["id"]))
                continue
            resolved_adapters[adapter["id"]] = adapter
    core_rules = core.get("rules") if isinstance(core, dict) else None
    profile_items = profiles.get("profiles") if isinstance(profiles, dict) else None
    if not isinstance(core_rules, list):
        blockers.append(issue("blocker", "RULE-CATALOG-CORE", "core rule catalog must contain a rules array")); core_rules = []
    if not isinstance(profile_items, list):
        blockers.append(issue("blocker", "RULE-CATALOG-PROFILES", "profile catalog must contain a profiles array")); profile_items = []
    files = [
        {"path": relative, "sha256": hashlib.sha256(snapshots[relative]).hexdigest()}
        for relative in ("rules/v1/layers.json", "rules/v1/core.json", "rules/v1/profiles.json", "rules/v1/adapters.json")
    ]
    return {"version": "v1", "adapters": resolved_adapters, "coreRules": core_rules, "profiles": profile_items, "files": files}


def positioning_summary(value: dict[str, Any]) -> dict[str, Any]:
    """Return only the axes the user is authorizing, in canonical form."""
    return {
        "primaryExperience": value["primaryExperience"],
        "capabilityDomains": sorted(value["capabilityDomains"]),
        "deliveryObjective": value["deliveryObjective"],
        "releaseIntent": value["releaseIntent"],
        "runtimeTargets": sorted(value["runtimeTargets"]),
        "targetEnvironments": sorted(value["targetEnvironments"], key=lambda item: canonical_bytes(item)),
        "distributionChannels": sorted(value["distributionChannels"]),
        "humanQualityGates": sorted(value["humanQualityGates"], key=lambda item: canonical_bytes(item)),
        "nonGoals": sorted(value["nonGoals"]),
        "firstVerticalSlice": value["firstVerticalSlice"],
    }


def verify_positioning(root: Path, positioning: dict[str, Any]) -> list[dict[str, Any]]:
    validate_object("project-positioning", positioning)
    summary_hash = sha256_bytes(canonical_bytes(positioning_summary(positioning)))
    confirmation = positioning["confirmation"]
    checks = [check(
        "HC-POSITIONING-SCHEMA", "PASS", "project positioning satisfies Schema 3.2"
    )]
    ref_result = verify_ref(root, confirmation["record"], "HC-POSITIONING-CONFIRMED")
    confirmed = ref_result["status"] == "PASS" and confirmation["summarySha256"] == summary_hash
    checks.append(check(
        "HC-POSITIONING-CONFIRMED", "PASS" if confirmed else (ref_result["status"] if ref_result["status"] != "PASS" else "FAIL"),
        "user confirmation binds the canonical positioning summary" if confirmed else "positioning confirmation record or summary hash does not bind the selected axes",
        expectedSummarySha256=summary_hash,
        actualSummarySha256=confirmation.get("summarySha256"),
    ))
    return checks


def compile_for_project(
    spec: dict[str, Any],
    project_root: Path,
    runtime_root: Path,
    *,
    expected_runtime_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Always recompile from the source spec; never consume a caller-supplied result."""
    runtime_root = runtime_root.resolve()
    current_compiler = Path(__file__).resolve().with_name("project_rules.py")
    requested_compiler = runtime_root / _COMPILER_PATH
    if requested_compiler.resolve() == current_compiler:
        return compile_positioning(spec, project_root, runtime_root)

    manifest_path = runtime_root / "runtime-manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ControlError("HC-RULE-COMPILER-COMPATIBILITY", "bound runtime manifest is missing or unsafe", status="BLOCKED")
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ControlError("HC-RULE-COMPILER-COMPATIBILITY", "bound runtime manifest is unreadable", status="BLOCKED") from exc
    version = manifest.get("runtimeVersion") if isinstance(manifest, dict) else None
    entries = {
        item.get("path"): item
        for item in manifest.get("files", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    } if isinstance(manifest, dict) else {}
    required_paths = {_COMPILER_PATH, *_RULE_CATALOG_PATHS}
    if not required_paths.issubset(entries):
        raise ControlError("HC-RULE-COMPILER-COMPATIBILITY", "bound runtime manifest omits the compiler or rule catalogs", status="BLOCKED")
    snapshots: dict[str, bytes] = {}
    for relative in sorted(required_paths):
        path = runtime_root / relative
        if path.is_symlink() or not path.is_file():
            raise ControlError("HC-RULE-COMPILER-COMPATIBILITY", "bound compiler inputs are missing or unsafe", status="BLOCKED", details={"path": relative})
        try:
            path.resolve().relative_to(runtime_root)
        except ValueError as exc:
            raise ControlError("HC-RULE-COMPILER-COMPATIBILITY", "bound compiler input escapes its runtime", status="BLOCKED", details={"path": relative}) from exc
        entry = entries[relative]
        snapshot = path.read_bytes()
        snapshots[relative] = snapshot
        actual = hashlib.sha256(snapshot).hexdigest()
        if entry.get("sha256") != actual or entry.get("bytes") != path.stat().st_size:
            raise ControlError("HC-RULE-COMPILER-COMPATIBILITY", "bound compiler input does not match its runtime manifest", status="INVALIDATED", details={"path": relative})
    compiler_hash = entries[_COMPILER_PATH]["sha256"]
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    if expected_runtime_manifest_sha256 is None or manifest_hash != expected_runtime_manifest_sha256:
        raise ControlError(
            "HC-RULE-COMPILER-COMPATIBILITY",
            "bound runtime manifest does not match the governance snapshot",
            status="INVALIDATED",
            details={"expectedSha256": expected_runtime_manifest_sha256, "actualSha256": manifest_hash},
        )
    if (version, compiler_hash) not in _COMPATIBLE_RULE_COMPILERS:
        raise ControlError(
            "HC-RULE-COMPILER-COMPATIBILITY",
            "the installed controller does not support this bound rule compiler",
            status="BLOCKED",
            details={"runtimeVersion": version, "compilerSha256": compiler_hash},
        )
    try:
        namespace: dict[str, Any] = {"__name__": "vibe_control_bound_project_rules", "__file__": str(requested_compiler)}
        exec(compile(snapshots[_COMPILER_PATH], str(requested_compiler), "exec"), namespace)
        legacy_compile = namespace.get("compile_positioning")
        if not callable(legacy_compile):
            raise TypeError("compile_positioning is not callable")
        namespace["_load_runtime_catalog"] = lambda _runtime_root, blockers: _catalog_from_snapshots(namespace, snapshots, blockers)
        return legacy_compile(spec, project_root, runtime_root)
    except ControlError:
        raise
    except Exception as exc:
        raise ControlError("HC-RULE-COMPILER-COMPATIBILITY", "bound rule compiler failed closed", status="BLOCKED") from exc


def compiler_checks(compiled: dict[str, Any]) -> list[dict[str, Any]]:
    conflicts = compiled.get("conflicts", [])
    weakening = [item for item in conflicts if item.get("id") == "OVERLAY-NON-WEAKENING"]
    other_conflicts = [item for item in conflicts if item.get("id") != "OVERLAY-NON-WEAKENING"]
    blockers = compiled.get("blockers", [])
    skill_blockers = [item for item in blockers if str(item.get("id", "")).startswith("SKILL-")]
    adapter_blockers = [item for item in blockers if item not in skill_blockers]
    install_requests = compiled.get("installRequests", [])
    return [
        check("HC-RULESET-NON-WEAKENING", "FAIL" if weakening else "PASS", "overlay attempted to weaken or remove a rule" if weakening else "project overlay only adds constraints", issues=weakening),
        check("HC-RULESET-CONFLICT", "FAIL" if other_conflicts else "PASS", "rule IDs or sources conflict" if other_conflicts else "rule IDs have one canonical meaning", issues=other_conflicts),
        check("HC-ADAPTER-CAPABILITY", "BLOCKED" if adapter_blockers else "PASS", "rule or adapter catalog is incomplete" if adapter_blockers else "adapter descriptors expose explicit proof limits", issues=adapter_blockers),
        check("HC-SKILL-BINDING", "BLOCKED" if skill_blockers else "PASS", "required Skill binding is missing or drifted" if skill_blockers else "required Skill bindings are content-addressed; advisory gaps do not grant proof", issues=skill_blockers),
        check("VC-SKILL-INSTALL-APPROVAL", "BLOCKED" if install_requests else "PASS", "required Skill installation needs explicit user approval and a fresh resolution" if install_requests else "no unapproved required Skill installation is pending", requests=install_requests),
    ]


def coverage_check(compiled: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
    candidate_cases = [case for case in cases if case.get("lifecycle", "CANDIDATE_EXECUTION") == "CANDIDATE_EXECUTION"]
    rule_ids = {item["id"] for item in compiled["canonical"]["layers"]}
    covered_ids = {rule_id for case in candidate_cases for rule_id in case.get("satisfiesRuleIds", [])}
    required_capabilities = {
        capability
        for item in compiled["canonical"]["layers"]
        for capability in item["rule"].get("caseCapabilities", [])
    }
    covered_capabilities = {capability for case in candidate_cases for capability in case.get("capabilities", [])}
    missing_rules = sorted(rule_ids - covered_ids)
    missing_capabilities = sorted(required_capabilities - covered_capabilities)
    descriptors = {item.get("id"): item for item in compiled["canonical"].get("runtimeAdapters", [])}
    unsupported_capabilities = []
    for case in candidate_cases:
        adapter = case.get("adapter", {})
        adapter_id = adapter.get("id") if isinstance(adapter, dict) else adapter
        descriptor = descriptors.get(adapter_id)
        allowed = set(descriptor.get("provesCaseCapabilities", [])) if descriptor else set()
        unsupported = sorted(
            capability for capability in case.get("capabilities", [])
            if capability not in allowed and not capability.startswith("skill-binding:")
        )
        if unsupported:
            unsupported_capabilities.append({"caseId": case.get("id"), "adapterId": adapter_id, "capabilities": unsupported})
    return check(
        "HC-RULE-CASE-COVERAGE", "PASS" if not missing_rules and not missing_capabilities and not unsupported_capabilities else "FAIL",
        "fixed cases cover every applicable rule within their adapter proof boundaries" if not missing_rules and not missing_capabilities and not unsupported_capabilities else "applicable rules are uncovered or cases claim capabilities outside their adapter proof boundary",
        missingRuleIds=missing_rules, missingCapabilities=missing_capabilities, unsupportedCapabilities=unsupported_capabilities,
    )


def load_and_compare_rules(root: Path, rule_ref: dict[str, Any], spec: dict[str, Any], runtime_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ref_result = verify_ref(root, rule_ref, "HC-RULESET-BINDING")
    if ref_result["status"] != "PASS":
        return {}, [ref_result]
    recorded = load_json(root / rule_ref["path"])
    validate_object("resolved-rule-set", recorded)
    compiled = compile_for_project(spec, root, runtime_root)
    matches = recorded.get("canonicalSha256") == compiled.get("canonicalSha256")
    return compiled, [ref_result, check(
        "HC-RULESET-BINDING", "PASS" if matches else "INVALIDATED",
        "resolved rule set equals a fresh deterministic compilation" if matches else "positioning, profile, adapter, Skill, overlay or rule catalog drifted",
        recordedSha256=recorded.get("canonicalSha256"), actualSha256=compiled.get("canonicalSha256"),
    )]


def fail_on_compile_issues(checks: list[dict[str, Any]]) -> None:
    priorities = {"FAIL": 3, "INVALIDATED": 3, "BLOCKED": 2, "PASS": 0}
    failed = [item for item in checks if item["status"] != "PASS"]
    if not failed:
        return
    worst = max(failed, key=lambda item: priorities[item["status"]])
    raise ControlError(worst["id"], worst["message"], status=worst["status"], details=worst.get("details"))
