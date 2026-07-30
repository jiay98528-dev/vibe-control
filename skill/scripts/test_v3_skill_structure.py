#!/usr/bin/env python3
"""Skill-binding, orchestration-compatibility, and package-structure tests for 0.4.0."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "assets" / "project-control" / "runtime"
sys.path.insert(0, str(RUNTIME))

from vibe_runtime.project_rules import canonical_rule_bytes, compile_positioning  # noqa: E402
from vibe_runtime import cli  # noqa: E402


LOCKED_CHECK_IDS = {
    "HC-POSITIONING-SCHEMA",
    "HC-POSITIONING-CONFIRMED",
    "HC-RULESET-BINDING",
    "HC-RULESET-CONFLICT",
    "HC-RULESET-NON-WEAKENING",
    "HC-RULE-CASE-COVERAGE",
    "HC-ADAPTER-CAPABILITY",
    "HC-SKILL-BINDING",
    "HC-CHECKPOINT-CONFIRMATION",
    "HC-CHECKPOINT-RESULT-MISMATCH",
    "HC-CHECKPOINT-REVIEW-CLOSURE",
    "HC-CHECKPOINT-HUMAN-DECISION",
    "HC-FINDING-TASK-SCOPE",
    "HC-FINDING-CLAIM-SCOPE",
    "HC-FINDING-CORE-REF",
    "HC-AUDIT-EXPLORATION-BUDGET",
    "HC-AUDIT-STOP-CLOSURE",
    "VC-SKILL-INSTALL-APPROVAL",
    "VC-REINSTALL-REQUIRED",
    "HC-AUTOMATION-PLAN-HASH",
    "HC-AUTOMATION-POLICY-DRIFT",
    "HC-AUTOMATION-MANUAL",
    "HC-AUTOMATION-PUSH-POLICY",
    "HC-AUTOMATION-WORKTREE-CLEAN",
    "HC-AUTOMATION-UPSTREAM",
    "HC-AUTOMATION-REMOTE-DRIFT",
    "HC-AUTOMATION-R3-STOP",
    "HC-AUTOMATION-REVIEW-POINT",
    "HC-DASHBOARD-OUTPUT-SCOPE",
    "HC-DASHBOARD-SNAPSHOT",
    "HC-DASHBOARD-READONLY-DRIFT",
    "HC-EXECUTABLE-RESOLUTION",
    "HC-EVIDENCE-GIT-BYTE-POLICY",
    "HC-CASE-LIFECYCLE-SCOPE",
    "HC-AUTOMATION-MILESTONE-MESSAGE",
    "HC-AUTOMATION-MILESTONE-COMMIT",
    "HC-UPGRADE-PLAN-HASH",
    "HC-UPGRADE-INVALIDATION",
    "HC-UPGRADE-ARCHIVE",
    "HC-PROGRESS-STOPPED",
    "HC-PROGRESS-REPORT-BINDING",
    "HC-DASHBOARD-DESTINATION-OWNERSHIP",
    "HC-GUARD-POLICY",
}


def _tree_hash(path: Path) -> str:
    entries = []
    for file in sorted((item for item in path.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        entries.append({"path": file.relative_to(path).as_posix(), "sha256": hashlib.sha256(file.read_bytes()).hexdigest()})
    return hashlib.sha256(canonical_rule_bytes(entries)).hexdigest()


def _binding(path: Path, *, required: bool) -> dict:
    return {
        "skillId": "fixture-skill",
        "requirement": "required" if required else "advisory",
        "role": "producer",
        "triggerConditions": ["fixture"],
        "writePermissions": ["generated/**"],
        "canApprove": False,
        "path": str(path),
        "version": "1.0.0",
        "treeSha256": _tree_hash(path),
    }


def test_required_skill_is_content_addressed_and_cannot_approve() -> None:
    with tempfile.TemporaryDirectory(prefix="vibe-control-v3-skill-") as temp:
        root = Path(temp)
        skill = root / "skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text("---\nname: fixture\ndescription: fixture\n---\n", encoding="utf-8")
        binding = _binding(skill, required=True)
        result = compile_positioning({"skillBindings": [binding]}, root, RUNTIME)
    assert not result["blockers"], result["blockers"]
    resolved = result["canonical"]["skillBindings"][0]
    assert resolved["requirement"] == "required"
    assert resolved["treeSha256"] == binding["treeSha256"]
    assert resolved["role"] == "producer"
    assert resolved["writePermissions"] == ["generated/**"]
    assert resolved["canApprove"] is False
    assert result["canApprove"] is False


def test_required_drift_blocks_and_requests_user_approved_install() -> None:
    with tempfile.TemporaryDirectory(prefix="vibe-control-v3-required-skill-") as temp:
        root = Path(temp)
        skill = root / "skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text("v1\n", encoding="utf-8")
        binding = _binding(skill, required=True)
        (skill / "SKILL.md").write_text("drift\n", encoding="utf-8")
        result = compile_positioning({"skillBindings": [binding]}, root, RUNTIME)
        assert skill.exists(), "resolver must never install/delete/replace a Skill"
    assert "SKILL-BINDING-DRIFT" in {item["id"] for item in result["blockers"]}
    assert result["installRequests"], result
    request = result["installRequests"][0]
    assert request["checkId"] == "VC-SKILL-INSTALL-APPROVAL"
    assert request["approvalRequired"] is True


def test_unaddressed_advisory_skill_warns_without_blocking() -> None:
    with tempfile.TemporaryDirectory(prefix="vibe-control-v3-advisory-skill-") as temp:
        root = Path(temp)
        result = compile_positioning({"skillBindings": [{
            "skillId": "optional-reviewer",
            "requirement": "advisory",
            "role": "heuristic-reviewer",
            "canApprove": False,
        }]}, root, RUNTIME)
    assert not result["blockers"], result["blockers"]
    assert "SKILL-BINDING-ADVISORY" in {item["id"] for item in result["warnings"]}
    assert not result["installRequests"]


def test_all_locked_check_ids_are_consumed_by_runtime() -> None:
    sources = "\n".join(path.read_text(encoding="utf-8-sig") for path in sorted((RUNTIME / "vibe_runtime").glob("*.py")))
    missing = sorted(check_id for check_id in LOCKED_CHECK_IDS if check_id not in sources)
    assert not missing, f"locked 0.4.0 check IDs are not consumed by runtime: {missing}"


def test_skill_routes_schema4_observable_automation_boundary() -> None:
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8-sig")
    required_phrases = [
        "0.4.0", "Schema 4.0", "resolve-rules", "reposition", "upgrade",
        "project-positioning.md", "progress-dashboard.md", "checkpoint-contract.md",
        "TEAM → SUBAGENT → SERIAL", "MILESTONE_COMMITS", "push",
        "automation", "dashboard", "AUTO_LOCAL_TO_REVIEW", "plainLanguage",
    ]
    missing = [phrase for phrase in required_phrases if phrase not in text]
    assert not missing, f"SKILL.md is missing 0.4.0 route language: {missing}"
    assert "do not require or create private keys" in text.lower()
    assert (ROOT / "scripts" / "validate_installation.py").is_file()
    assert "validate_installation.py" in text and "PORTABLE_COPY" in text


def test_orchestration_compatibility_layer_is_capability_driven_and_unbounded() -> None:
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8-sig")
    routing = (ROOT / "references" / "multi-session-routing.md").read_text(encoding="utf-8-sig")
    model_routing = (ROOT / "references" / "model-routing.md").read_text(encoding="utf-8-sig")
    template_text = (ROOT / "assets" / "project-control" / "templates" / "model-routing.json").read_text(encoding="utf-8-sig")
    template = json.loads(template_text)

    combined = "\n".join((skill, routing, model_routing))
    for backend in ("TEAM", "SUBAGENT", "SERIAL"):
        assert backend in combined, f"missing orchestration backend {backend}"
    assert "non Codex" in combined or "非 Codex" in combined
    assert "Coordinator alone writes" in skill
    assert "fresh audit" in skill
    assert "NO_SKILL_FIXED_LIMIT" in routing and "NO_SKILL_FIXED_LIMIT" in model_routing
    assert template["coordination"]["requestedBackend"] == "AUTO"
    assert template["coordination"]["workerCountPolicy"] == "NO_SKILL_FIXED_LIMIT"
    assert "maxConcurrentWorkers" not in template_text
    forbidden_caps = ("Default to at most three workers", "默认最多 3 个执行会话并发")
    assert not any(value in combined for value in forbidden_caps)
    assert "增量 wait" in routing and "TEAM" in routing
    assert "mailbox/wait" in routing and "SUBAGENT" in routing


def test_schema_and_rule_resources_exist_in_public_and_runtime_bundles() -> None:
    schema_names = {
        "audit-closure.schema.json",
        "project-positioning.schema.json",
        "resolved-rule-set.schema.json",
        "profile.schema.json",
        "adapter-descriptor.schema.json",
        "skill-binding.schema.json",
        "rule-overlay.schema.json",
        "bootstrap-spec.schema.json",
        "automation-policy.schema.json",
    }
    public = ROOT / "assets" / "project-control" / "schemas"
    pinned = RUNTIME / "schemas"
    assert schema_names <= {path.name for path in public.glob("*.json")}
    assert schema_names <= {path.name for path in pinned.glob("*.json")}
    for name in schema_names:
        assert (public / name).read_bytes() == (pinned / name).read_bytes(), f"schema mirror drift: {name}"
    assert (RUNTIME / "rules" / "v1" / "core.json").is_file()
    assert (RUNTIME / "rules" / "v1" / "profiles.json").is_file()
    assert (RUNTIME / "rules" / "v1" / "adapters.json").is_file()


def test_automation_dashboard_surface_and_assurance_controls_exist() -> None:
    parser = cli.parser()
    subparsers = next(action for action in parser._actions if hasattr(action, "choices") and action.choices)
    assert {"automation", "dashboard"} <= set(subparsers.choices)
    assert (RUNTIME / "vibe_runtime" / "automation_control.py").is_file()
    assert (RUNTIME / "vibe_runtime" / "dashboard.py").is_file()
    assert (ROOT / "assets" / "project-control" / "templates" / "automation-policy.json").is_file()

    matrix = json.loads((ROOT / "references" / "controller-assurance-matrix.json").read_text(encoding="utf-8-sig"))
    controls = {item["id"]: item for item in matrix["confirmedControls"]}
    for control_id in (
        "CTRL-CONFIRMED-031", "CTRL-CONFIRMED-032", "CTRL-CONFIRMED-033",
        "CTRL-CONFIRMED-037", "CTRL-CONFIRMED-038", "CTRL-CONFIRMED-039",
        "CTRL-CONFIRMED-040", "CTRL-CONFIRMED-041", "CTRL-CONFIRMED-042",
        "CTRL-CONFIRMED-043",
    ):
        item = controls[control_id]
        assert item["implementationStatus"] == "IMPLEMENTED"
        assert item["independentValidation"] == "PENDING"


TESTS = [
    test_required_skill_is_content_addressed_and_cannot_approve,
    test_required_drift_blocks_and_requests_user_approved_install,
    test_unaddressed_advisory_skill_warns_without_blocking,
    test_all_locked_check_ids_are_consumed_by_runtime,
    test_skill_routes_schema4_observable_automation_boundary,
    test_orchestration_compatibility_layer_is_capability_driven_and_unbounded,
    test_schema_and_rule_resources_exist_in_public_and_runtime_bundles,
    test_automation_dashboard_surface_and_assurance_controls_exist,
]


def main() -> int:
    results = []
    for test in TESTS:
        try:
            test()
            results.append({"test": test.__name__, "status": "PASS"})
        except Exception as exc:
            results.append({"test": test.__name__, "status": "FAIL", "error": str(exc)})
    ok = all(item["status"] == "PASS" for item in results)
    print(json.dumps({"status": "PASS" if ok else "FAIL", "suite": "v3-skill-structure", "tests": results}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
