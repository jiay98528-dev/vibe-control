#!/usr/bin/env python3
"""Focused deterministic checks for the vibe-control Schema 4.0 core."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "assets" / "project-control" / "runtime"
sys.path.insert(0, str(RUNTIME))

from vibe_runtime.automation_control import (  # noqa: E402
    SUBAGENT_CAPABILITIES,
    TEAM_CAPABILITIES,
    _validate_semantics,
    default_policy_spec,
    failure_disposition,
    guard_effect_from_checks,
    resolve_coordination_backend,
)
from vibe_runtime.checkpoint_control import (  # noqa: E402
    DEFAULT_AUDIT_POLICY,
    execution_plan_checks,
    execution_plan_sha256,
    checkpoint_contract_checks,
    checkpoint_set_sha256,
    review_requirement,
    statement_id,
)
from vibe_runtime.common import (  # noqa: E402
    SCHEMA_VERSION,
    ControlError,
    envelope,
    error_envelope,
)
from vibe_runtime.controller import execution_result  # noqa: E402
from vibe_runtime.schema import validate_object  # noqa: E402
from vibe_runtime.upgrade_control import (  # noqa: E402
    DOWNSTREAM_DIRECTORIES,
    INVALIDATES,
    upgrade_actions,
    upgrade_automation_policy_spec,
)
import vibe_runtime.upgrade_control as upgrade_module  # noqa: E402


def expect_error(check_id: str, callback) -> None:
    try:
        callback()
    except ControlError as exc:
        assert exc.check_id == check_id, (exc.check_id, check_id, exc.message)
    else:
        raise AssertionError(f"expected {check_id}")


def fixture() -> tuple[dict, dict, dict]:
    signal = {"id": statement_id("SIG", "The locked candidate prints OK"), "statement": "The locked candidate prints OK"}
    positioning = {
        "schemaVersion": "4.0",
        "firstVerticalSlice": {"successSignals": [signal]},
        "humanQualityGates": [],
    }
    case = {
        "id": "CASE-001", "observation": "runtime-observed", "maxClaimLevel": "VERIFIED",
        "oracle": {"exitCode": 0, "stdoutContainsAll": ["OK"], "stderrContainsNone": []},
        "artifacts": [],
    }
    catalog = {"schemaVersion": "4.0", "catalogId": "cases", "cases": [case]}
    contract = {
        "schemaVersion": "4.0", "taskId": "TASK-001", "goal": "Prove one observable outcome",
        "objectiveRefs": ["KO-001"], "allowedPaths": ["src/**"], "forbiddenPaths": ["secrets/**"],
        "requiredCaseIds": ["CASE-001"], "risk": "R2", "maxClaimLevel": "VERIFIED",
        "authorityRefs": [], "nonGoals": [], "humanDecisionPoints": [],
        "acceptanceCheckpoints": [{
            "id": "CP-001", "sourceRefs": [signal["id"]], "objectiveRefs": ["KO-001"],
            "statement": "Candidate prints OK", "type": "AUTOMATED", "requiredForClaim": "VERIFIED",
            "caseIds": ["CASE-001"], "assertions": [{"id": "ASRT-001", "statement": "stdout includes OK", "caseIds": ["CASE-001"]}],
            "expected": {"status": "PASS", "minExecuted": 1, "maxFailed": 0, "maxSkipped": 0, "artifacts": "AS_DECLARED"},
            "notProven": ["Release readiness"],
        }],
        "checkpointConfirmation": {
            "actorId": "owner", "summary": "confirmed task plan", "checkpointSetSha256": "0" * 64,
            "executionPlanSha256": "0" * 64, "record": "CONFIRMATION.json", "confirmedAt": "2026-07-30T00:00:00Z",
        },
        "auditPolicy": copy.deepcopy(DEFAULT_AUDIT_POLICY),
        "milestones": [{
            "id": "MS-001", "outcome": "Close the observable slice", "objectiveRefs": ["KO-001"], "dependsOn": [],
            "workNodes": [{"id": "WN-001", "title": "Implement the observable slice", "kind": "IMPLEMENTATION", "allowedPaths": ["src/**"], "minimumChecks": ["QC-001", "CASE-001"], "ownerRole": "IMPLEMENTER"}],
            "checkpointIds": ["CP-001"], "expectedPassConditions": ["CP-001 reports PASS"],
        }],
        "scorecardPlan": {
            "weights": {"FUNCTIONALITY": 40, "ROBUSTNESS_SECURITY": 25, "AUDIT": 20, "PROCESS": 15},
            "items": [
                {"id": "SC-001", "category": "FUNCTIONALITY", "statement": "Outcome works", "checkpointIds": ["CP-001"], "factSources": [{"kind": "CHECKPOINT", "refs": ["CP-001"]}]},
                {"id": "SC-002", "category": "ROBUSTNESS_SECURITY", "statement": "Failure stays visible", "checkpointIds": ["CP-001"], "factSources": [{"kind": "CASE", "refs": ["CASE-001"]}]},
                {"id": "SC-003", "category": "AUDIT", "statement": "Evidence binds candidate", "checkpointIds": ["CP-001"], "factSources": [{"kind": "REVIEW", "refs": ["FRESH-INDEPENDENT-REVIEW"]}]},
                {"id": "SC-004", "category": "PROCESS", "statement": "Scope stays bounded", "checkpointIds": ["CP-001"], "factSources": [{"kind": "CORE_CONTROL", "refs": ["RULE-CORE-OBSERVABLE-CANDIDATE"]}]},
            ],
        },
        "verificationStrategy": {
            "mode": "CANDIDATE_BOUND", "failureDisposition": "REPAIR_WITHIN_CONTRACT", "eligibleObservations": ["runtime-observed"], "requireZeroSkipped": True,
            "checkpointCases": [{"checkpointId": "CP-001", "caseIds": ["CASE-001"]}],
            "implementer": {"quickChecks": [{"id": "QC-001", "command": ["python", "-m", "compileall", "src"], "requiredBeforeMilestone": True}]},
            "executor": {"caseIds": ["CASE-001"], "evidenceRequirements": ["candidate-bound transcript", "nonzero counters", "zero skipped cases"]},
            "auditor": {"required": True, "form": "FRESH_INDEPENDENT_REVIEW", "inputs": ["candidate", "case evidence", "checkpoint expectations"], "stopCondition": "ALL_REQUIRED_CHECKPOINTS_REPORTED"},
            "notProven": ["Release readiness"],
        },
        "guardPolicy": {"defaultEffect": "ADVISORY", "guards": [
            {"id": "GUARD-ACTION", "scope": "MUTATION", "effect": "ACTION_GUARD"},
            {"id": "GUARD-CLAIM", "scope": "CLAIM", "effect": "CLAIM_GUARD"},
            {"id": "GUARD-PROCESS", "scope": "PROCESS", "effect": "ADVISORY"},
            {"id": "GUARD-HUMAN", "scope": "HUMAN", "effect": "HUMAN_DECISION"},
            {"id": "GUARD-ENVIRONMENT", "scope": "ENVIRONMENT", "effect": "ENVIRONMENT_BLOCKED"},
        ]},
        "reportingPolicy": {
            "orientation": "ZERO_CONTEXT_ORIENTATION", "progressMode": "NON_BLOCKING", "reviewPoint": "OWNER_REVIEW",
            "plainLanguageFields": ["projectPurpose", "whatWasDone", "whatWorksNow", "whatStillDoesNotWork", "userImpact", "canContinue", "canRelease"],
            "nextActions": {"continue": ["continue the next work node"], "repair": ["repair within the task"], "humanReview": ["review the candidate"]},
        },
    }
    contract["checkpointConfirmation"]["checkpointSetSha256"] = checkpoint_set_sha256(contract)
    contract["checkpointConfirmation"]["executionPlanSha256"] = execution_plan_sha256(contract)
    return contract, positioning, catalog


def test_schema_and_execution_plan() -> None:
    contract, positioning, catalog = fixture()
    validate_object("task-contract", contract)
    checks = checkpoint_contract_checks(contract, positioning, catalog, "VERIFIED")
    assert all(item["status"] == "PASS" for item in checks)
    assert {item["category"] for item in contract["scorecardPlan"]["items"]} == {"FUNCTIONALITY", "ROBUSTNESS_SECURITY", "AUDIT", "PROCESS"}


def test_plan_mutations_fail_closed() -> None:
    contract, _, catalog = fixture()
    original = execution_plan_sha256(contract)

    changed = copy.deepcopy(contract)
    changed["scorecardPlan"]["items"][0]["statement"] = "Different meaning"
    assert execution_plan_sha256(changed) != original
    expect_error("HC-EXECUTION-PLAN-HASH", lambda: execution_plan_checks(changed, catalog))

    missing_category = copy.deepcopy(contract)
    missing_category["scorecardPlan"]["items"][-1]["category"] = "AUDIT"
    missing_category["scorecardPlan"]["items"][-1]["factSources"] = [{"kind": "REVIEW", "refs": ["SECOND-REVIEW"]}]
    missing_category["checkpointConfirmation"]["executionPlanSha256"] = execution_plan_sha256(missing_category)
    expect_error("HC-SCORECARD-CLOSURE", lambda: execution_plan_checks(missing_category, catalog))

    bad_guard = copy.deepcopy(contract)
    bad_guard["guardPolicy"]["guards"][0]["effect"] = "ADVISORY"
    bad_guard["checkpointConfirmation"]["executionPlanSha256"] = execution_plan_sha256(bad_guard)
    expect_error("HC-GUARD-POLICY", lambda: execution_plan_checks(bad_guard, catalog))

    bad_milestone = copy.deepcopy(contract)
    bad_milestone["milestones"].append(copy.deepcopy(bad_milestone["milestones"][0]))
    bad_milestone["milestones"][-1]["id"] = "MS-002"
    bad_milestone["checkpointConfirmation"]["executionPlanSha256"] = execution_plan_sha256(bad_milestone)
    expect_error("HC-MILESTONE-CLOSURE", lambda: execution_plan_checks(bad_milestone, catalog))

    outside_node = copy.deepcopy(contract)
    outside_node["milestones"][0]["workNodes"][0]["allowedPaths"] = ["docs/**"]
    outside_node["checkpointConfirmation"]["executionPlanSha256"] = execution_plan_sha256(outside_node)
    expect_error("HC-MILESTONE-CLOSURE", lambda: execution_plan_checks(outside_node, catalog))

    unknown_dependency = copy.deepcopy(contract)
    unknown_dependency["milestones"][0]["dependsOn"] = ["MS-UNKNOWN"]
    unknown_dependency["checkpointConfirmation"]["executionPlanSha256"] = execution_plan_sha256(unknown_dependency)
    expect_error("HC-MILESTONE-CLOSURE", lambda: execution_plan_checks(unknown_dependency, catalog))

    audit_borrows_function = copy.deepcopy(contract)
    audit_borrows_function["scorecardPlan"]["items"][2]["factSources"] = [{"kind": "CHECKPOINT", "refs": ["CP-001"]}]
    audit_borrows_function["checkpointConfirmation"]["executionPlanSha256"] = execution_plan_sha256(audit_borrows_function)
    expect_error("HC-SCORECARD-FACT-SOURCE", lambda: execution_plan_checks(audit_borrows_function, catalog))

    process_borrows_case = copy.deepcopy(contract)
    process_borrows_case["scorecardPlan"]["items"][3]["factSources"] = [{"kind": "CASE", "refs": ["CASE-001"]}]
    process_borrows_case["checkpointConfirmation"]["executionPlanSha256"] = execution_plan_sha256(process_borrows_case)
    expect_error("HC-SCORECARD-FACT-SOURCE", lambda: execution_plan_checks(process_borrows_case, catalog))

    wrong_mapping = copy.deepcopy(contract)
    wrong_mapping["verificationStrategy"]["checkpointCases"][0]["caseIds"] = ["CASE-OTHER"]
    wrong_mapping["checkpointConfirmation"]["executionPlanSha256"] = execution_plan_sha256(wrong_mapping)
    expect_error("HC-VERIFICATION-STRATEGY", lambda: execution_plan_checks(wrong_mapping, catalog))

    unknown_minimum = copy.deepcopy(contract)
    unknown_minimum["milestones"][0]["workNodes"][0]["minimumChecks"] = ["UNKNOWN-CHECK"]
    unknown_minimum["checkpointConfirmation"]["executionPlanSha256"] = execution_plan_sha256(unknown_minimum)
    expect_error("HC-VERIFICATION-STRATEGY", lambda: execution_plan_checks(unknown_minimum, catalog))


def test_project_derived_audit_policy_is_bounded_not_fixed() -> None:
    contract, positioning, catalog = fixture()
    contract["auditPolicy"] = {
        "strategy": "PROJECT_DERIVED", "maxExploratoryFindings": 1,
        "stopCondition": "ALL_MILESTONE_CHECKPOINTS_REPORTED",
        "requiredReviewRoles": ["DOMAIN_REVIEWER", "INDEPENDENT_AUDITOR"],
        "triggerReasons": ["RISK_BOUNDARY_REACHED"],
    }
    contract["verificationStrategy"]["auditor"]["form"] = "DOMAIN_AND_INDEPENDENT_REVIEW"
    contract["verificationStrategy"]["auditor"]["stopCondition"] = "ALL_MILESTONE_CHECKPOINTS_REPORTED"
    contract["checkpointConfirmation"]["checkpointSetSha256"] = checkpoint_set_sha256(contract)
    contract["checkpointConfirmation"]["executionPlanSha256"] = execution_plan_sha256(contract)
    validate_object("task-contract", contract)
    checkpoint_contract_checks(contract, positioning, catalog, "VERIFIED")

    too_wide = copy.deepcopy(contract)
    too_wide["auditPolicy"]["maxExploratoryFindings"] = 4
    expect_error("HC-SCHEMA-CONTRACT", lambda: validate_object("task-contract", too_wide))


def test_same_risk_can_lock_different_review_workflows() -> None:
    required, positioning, catalog = fixture()
    optional = copy.deepcopy(required)
    optional["auditPolicy"].update({
        "requiredReviewRoles": [],
        "triggerReasons": [],
        "stopCondition": "NO_SEPARATE_REVIEW_REQUIRED",
    })
    optional["verificationStrategy"]["auditor"] = {
        "required": False,
        "form": "NONE",
        "inputs": [],
        "stopCondition": "NO_SEPARATE_REVIEW_REQUIRED",
    }
    optional["checkpointConfirmation"]["checkpointSetSha256"] = checkpoint_set_sha256(optional)
    optional["checkpointConfirmation"]["executionPlanSha256"] = execution_plan_sha256(optional)
    assert required["risk"] == optional["risk"] == "R2"
    assert review_requirement(required)["required"] is True
    assert review_requirement(optional) == {
        "required": False,
        "form": "NONE",
        "roles": [],
        "triggerReasons": [],
        "stopCondition": "NO_SEPARATE_REVIEW_REQUIRED",
    }
    validate_object("task-contract", optional)
    checkpoint_contract_checks(optional, positioning, catalog, "VERIFIED")
    assert checkpoint_set_sha256(required) != checkpoint_set_sha256(optional)
    assert execution_plan_sha256(required) != execution_plan_sha256(optional)


def _ref(path: str = "x.json") -> dict:
    return {"path": path, "bytes": 1, "sha256": "a" * 64, "tracked": True}


def test_execution_hash_is_required_downstream() -> None:
    contract, _, _ = fixture()
    execution_hash = execution_plan_sha256(contract)
    checkpoint_hash = checkpoint_set_sha256(contract)
    objects = {
        "task-lock": {"schemaVersion": "4.0", "taskId": "TASK-001", "contract": _ref(), "governanceLock": _ref(), "keyObjectives": _ref(), "caseCatalog": _ref(), "positioning": _ref(), "resolvedRuleSet": _ref(), "authorityBindings": [], "applicableRuleIds": ["RULE"], "requiredCaseCapabilities": ["command"], "checkpointSetSha256": checkpoint_hash, "executionPlanSha256": execution_hash, "checkpointConfirmation": _ref(), "baselineCommit": "b" * 40, "baselineTree": "c" * 40, "lockedAt": "2026-07-30T00:00:00Z"},
        "candidate-manifest": {"schemaVersion": "4.0", "candidateId": "candidate", "taskId": "TASK-001", "taskLock": _ref(), "keyObjectives": _ref(), "requirementSources": [_ref()], "positioning": _ref(), "resolvedRuleSet": _ref(), "checkpointSetSha256": checkpoint_hash, "executionPlanSha256": execution_hash, "commit": "b" * 40, "tree": "c" * 40, "implementer": {"actorId": "i", "sessionId": "s"}, "changedPaths": [], "inputBindings": [], "frozenAt": "2026-07-30T00:00:00Z"},
        "execution-evidence": {"schemaVersion": "4.0", "evidenceId": "e", "taskId": "TASK-001", "candidateId": "candidate", "candidateCommit": "b" * 40, "positioning": _ref(), "resolvedRuleSet": _ref(), "caseId": "CASE-001", "caseHash": "d" * 64, "oracleHash": "e" * 64, "inputHash": "f" * 64, "checkpointSetSha256": checkpoint_hash, "executionPlanSha256": execution_hash, "checkpointIds": ["CP-001"], "executor": {}, "observation": "runtime-observed", "adapter": {"id": "generic", "version": "1", "sha256": "a" * 64}, "capabilitiesObserved": [], "adapterInvocation": _ref(), "command": ["tool"], "startedAt": "2026-07-30T00:00:00Z", "finishedAt": "2026-07-30T00:00:01Z", "exitCode": 0, "counters": {}, "transcript": _ref(), "artifacts": [], "result": "PASS"},
        "review-attestation": {"schemaVersion": "4.0", "reviewId": "r", "taskId": "TASK-001", "candidateId": "candidate", "candidateCommit": "b" * 40, "checkpointSetSha256": checkpoint_hash, "executionPlanSha256": execution_hash, "keyObjectives": _ref(), "positioning": _ref(), "resolvedRuleSet": _ref(), "reviewForm": "FRESH_INDEPENDENT_REVIEW", "reviewRoles": ["INDEPENDENT_AUDITOR"], "auditor": {"actorId": "a", "sessionId": "s"}, "evidenceIds": [], "evidenceRefs": [], "checkpointResults": [], "findings": [], "transcript": _ref(), "result": "PASS", "reviewedAt": "2026-07-30T00:00:00Z"},
        "approval-signature": {"schemaVersion": "4.0", "decisionId": "d", "taskId": "TASK-001", "candidateId": "candidate", "candidateCommit": "b" * 40, "checkpointSetSha256": checkpoint_hash, "executionPlanSha256": execution_hash, "checkpointDecisions": [], "positioning": _ref(), "resolvedRuleSet": _ref(), "scope": ["src/x"], "owner": {"actorId": "o"}, "decision": "APPROVE", "decidedAt": "2026-07-30T00:00:00Z"},
        "handoff": {"schemaVersion": "4.0", "handoffId": "h", "taskId": "TASK-001", "candidateId": "candidate", "checkpointSetSha256": checkpoint_hash, "executionPlanSha256": execution_hash, "positioningId": "p", "ruleSetId": "r", "phase": "CANDIDATE_FROZEN", "health": "CLEAR", "claimLevel": "DEVELOPMENT_CHECKED", "evidenceIds": [], "reviewId": None, "decisionId": None, "blockers": [], "createdAt": "2026-07-30T00:00:00Z"},
    }
    schema_errors = {
        "task-lock": "HC-SCHEMA-TASK_LOCK",
        "candidate-manifest": "HC-SCHEMA-CANDIDATE",
        "execution-evidence": "HC-SCHEMA-EVIDENCE",
        "review-attestation": "HC-SCHEMA-REVIEW_ATTESTATION",
        "approval-signature": "HC-SCHEMA-APPROVAL_SIGNATURE",
        "handoff": "HC-SCHEMA-HANDOFF",
    }
    for kind, value in objects.items():
        validate_object(kind, value)
        broken = copy.deepcopy(value)
        broken.pop("executionPlanSha256")
        expect_error(schema_errors[kind], lambda kind=kind, broken=broken: validate_object(kind, broken))


def test_automatic_default_and_capability_routing() -> None:
    confirmation = {"actorId": "owner", "record": "CONFIRMATION.json", "confirmedAt": "2026-07-30T00:00:00Z"}
    policy = default_policy_spec("project", confirmation)
    _validate_semantics(policy, project_id="project")
    assert (policy["mode"], policy["commitPolicy"], policy["pushPolicy"]) == ("AUTO_LOCAL_TO_REVIEW", "MILESTONE_COMMITS", "NONE")
    assert resolve_coordination_backend(TEAM_CAPABILITIES)["resolvedBackend"] == "TEAM"
    authorized_team = resolve_coordination_backend(
        TEAM_CAPABILITIES | SUBAGENT_CAPABILITIES,
        host_requires_authorization=True,
        authorization_granted=True,
    )
    assert authorized_team["resolvedBackend"] == "TEAM" and authorized_team["authorizationPromptRequired"] is False
    prompt_fallback = resolve_coordination_backend(
        TEAM_CAPABILITIES | SUBAGENT_CAPABILITIES,
        host_requires_authorization=True,
        authorization_granted=False,
    )
    assert prompt_fallback["resolvedBackend"] == "SUBAGENT" and prompt_fallback["authorizationPromptRequired"] is True
    assert resolve_coordination_backend(SUBAGENT_CAPABILITIES)["resolvedBackend"] == "SUBAGENT"
    assert resolve_coordination_backend(TEAM_CAPABILITIES, "CODEX_THREADS")["requestedBackend"] == "TEAM"
    assert resolve_coordination_backend(SUBAGENT_CAPABILITIES, "SUBAGENTS")["requestedBackend"] == "SUBAGENT"
    assert resolve_coordination_backend([])["resolvedBackend"] == "SERIAL"
    assert policy["coordination"]["workerLimit"] == "HOST_CAPACITY_ONLY"


def test_failure_is_repairable_but_never_claim_eligible() -> None:
    contract, _, _ = fixture()
    continuation = failure_disposition(contract, "continue", "FAILED")
    commit = failure_disposition(contract, "commit", "FAILED")
    assert continuation == {"allowed": True, "repairRequired": True, "effect": "CLAIM_GUARD", "claimEligible": False}
    assert commit == {"allowed": False, "repairRequired": True, "effect": "ACTION_GUARD", "claimEligible": False}
    report = execution_result(["evidence.json"], ["FAIL"], None)
    assert report["status"] == "FAIL" and report["formal"]["eligible"] is False
    assert isinstance(report["plainLanguage"]["canContinue"], str) and isinstance(report["plainLanguage"]["canRelease"], str)
    claim_effect = guard_effect_from_checks(contract, [{"id": "HC-REQUIRED-CASE-COVERAGE", "status": "FAIL"}])
    environment_effect = guard_effect_from_checks(contract, [{"id": "HC-EXECUTABLE-RESOLUTION", "status": "BLOCKED"}])
    dependency_effect = guard_effect_from_checks(contract, [{"id": "HC-DEPENDENCY-JSONSCHEMA", "status": "BLOCKED"}])
    action_effect = guard_effect_from_checks(contract, [{"id": "HC-AUTOMATION-BOUNDARY-CHANGE", "status": "BLOCKED"}])
    human_effect = guard_effect_from_checks(contract, [{"id": "HC-AUTOMATION-REVIEW-POINT", "status": "BLOCKED"}])
    assert failure_disposition(contract, "continue", "BLOCKED", claim_effect)["allowed"] is True
    assert failure_disposition(contract, "continue", "CLEAR", claim_effect) == {"allowed": True, "repairRequired": True, "effect": "CLAIM_GUARD", "claimEligible": False}
    assert failure_disposition(contract, "commit", "CLEAR", claim_effect)["allowed"] is False
    assert failure_disposition(contract, "continue", "BLOCKED", environment_effect)["allowed"] is False
    assert dependency_effect == environment_effect == "ENVIRONMENT_BLOCKED"
    assert failure_disposition(contract, "continue", "BLOCKED", action_effect)["allowed"] is False
    assert failure_disposition(contract, "continue", "BLOCKED", human_effect)["allowed"] is False


def test_plain_language_is_universal_and_overrideable() -> None:
    for report in (
        envelope(status="PASS"),
        envelope(status="BLOCKED"),
        error_envelope(ControlError("HC-EXAMPLE", "internal detail", status="FAIL")),
    ):
        assert report["schemaVersion"] == "4.0"
        assert set(report["plainLanguage"]) == {"projectPurpose", "whatWasDone", "whatWorksNow", "whatStillDoesNotWork", "userImpact", "canContinue", "canRelease"}
        assert list(report)[-1] == "plainLanguage"
        assert all(isinstance(value, str) and value.strip() for value in report["plainLanguage"].values())
        assert all("HC-" not in value for value in report["plainLanguage"].values())
    overridden = envelope(status="BLOCKED", plain_language={"whatWasDone": "已生成只读计划。", "canContinue": "可以继续阅读计划，但尚不能执行。"})
    assert overridden["plainLanguage"]["whatWasDone"] == "已生成只读计划。"
    assert overridden["plainLanguage"]["canContinue"] == "可以继续阅读计划，但尚不能执行。"
    expect_error("HC-PLAIN-LANGUAGE", lambda: envelope(status="PASS", plain_language={"whatWorksNow": "Schema 已通过。"}))
    expect_error("HC-PLAIN-LANGUAGE", lambda: envelope(status="PASS", plain_language={"whatWorksNow": "候选提交的哈希已通过。"}))


def test_schema_upgrade_boundary_and_mirrors() -> None:
    assert SCHEMA_VERSION == "4.0"
    assert upgrade_actions("3.2") == upgrade_actions("3.2")
    assert "convert-schema-3.2-to-4.0" in upgrade_actions("3.2")
    assert set(INVALIDATES) >= {"task", "task-lock", "candidate", "evidence", "review", "decision", "handoff"}
    assert set(DOWNSTREAM_DIRECTORIES) >= {"tasks", "task-locks", "candidates", "evidence", "reviews", "decisions", "handoffs"}
    public = ROOT / "assets" / "project-control" / "schemas"
    mirror = RUNTIME / "schemas"
    for path in public.glob("*.schema.json"):
        assert path.read_bytes() == (mirror / path.name).read_bytes(), path.name


def test_schema_32_upgrade_selects_auto_unless_explicitly_manual() -> None:
    confirmation = {"actorId": "owner", "record": "CONFIRMATION.json", "confirmedAt": "2026-07-30T00:00:00Z"}
    legacy_manual = default_policy_spec("project", confirmation, mode="MANUAL_STAGE_CONFIRMATION")
    automatic = upgrade_automation_policy_spec("3.2", "project", confirmation, {}, legacy_manual)
    explicit_manual = upgrade_automation_policy_spec(
        "3.2", "project", confirmation, {"automationMode": "MANUAL_STAGE_CONFIRMATION"}, None,
    )
    preserved = upgrade_automation_policy_spec("4.0", "project", confirmation, {}, legacy_manual)
    assert (automatic["mode"], automatic["commitPolicy"], automatic["pushPolicy"]) == ("AUTO_LOCAL_TO_REVIEW", "MILESTONE_COMMITS", "NONE")
    assert (explicit_manual["mode"], explicit_manual["commitPolicy"], explicit_manual["pushPolicy"]) == ("MANUAL_STAGE_CONFIRMATION", "MANUAL", "NONE")
    assert preserved is legacy_manual
    upgrade_spec = {
        "schemaVersion": "4.0", "projectId": "project",
        "sourceRuntimeVersion": "0.3.7", "targetRuntimeVersion": "0.4.0",
        "sourceSchemaVersion": "3.2", "targetSchemaVersion": "4.0",
        "automationMode": "MANUAL_STAGE_CONFIRMATION",
        "confirmation": {"actorId": "owner", "summary": "manual after upgrade", "summarySha256": hashlib.sha256(b"manual after upgrade").hexdigest(), "confirmedAt": "2026-07-30T00:00:00Z"},
    }
    validate_object("upgrade-spec", upgrade_spec)
    invalid_same_schema = copy.deepcopy(upgrade_spec)
    invalid_same_schema["sourceSchemaVersion"] = "4.0"
    expect_error("HC-SCHEMA-UPGRADE-SPEC", lambda: validate_object("upgrade-spec", invalid_same_schema))


def test_legacy_32_upgrade_plan_is_deterministic_and_invalidates_facts() -> None:
    def run(root: Path, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def write(path: Path, value) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")

    def ref(root: Path, path: Path) -> dict:
        data = path.read_bytes()
        return {"path": path.relative_to(root).as_posix(), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "tracked": True}

    with tempfile.TemporaryDirectory(prefix="vc40-legacy-plan-") as directory:
        root = Path(directory)
        run(root, "init")
        run(root, "config", "user.name", "vc40-test")
        run(root, "config", "user.email", "vc40@example.invalid")
        control = root / ".vibe-control"
        package = control / "governance" / "package-manifest.json"
        runtime_manifest = control / "runtime" / "0.3.7" / "runtime-manifest.json"
        matrix = control / "governance" / "matrix.json"
        cases = control / "case-catalog.json"
        objectives = control / "key-objectives-lock.json"
        positioning = control / "project-positioning.json"
        resolved = control / "resolved-rule-set.json"
        for path, value in (
            (package, "package\n"), (runtime_manifest, "runtime\n"), (matrix, "matrix\n"),
            (cases, {"schemaVersion": "3.2", "catalogId": "legacy", "cases": []}),
            (objectives, {"schemaVersion": "3.2"}), (positioning, {"schemaVersion": "3.2"}),
            (resolved, {"schemaVersion": "3.2"}),
            (control / "stage-state.json", {"schemaVersion": "3.2", "phase": "DRAFT"}),
        ):
            write(path, value)
        lock = {
            "schemaVersion": "3.2", "projectId": "legacy-project", "packageMode": "DEVELOPMENT",
            "packageBinding": {"version": "0.3.7", "sourceKind": "PORTABLE_COPY", "packageManifest": ref(root, package), "runtimeManifest": ref(root, runtime_manifest), "assuranceMatrix": ref(root, matrix)},
            "runtime": ref(root, runtime_manifest), "caseCatalog": ref(root, cases), "keyObjectives": ref(root, objectives),
            "positioning": ref(root, positioning), "resolvedRuleSet": ref(root, resolved),
        }
        write(control / "project-governance-lock.json", lock)
        run(root, "add", ".")
        run(root, "commit", "-m", "legacy control plane")

        original_assert = upgrade_module.assert_dependencies
        original_target = upgrade_module._target_package
        upgrade_module.assert_dependencies = lambda: None
        upgrade_module._target_package = lambda: (ROOT, {
            "version": "0.4.0", "sourceKind": "PORTABLE_COPY",
            "packageManifestSha256": "1" * 64, "runtimeManifestSha256": "2" * 64, "assuranceMatrixSha256": "3" * 64,
        })
        try:
            first = upgrade_module.upgrade_plan(root)["data"]
            second = upgrade_module.upgrade_plan(root)["data"]
        finally:
            upgrade_module.assert_dependencies = original_assert
            upgrade_module._target_package = original_target
        assert first == second
        assert first["operation"] == "schema-runtime-upgrade"
        assert (first["sourceSchemaVersion"], first["targetSchemaVersion"]) == ("3.2", "4.0")
        assert "convert-schema-3.2-to-4.0" in first["actions"]
        assert first["invalidates"] == INVALIDATES


TESTS = [
    test_schema_and_execution_plan,
    test_plan_mutations_fail_closed,
    test_project_derived_audit_policy_is_bounded_not_fixed,
    test_same_risk_can_lock_different_review_workflows,
    test_execution_hash_is_required_downstream,
    test_automatic_default_and_capability_routing,
    test_failure_is_repairable_but_never_claim_eligible,
    test_plain_language_is_universal_and_overrideable,
    test_schema_upgrade_boundary_and_mirrors,
    test_schema_32_upgrade_selects_auto_unless_explicitly_manual,
    test_legacy_32_upgrade_plan_is_deterministic_and_invalidates_facts,
]


def main() -> int:
    passed = 0
    failures: list[dict[str, str]] = []
    for test in TESTS:
        try:
            test()
            passed += 1
        except Exception as exc:  # noqa: BLE001 - deterministic test aggregation
            failures.append({"test": test.__name__, "error": f"{type(exc).__name__}: {exc}"})
    result = {"schemaVersion": "4.0", "total": len(TESTS), "passed": passed, "failed": len(failures), "skipped": 0, "failures": failures}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
