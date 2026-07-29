"""Deterministic Project Positioning rule compiler.

This module deliberately has no controller, CLI, schema, Git, or write dependency.
It normalizes a positioning specification into a content-addressed rule set and
reports policy conflicts for a caller to map onto stable controller check IDs.
"""
from __future__ import annotations

import hashlib
import json
import fnmatch
import os
import re
from pathlib import Path
from typing import Any


LAYERS = (
    "CORE", "EXPERIENCE", "CAPABILITY_PROFILE", "RUNTIME_ADAPTER",
    "SKILL_BINDING", "PROJECT_OVERLAY",
)
_WEAKENING_FIELDS = {"evidence", "evidenceLevel", "claim", "claimLevel", "maxClaimLevel"}
_POSITIONING_KEYS = (
    "primaryExperience", "capabilityDomains", "deliveryObjective", "releaseIntent",
    "runtimeTargets", "targetEnvironments", "distributionChannels", "humanQualityGates",
    "nonGoals", "firstVerticalSlice",
)


def canonical_rule_bytes(value: Any) -> bytes:
    """Return the one canonical serialization used for equality and hashing."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_rule_bytes(value)).hexdigest()


def _issue(kind: str, issue_id: str, message: str, **details: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"id": issue_id, "message": message}
    if details:
        item["details"] = details
    return item


def _sorted_issues(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: (item["id"], canonical_rule_bytes(item)))


def _load_json(path: Path, *, bucket: list[dict[str, Any]], issue_id: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        bucket.append(_issue("blocker", issue_id, "required versioned rule file is missing", path=str(path)))
    except json.JSONDecodeError:
        bucket.append(_issue("blocker", issue_id, "versioned rule file is malformed", path=str(path)))
    return None


def _rules_directory(runtime_root: Path) -> Path:
    direct = runtime_root / "rules"
    return direct if direct.is_dir() else runtime_root.parent / "rules"


def _load_runtime_catalog(runtime_root: Path, blockers: list[dict[str, Any]]) -> dict[str, Any]:
    directory = _rules_directory(runtime_root) / "v1"
    layers = _load_json(directory / "layers.json", bucket=blockers, issue_id="RULE-CATALOG-LAYERS")
    core = _load_json(directory / "core.json", bucket=blockers, issue_id="RULE-CATALOG-CORE")
    profiles = _load_json(directory / "profiles.json", bucket=blockers, issue_id="RULE-CATALOG-PROFILES")
    adapters = _load_json(directory / "adapters.json", bucket=blockers, issue_id="RULE-CATALOG-ADAPTERS")
    if not isinstance(layers, dict) or tuple(layers.get("layers", ())) != LAYERS:
        blockers.append(_issue("blocker", "RULE-CATALOG-LAYERS", "versioned layer catalog must declare the six fixed layers"))
    adapter_items = adapters.get("adapters") if isinstance(adapters, dict) else None
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(adapter_items, list):
        blockers.append(_issue("blocker", "RULE-CATALOG-ADAPTERS", "adapter catalog must contain an adapters array"))
    else:
        for adapter in adapter_items:
            if not isinstance(adapter, dict) or not isinstance(adapter.get("id"), str):
                blockers.append(_issue("blocker", "RULE-CATALOG-ADAPTERS", "adapter descriptor is invalid"))
                continue
            if adapter.get("canApprove") is not False or not isinstance(adapter.get("evidenceCapabilities"), list) or not isinstance(adapter.get("doesNotProve"), list) or not isinstance(adapter.get("provesCaseCapabilities"), list):
                blockers.append(_issue("blocker", "RULE-CATALOG-ADAPTERS", "adapter descriptor must declare proof limits and canApprove=false", adapter=adapter["id"]))
                continue
            result[adapter["id"]] = adapter
    core_rules = core.get("rules") if isinstance(core, dict) else None
    profile_items = profiles.get("profiles") if isinstance(profiles, dict) else None
    if not isinstance(core_rules, list):
        blockers.append(_issue("blocker", "RULE-CATALOG-CORE", "core rule catalog must contain a rules array")); core_rules = []
    if not isinstance(profile_items, list):
        blockers.append(_issue("blocker", "RULE-CATALOG-PROFILES", "profile catalog must contain a profiles array")); profile_items = []
    files = []
    for name in ("layers.json", "core.json", "profiles.json", "adapters.json"):
        path = directory / name
        if path.is_file():
            files.append({"path": f"rules/v1/{name}", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return {"version": "v1", "adapters": result, "coreRules": core_rules, "profiles": profile_items, "files": files}


def _layer_values(spec: dict[str, Any], layer: str) -> list[Any]:
    source = spec.get("layers", {})
    if isinstance(source, dict) and layer in source:
        value = source[layer]
    else:
        value = spec.get(layer, [])
    return value if isinstance(value, list) else [value]


def _rule_id(value: Any) -> str | None:
    return value.get("id") if isinstance(value, dict) and isinstance(value.get("id"), str) and value["id"] else None


def _normal_rule(layer: str, value: Any, index: int, conflicts: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        conflicts.append(_issue("conflict", "RULE-INVALID-ITEM", "rule item must be an object", layer=layer, index=index))
        return None
    rule_id = _rule_id(value)
    if not rule_id:
        conflicts.append(_issue("conflict", "RULE-INVALID-ID", "rule item requires a nonempty id", layer=layer, index=index))
        return None
    return {"layer": layer, "id": rule_id, "rule": value}


def _contains_weakened_field(value: Any) -> bool:
    if isinstance(value, dict):
        return any(key in _WEAKENING_FIELDS or _contains_weakened_field(child) for key, child in value.items())
    if isinstance(value, list):
        return any(_contains_weakened_field(child) for child in value)
    return False


def _overlay_operations(spec: dict[str, Any]) -> list[Any]:
    overlay = spec.get("projectOverlay", spec.get("PROJECT_OVERLAY", []))
    if isinstance(overlay, dict):
        return overlay.get("operations", overlay.get("rules", []))
    return overlay if isinstance(overlay, list) else [overlay]


def _discover_project(project_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    facts: list[dict[str, Any]] = []
    investigations: list[dict[str, Any]] = []
    candidates = (
        ("Browser", (
            "playwright.config.ts", "playwright.config.js", "playwright.config.mjs", "playwright.config.cjs",
            "**/playwright.config.ts", "**/playwright.config.js", "**/playwright.config.mjs", "**/playwright.config.cjs",
            "**/playwright.*.config.ts", "**/playwright.*.config.js", "**/playwright.*.config.mjs", "**/playwright.*.config.cjs",
            "selenium",
        ), "browser-runtime"),
        ("Godot", ("project.godot",), "godot-runtime"),
        ("Tauri", ("src-tauri/tauri.conf.json", "src-tauri/tauri.conf.json5", "src-tauri/tauri.conf.toml"), None),
        ("Electron", ("electron-builder.yml", "electron-builder.yaml", "electron-builder.json"), None),
        ("Unreal", ("*.uproject",), None),
        ("Capacitor", (
            "capacitor.config.ts", "capacitor.config.js", "capacitor.config.json",
            "**/capacitor.config.ts", "**/capacitor.config.js", "**/capacitor.config.mjs",
            "**/capacitor.config.cjs", "**/capacitor.config.json",
        ), None),
    )
    if not project_root.is_dir():
        investigations.append(_issue("investigation", "PROJECT-ROOT-UNAVAILABLE", "project root cannot be inspected", path=str(project_root)))
        return facts, investigations
    excluded = {".git", ".vibe-control", "node_modules", "archive", "tmp"}
    project_files: list[str] = []
    for directory, child_directories, filenames in os.walk(project_root):
        child_directories[:] = sorted(value for value in child_directories if value not in excluded)
        current = Path(directory)
        project_files.extend((current / filename).relative_to(project_root).as_posix() for filename in sorted(filenames))
    for name, patterns, adapter in candidates:
        matches = sorted({
            relative
            for relative in project_files
            for pattern in patterns
            if fnmatch.fnmatchcase(relative, pattern)
            or (pattern.startswith("**/") and fnmatch.fnmatchcase(relative, pattern[3:]))
        })
        if matches:
            facts.append({"signal": name, "paths": matches, "adapter": adapter})
            if adapter is None:
                investigations.append(_issue("investigation", f"PROJECT-SIGNAL-{name.upper()}", "project signal needs an explicit adapter; it does not infer release intent", paths=matches))
    return facts, investigations


def _directory_tree_hash(path: Path) -> str | None:
    if not path.is_dir():
        return None
    entries = []
    for file in sorted((item for item in path.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        entries.append({"path": file.relative_to(path).as_posix(), "sha256": hashlib.sha256(file.read_bytes()).hexdigest()})
    return _sha(entries)


def _skill_checks(spec: dict[str, Any], project_root: Path, blockers: list[dict[str, Any]], warnings: list[dict[str, Any]], install_requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw = spec.get("skills", spec.get("skillBindings", []))
    values = raw if isinstance(raw, list) else [raw]
    resolved: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            blockers.append(_issue("blocker", "SKILL-BINDING-INVALID", "skill binding must be an object", index=index)); continue
        required = value.get("required") is True or value.get("mode") == "required" or value.get("requirement") == "required"
        name = value.get("skillId", value.get("id", value.get("name", f"skill-{index}")))
        path = value.get("path"); version = value.get("version"); tree_hash = value.get("treeSha256", value.get("treeHash"))
        complete = isinstance(path, str) and isinstance(version, str) and isinstance(tree_hash, str)
        if required and not complete:
            blockers.append(_issue("blocker", "SKILL-BINDING-ADDRESS", "required skill must have path, version, and treeHash", skill=name))
            install_requests.append({"skill": name, "reason": "required skill lacks content-addressed binding", "checkId": "VC-SKILL-INSTALL-APPROVAL", "approvalRequired": True})
            continue
        if not required and not complete:
            warnings.append(_issue("warning", "SKILL-BINDING-ADVISORY", "advisory skill is not content-addressed", skill=name)); continue
        actual_path = Path(path)
        if not actual_path.is_absolute():
            actual_path = project_root / actual_path
        actual_hash = _directory_tree_hash(actual_path)
        item = {
            "skillId": name, "path": str(path).replace("\\", "/"), "version": version,
            "treeSha256": tree_hash, "requirement": "required" if required else "advisory",
            "role": value.get("role", "heuristic-reviewer"),
            "triggerConditions": sorted(value.get("triggerConditions", [])),
            "writePermissions": sorted(value.get("writePermissions", value.get("writePaths", []))), "canApprove": False,
        }
        if actual_hash is None or actual_hash != tree_hash:
            target = blockers if required else warnings
            target.append(_issue("blocker" if required else "warning", "SKILL-BINDING-DRIFT", "skill binding is missing or content drifted", skill=name, expectedTreeHash=tree_hash, actualTreeHash=actual_hash))
            if required: install_requests.append({"skill": name, "reason": "required skill content is missing or drifted", "checkId": "VC-SKILL-INSTALL-APPROVAL", "approvalRequired": True})
        resolved.append(item)
    return sorted(resolved, key=lambda item: canonical_rule_bytes(item))


def compile_positioning(spec: Any, project_root: str | Path, runtime_root: str | Path) -> dict[str, Any]:
    """Compile a positioning spec without writes or non-deterministic fields.

    Conflicts are deliberate non-approval results: callers map their identifiers
    to stable controller checks and retain the returned evidence for diagnosis.
    """
    blockers: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    investigations: list[dict[str, Any]] = []
    install_requests: list[dict[str, Any]] = []
    root = Path(project_root).resolve()
    catalog = _load_runtime_catalog(Path(runtime_root).resolve(), blockers)
    if not isinstance(spec, dict):
        blockers.append(_issue("blocker", "POSITIONING-INVALID", "positioning specification must be an object"))
        spec = {}
    positioning_source = spec.get("positioning") if "positioning" in spec else spec
    if not isinstance(positioning_source, dict):
        blockers.append(_issue("blocker", "POSITIONING-INVALID", "positioning must be an object")); positioning = {}
    else:
        positioning = {key: positioning_source[key] for key in _POSITIONING_KEYS if key in positioning_source}
    compiler_spec = dict(spec)
    compiler_spec.update({key: value for key, value in positioning.items() if key not in compiler_spec})
    by_id: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(catalog["coreRules"]):
        normalized = _normal_rule("CORE", value, index, conflicts)
        if normalized is not None:
            by_id[normalized["id"]] = normalized
    for layer in LAYERS[:-1]:
        for index, value in enumerate(_layer_values(compiler_spec, layer)):
            normalized = _normal_rule(layer, value, index, conflicts)
            if normalized is None:
                continue
            existing = by_id.get(normalized["id"])
            if existing is None:
                by_id[normalized["id"]] = normalized
            elif existing["rule"] != normalized["rule"]:
                conflicts.append(_issue("conflict", "RULE-ID-CONFLICT", "same rule ID has different content", ruleId=normalized["id"], layers=sorted({existing["layer"], layer})))
    for index, operation in enumerate(_overlay_operations(compiler_spec)):
        if not isinstance(operation, dict):
            conflicts.append(_issue("conflict", "OVERLAY-INVALID", "overlay operation must be an object", index=index)); continue
        op = operation.get("op", "ADD")
        candidate = operation.get("rule", operation)
        if op != "ADD":
            conflicts.append(_issue("conflict", "OVERLAY-NON-WEAKENING", "project overlay only permits ADD; remove/replace/lower operations are forbidden", operation=op, index=index)); continue
        if _contains_weakened_field(operation.get("changes", {})):
            conflicts.append(_issue("conflict", "OVERLAY-NON-WEAKENING", "project overlay cannot lower evidence or claim requirements", index=index)); continue
        normalized = _normal_rule("PROJECT_OVERLAY", candidate, index, conflicts)
        if normalized is None:
            continue
        existing = by_id.get(normalized["id"])
        if existing is None:
            by_id[normalized["id"]] = normalized
        elif existing["rule"] != normalized["rule"]:
            conflicts.append(_issue("conflict", "OVERLAY-ID-CONFLICT", "overlay ADD conflicts with an existing rule ID", ruleId=normalized["id"], existingLayer=existing["layer"]))
    primary = positioning.get("primaryExperience")
    domains = set(positioning.get("capabilityDomains", [])) if isinstance(positioning.get("capabilityDomains", []), list) else set()
    profile_ids: list[str] = []
    selected_profiles: list[dict[str, Any]] = []
    for profile in catalog["profiles"]:
        if not isinstance(profile, dict) or not isinstance(profile.get("id"), str):
            conflicts.append(_issue("conflict", "PROFILE-INVALID", "catalog profile requires an id")); continue
        applies = primary in profile.get("primaryExperiences", []) or bool(domains.intersection(profile.get("capabilityDomains", [])))
        if not applies:
            continue
        profile_ids.append(profile["id"]); selected_profiles.append(profile)
        for index, value in enumerate(profile.get("rules", [])):
            normalized = _normal_rule("CAPABILITY_PROFILE", value, index, conflicts)
            if normalized is None: continue
            existing = by_id.get(normalized["id"])
            if existing is None: by_id[normalized["id"]] = normalized
            elif existing["rule"] != normalized["rule"]:
                conflicts.append(_issue("conflict", "RULE-ID-CONFLICT", "profile rule conflicts with an existing rule ID", ruleId=normalized["id"]))
    profile_resolution = {"operator": "AND", "requiredProfileIds": sorted(set(profile_ids)), "canApprove": False}
    facts, found_investigations = _discover_project(root)
    investigations.extend(found_investigations)
    requested_adapters = compiler_spec.get("runtimeAdapters", [])
    requested_adapters = requested_adapters if isinstance(requested_adapters, list) else [requested_adapters]
    adapter_ids = {"generic-command"}
    adapter_ids.update(item if isinstance(item, str) else item.get("id") for item in requested_adapters if isinstance(item, str) or isinstance(item, dict) and isinstance(item.get("id"), str))
    adapter_ids.update(item["adapter"] for item in facts if item.get("adapter"))
    normalized_targets = {str(value).strip().lower() for value in positioning.get("runtimeTargets", [])}
    targets = " ".join(sorted(normalized_targets))
    if "browser" in targets or "web" in targets: adapter_ids.add("browser-runtime")
    webgl_game_adapter = "browser-webgl-game-runtime"
    webgl_game_targets = {"browser-webgl", "browser-webgl2", "webgl", "webgl2"}
    webgl_game_applicable = primary == "GAMEPLAY" and bool(normalized_targets & webgl_game_targets)
    if webgl_game_applicable:
        adapter_ids.add(webgl_game_adapter)
    elif webgl_game_adapter in adapter_ids:
        adapter_ids.remove(webgl_game_adapter)
        investigations.append(_issue(
            "investigation", "ADAPTER-POSITIONING-MISMATCH",
            "browser WebGL gameplay proof requires GAMEPLAY positioning and an explicit WebGL runtime target",
            adapter=webgl_game_adapter,
        ))
    if "godot" in targets: adapter_ids.add("godot-runtime")
    adapter_ids = sorted(adapter_ids)
    adapter_descriptors: list[dict[str, Any]] = []
    for adapter_id in adapter_ids:
        descriptor = catalog["adapters"].get(adapter_id)
        if descriptor is None:
            investigations.append(_issue("investigation", "ADAPTER-UNRESOLVED", "adapter is not a registered versioned descriptor", adapter=adapter_id))
        else:
            adapter_descriptors.append(descriptor)
            rule = {
                "id": f"RULE-ADAPTER-{adapter_id.upper().replace('-', '_')}",
                "applicability": {"adapter": adapter_id},
                "requirements": [f"Use the {adapter_id} proof boundary."],
                "evidenceRequirements": list(descriptor.get("evidenceCapabilities", [])),
                "caseCapabilities": list(descriptor.get("requiredCaseCapabilities", [])),
            }
            normalized = _normal_rule("RUNTIME_ADAPTER", rule, len(adapter_descriptors), conflicts)
            if normalized is not None: by_id.setdefault(normalized["id"], normalized)
    skills = _skill_checks(compiler_spec, root, blockers, warnings, install_requests)
    for skill in skills:
        if skill["requirement"] != "required": continue
        token = re.sub(r"[^A-Za-z0-9]+", "_", skill["skillId"]).strip("_").upper()
        rule = {
            "id": f"RULE-SKILL-{token}", "applicability": {"requiredSkill": skill["skillId"]},
            "requirements": ["Keep the required Skill content-addressed and within its write boundary."],
            "evidenceRequirements": ["tree hash and role/write-boundary verification"],
            "caseCapabilities": [f"skill-binding:{skill['skillId']}"],
        }
        normalized = _normal_rule("SKILL_BINDING", rule, 0, conflicts)
        if normalized is not None: by_id.setdefault(normalized["id"], normalized)
    canonical = {
        "ruleCatalogVersion": catalog["version"],
        "positioningInputs": positioning,
        "catalogFiles": catalog["files"],
        "layers": sorted(by_id.values(), key=lambda item: (LAYERS.index(item["layer"]), item["id"], canonical_rule_bytes(item["rule"]))),
        "capabilityProfiles": profile_resolution,
        "projectSignals": sorted(facts, key=lambda item: (item["signal"], canonical_rule_bytes(item))),
        "runtimeAdapters": sorted(adapter_descriptors, key=lambda item: item["id"]),
        "skillBindings": skills,
        "canApprove": False,
    }
    return {
        "canonical": canonical,
        "canonicalSha256": _sha(canonical),
        "conflicts": _sorted_issues(conflicts),
        "warnings": _sorted_issues(warnings),
        "investigations": _sorted_issues(investigations),
        "installRequests": sorted(install_requests, key=lambda item: canonical_rule_bytes(item)),
        "blockers": _sorted_issues(blockers),
        "canApprove": False,
    }
