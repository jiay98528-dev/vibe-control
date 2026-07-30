#!/usr/bin/env python3
"""Audit materialization, path budget, tag aggregation, and development-cap regressions."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()
sys.path.insert(0, str(ROOT / "scripts"))
from check_audit_path import report as path_budget_report  # noqa: E402


def run(*args: str, cwd: Path | None = None, expect: int | None = 0) -> subprocess.CompletedProcess[str]:
    value = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    if expect is not None and value.returncode != expect:
        raise AssertionError(f"exit={value.returncode}, expected={expect}\nstdout={value.stdout}\nstderr={value.stderr}")
    return value


def git(root: Path, *args: str) -> str:
    return run("git", "-C", str(root), *args).stdout.strip()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def source_id(prefix: str, statement: str) -> str:
    normalized = " ".join(statement.strip().split())
    return f"{prefix}-{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:12]}"


def automation_policy(project_id: str) -> dict:
    stop_conditions = sorted([
        "AUTOMATED_CHECKPOINTS_COMPLETE", "HUMAN_CHECKPOINT", "OWNER_DECISION",
        "BOUNDARY_CHANGE", "R3_OR_IRREVERSIBLE_ACTION", "HARD_FAILURE",
        "PUSH_CONFLICT", "USER_INTERRUPT",
    ])
    semantic = {
        "projectId": project_id,
        "mode": "AUTO_LOCAL_TO_REVIEW",
        "commitPolicy": "MILESTONE_COMMITS",
        "pushPolicy": "NONE",
        "stopConditions": stop_conditions,
    }
    summary = json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(summary.encode("utf-8")).hexdigest()
    return {
        "schemaVersion": "1.0", "policyId": f"automation-{digest[:12]}", **semantic,
        "confirmation": {
            "actorId": "owner", "summary": summary, "summarySha256": digest,
            "record": "AUTOMATION_CONFIRMATION.json", "confirmedAt": "2026-07-29T00:00:00+08:00",
        },
    }


def checkpoint_contract() -> dict:
    signal_id = source_id("SIG", "CASE-001 passes")
    checkpoint = {
        "id": "CP-001", "sourceRefs": [signal_id], "objectiveRefs": ["KO-001"],
        "statement": "CASE-001 passes its locked oracle", "type": "AUTOMATED",
        "requiredForClaim": "VERIFIED", "caseIds": ["CASE-001"],
        "assertions": [{"id": "ASRT-001", "statement": "CASE-001 exits successfully", "caseIds": ["CASE-001"]}],
        "expected": {"status": "PASS", "minExecuted": 1, "maxFailed": 0, "maxSkipped": 0, "artifacts": "AS_DECLARED"},
        "notProven": [],
    }
    policy = {
        "strategy": "PROJECT_DERIVED", "maxExploratoryFindings": 3,
        "stopCondition": "ALL_REQUIRED_CHECKPOINTS_REPORTED",
        "requiredReviewRoles": ["INDEPENDENT_AUDITOR"],
        "triggerReasons": ["MILESTONE_CANDIDATE_READY"],
    }
    checkpoint_hash = hashlib.sha256(json.dumps({"acceptanceCheckpoints": [checkpoint], "auditPolicy": policy}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    planning = {
        "milestones": [{
            "id": "MS-001", "outcome": "close the observable fixture outcome", "objectiveRefs": ["KO-001"],
            "dependsOn": [], "workNodes": [{
                "id": "WN-001", "title": "implement and check the fixture", "kind": "IMPLEMENTATION",
                "allowedPaths": ["fixture.py"], "minimumChecks": ["QC-001", "CASE-001"], "ownerRole": "IMPLEMENTER",
            }], "checkpointIds": ["CP-001"], "expectedPassConditions": ["CP-001 reports PASS with no skipped work"],
        }],
        "scorecardPlan": {"weights": {"FUNCTIONALITY": 40, "ROBUSTNESS_SECURITY": 25, "AUDIT": 20, "PROCESS": 15}, "items": [
            {"id": "SC-001", "category": "FUNCTIONALITY", "statement": "fixture behavior works", "checkpointIds": ["CP-001"], "factSources": [{"kind": "CHECKPOINT", "refs": ["CP-001"]}]},
            {"id": "SC-002", "category": "ROBUSTNESS_SECURITY", "statement": "fixture failures stay observable", "checkpointIds": ["CP-001"], "factSources": [{"kind": "CASE", "refs": ["CASE-001"]}]},
            {"id": "SC-003", "category": "AUDIT", "statement": "fixture evidence receives fresh review", "checkpointIds": ["CP-001"], "factSources": [{"kind": "REVIEW", "refs": ["FRESH-INDEPENDENT-REVIEW"]}]},
            {"id": "SC-004", "category": "PROCESS", "statement": "fixture keeps the minimum proof boundary", "checkpointIds": ["CP-001"], "factSources": [{"kind": "CORE_CONTROL", "refs": ["RULE-CORE-OBSERVABLE-CANDIDATE"]}]},
        ]},
        "verificationStrategy": {
            "mode": "CANDIDATE_BOUND", "failureDisposition": "REPAIR_WITHIN_CONTRACT",
            "eligibleObservations": ["runtime-observed"], "requireZeroSkipped": True,
            "checkpointCases": [{"checkpointId": "CP-001", "caseIds": ["CASE-001"]}],
            "implementer": {"quickChecks": [{"id": "QC-001", "command": [sys.executable, "-m", "py_compile", "fixture.py"], "requiredBeforeMilestone": True}]},
            "executor": {"caseIds": ["CASE-001"], "evidenceRequirements": ["candidate-bound transcript", "nonzero counters", "zero skipped cases"]},
            "auditor": {"required": True, "form": "FRESH_INDEPENDENT_REVIEW", "inputs": ["candidate", "case evidence", "checkpoint expectations"], "stopCondition": "ALL_REQUIRED_CHECKPOINTS_REPORTED"},
            "notProven": ["external distribution readiness"],
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
            "nextActions": {"continue": ["continue the locked fixture work"], "repair": ["repair the failed fixture check"], "humanReview": ["review the fixture candidate"]},
        },
    }
    execution_hash = hashlib.sha256(json.dumps(planning, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "schemaVersion": "4.0", "taskId": "TASK-001", "goal": "test cap", "objectiveRefs": ["KO-001"],
        "allowedPaths": ["fixture.py"], "forbiddenPaths": [], "requiredCaseIds": ["CASE-001"],
        "risk": "R2", "maxClaimLevel": "ACCEPTED", "authorityRefs": ["REQUIREMENTS.md"],
        "nonGoals": [], "humanDecisionPoints": [], "acceptanceCheckpoints": [checkpoint],
        "checkpointConfirmation": {"actorId": "owner", "summary": "checkpoint contract confirmed", "checkpointSetSha256": checkpoint_hash, "executionPlanSha256": execution_hash, "record": "CHECKPOINT_CONFIRMATION.json", "confirmedAt": "2026-07-28T00:00:00+08:00"},
        "auditPolicy": policy, **planning,
    }


def test_checkout_protocol_is_consistent() -> None:
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assurance = (ROOT / "references" / "controller-assurance.md").read_text(encoding="utf-8")
    command = "clone --no-local --branch <candidate-tag-or-branch> --single-branch"
    assert "[controller-assurance.md](references/controller-assurance.md)" in skill
    assert command in assurance
    assert "clone --no-local --no-checkout" not in skill and "clone --no-local --no-checkout" not in assurance


def test_audit_path_budget_blocks_long_root_and_accepts_short_root() -> None:
    with tempfile.TemporaryDirectory(prefix="vc-path-budget-") as temp:
        root = Path(temp) / "repo"; root.mkdir(); git(root, "init"); git(root, "config", "user.email", "fixture@example.invalid"); git(root, "config", "user.name", "Fixture")
        relative = "tracked/" + ("x" * 157)
        path = root / relative; path.parent.mkdir(); path.write_text("x", encoding="utf-8")
        git(root, "add", "-A"); git(root, "commit", "-m", "long path")
        long_root = Path("C:/" + ("a" * 99))
        blocked = path_budget_report(root, "HEAD", long_root, 240)
        passed = path_budget_report(root, "HEAD", Path("C:/vc32/a"), 240)
        assert blocked["status"] == "BLOCKED" and blocked["checkId"] == "VC-AUDIT-PATH-BUDGET"
        assert blocked["details"]["projectedCharacters"] > 240
        assert passed["status"] == "PASS"


def test_missing_release_and_audit_tags_are_aggregated() -> None:
    import test_package_release_audit as package_fixture

    temp, root = package_fixture.package_copy()
    try:
        report = package_fixture.package_report(root, expect=3)
        failed = {item["id"] for item in report["checks"] if item["status"] != "PASS"}
        assert {"PKG-AUDIT-RELEASE-TAG-MISSING", "PKG-AUDIT-REPORT-TAG-MISSING", "PKG-AUDIT-CONTENT-CLOSURE"} <= failed

        runtime_file = root / "assets" / "project-control" / "runtime" / "vibe_runtime" / "common.py"
        runtime_file.write_text(runtime_file.read_text(encoding="utf-8") + "\n# stale-inventory aggregation mutation\n", encoding="utf-8", newline="\n")
        git(root, "add", runtime_file.relative_to(root).as_posix())
        git(root, "commit", "-m", "mutate without rebuilding manifests")
        drifted = package_fixture.package_report(root, expect=3)
        drifted_ids = {item["id"] for item in drifted["checks"] if item["status"] != "PASS"}
        assert {"PKG-AUDIT-RELEASE-TAG-MISSING", "PKG-AUDIT-REPORT-TAG-MISSING", "RUNTIME-MANIFEST-VERIFY", "PKG-MANIFEST-VERIFY"} <= drifted_ids
    finally:
        temp.cleanup()


def test_development_package_never_grants_high_claims() -> None:
    with tempfile.TemporaryDirectory(prefix="vc-development-package-") as temp:
        base = Path(temp); skill = base / "skill"
        shutil.copytree(ROOT, skill, ignore=shutil.ignore_patterns(".git", ".vibe-control", "__pycache__", "*.pyc"))
        git(skill, "init"); git(skill, "config", "user.email", "fixture@example.invalid"); git(skill, "config", "user.name", "Fixture")
        run(sys.executable, str(skill / "scripts" / "build_manifest.py"), "--root", str(skill), cwd=skill)
        git(skill, "add", "-A"); git(skill, "commit", "-m", "development candidate")
        dev = json.loads(run(sys.executable, "-c", "import json,sys;sys.path.insert(0,r'" + str(skill / "assets" / "project-control" / "runtime") + "');from vibe_runtime.package_release import validate_development_package;print(json.dumps(validate_development_package(__import__('pathlib').Path(r'" + str(skill) + "'))))").stdout)
        assert dev["status"] == "PASS" and dev["formalClaimsAllowed"] is False and dev["maxClaimLevel"] == "DEVELOPMENT_CHECKED", dev

        project = base / "project"; project.mkdir(); git(project, "init"); git(project, "config", "user.email", "fixture@example.invalid"); git(project, "config", "user.name", "Fixture")
        (project / "REQUIREMENTS.md").write_text("# Requirement\n", encoding="utf-8")
        (project / "POSITIONING_CONFIRMATION.json").write_text("{}\n", encoding="utf-8")
        (project / "OBJECTIVES_CONFIRMATION.json").write_text("{}\n", encoding="utf-8")
        (project / "CHECKPOINT_CONFIRMATION.json").write_text("{}\n", encoding="utf-8")
        (project / "AUTOMATION_CONFIRMATION.json").write_text("{}\n", encoding="utf-8")
        (project / "KEY_OBJECTIVES.md").write_text("# Objectives\n- `KO-001`: outcome\n- `KF-001`: false proof\n- `NG-001`: deployment\n", encoding="utf-8", newline="\n")
        git(project, "add", "-A"); git(project, "commit", "-m", "requirements")
        positioning = {"primaryExperience":"SERVICE","capabilityDomains":["BACKEND_API"],"deliveryObjective":"VERTICAL_SLICE","releaseIntent":"PRIVATE_OPERATION","runtimeTargets":["python"],"targetEnvironments":[{"id":"dev","operatingSystem":"Windows","deviceClass":"desktop"}],"distributionChannels":[],"humanQualityGates":[],"nonGoals":[],"firstVerticalSlice":{"outcome":"x","included":["one command"],"excluded":[],"successSignals":[{"id":source_id("SIG","CASE-001 passes"),"statement":"CASE-001 passes"}]}}
        positioning_hash = hashlib.sha256(json.dumps(positioning, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        objective_summary = "confirmed"
        spec = {"schemaVersion":"4.0","projectId":"dev-fixture",**positioning,"confirmation":{"actorId":"owner","summary":"positioning","summarySha256":positioning_hash,"record":"POSITIONING_CONFIRMATION.json"},"keyObjectives":{"document":"KEY_OBJECTIVES.md","documentId":"DEV-OBJECTIVES","revision":1,"status":"CONFIRMED","sourceDocuments":["REQUIREMENTS.md"],"objectiveIds":["KO-001"],"failureModeIds":["KF-001"],"nonGoalIds":["NG-001"],"confirmation":{"actorId":"owner","summary":objective_summary,"summarySha256":hashlib.sha256(objective_summary.encode()).hexdigest(),"record":"OBJECTIVES_CONFIRMATION.json"}},"automationPolicy":automation_policy("dev-fixture"),"capabilityProfiles":[],"profileBindings":[],"runtimeAdapters":["generic-command"],"skillBindings":[],"projectOverlay":[],"authorityFiles":["REQUIREMENTS.md"],"trustedKeys":[],"cases":[{"id":"CASE-001","command":[sys.executable,"fixture.py"],"observation":"runtime-observed","maxClaimLevel":"ACCEPTED","oracle":{"exitCode":0,"stdoutContainsAll":[],"stderrContainsNone":[]},"artifacts":[],"satisfiesRuleIds":["RULE-CORE-OBSERVABLE-CANDIDATE","RULE-CORE-FAILURE-CONSERVATION","RULE-PROFILE-API-CONTRACT","RULE-ADAPTER-GENERIC_COMMAND"],"capabilities":["candidate-integrity","failure-conservation","api-contract-runtime","generic-command-execution"],"adapter":{"id":"generic-command","version":"1.0.0","sha256":"pending"}}]}
        adapters = json.loads((skill / "assets" / "project-control" / "runtime" / "rules" / "v1" / "adapters.json").read_text(encoding="utf-8"))
        descriptor = next(item for item in adapters["adapters"] if item["id"] == "generic-command")
        spec["cases"][0]["adapter"]["sha256"] = hashlib.sha256(json.dumps(descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        spec_path = base / "bootstrap.json"; write_json(spec_path, spec)
        wrapper = skill / "scripts" / "vibe_control.py"
        bootstrap = run(sys.executable, str(wrapper), "bootstrap", "--project", str(project), "--spec", str(spec_path), expect=2)
        bootstrap_report = json.loads(bootstrap.stdout); assert bootstrap_report["data"]["packageMode"] == "DEVELOPMENT"
        git(project, "add", "-A"); git(project, "commit", "-m", "bootstrap development")
        contract = checkpoint_contract()
        contract_path = project / ".vibe-control" / "tasks" / "TASK-001.json"; write_json(contract_path, contract); git(project, "add", "-A"); git(project, "commit", "-m", "task")
        pinned = project / ".vibe-control" / "runtime" / RUNTIME_VERSION / "control.py"
        run(sys.executable, str(pinned), "lock-task", "--project", str(project), "--contract", str(contract_path))
        git(project, "add", "-A"); git(project, "commit", "-m", "lock")
        release = json.loads(run(sys.executable, str(pinned), "release-check", "--project", str(project), expect=2).stdout)
        assert release["formal"]["eligible"] is False and release["formal"]["maxClaimLevel"] == "DEVELOPMENT_CHECKED"
        assert "HC-DEVELOPMENT-PACKAGE-CLAIM-CAP" in release["formal"]["blockers"]

        state_path = project / ".vibe-control" / "stage-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.update({"phase": "RELEASE_READY", "health": "CLEAR", "claimLevel": "RELEASE_READY"})
        write_json(state_path, state); git(project, "add", state_path.relative_to(project).as_posix()); git(project, "commit", "-m", "attempt high development claim")
        rejected = json.loads(run(sys.executable, str(pinned), "validate", "--project", str(project), expect=3).stdout)
        assert rejected["formal"]["eligible"] is False and rejected["formal"]["maxClaimLevel"] == "DEVELOPMENT_CHECKED"
        assert {"HC-DEVELOPMENT-PACKAGE-CLAIM-CAP", "HC-STATE-DERIVED-MISMATCH"} <= set(rejected["formal"]["blockers"])


def main() -> int:
    tests = [test_checkout_protocol_is_consistent, test_audit_path_budget_blocks_long_root_and_accepts_short_root, test_missing_release_and_audit_tags_are_aggregated, test_development_package_never_grants_high_claims]
    failures = []
    for item in tests:
        try:
            item()
        except Exception as exc:
            failures.append({"test": item.__name__, "error": str(exc)})
    print(json.dumps({"status": "PASS" if not failures else "FAIL", "passed": len(tests) - len(failures), "total": len(tests), "failures": failures}, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
