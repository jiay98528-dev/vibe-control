#!/usr/bin/env python3
"""Focused Schema 3.2 checkpoint, finding-scope, and migration regressions."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "assets" / "project-control" / "runtime"
sys.path.insert(0, str(RUNTIME))

from vibe_runtime.checkpoint_control import (  # noqa: E402
    AUDIT_POLICY, checkpoint_contract_checks, checkpoint_set_sha256,
    evaluate_case_oracle, finding_structure_checks, normalize_statement,
    owner_checkpoint_checks, review_checkpoint_checks, statement_id,
    validate_statement_objects,
)
from vibe_runtime.common import ControlError  # noqa: E402
from vibe_runtime import controller as controller_module  # noqa: E402
from vibe_runtime.controller import _atomic_replace_control_plane, _migration_conversion  # noqa: E402


BASELINE = "6067575040fba42cfbfd6268c0b7d77a5d22dd2f"


def _positioning() -> dict:
    signal = "the locked command returns an externally visible success marker"
    gate = "owner confirms the vertical slice is useful"
    return {
        "firstVerticalSlice": {"successSignals": [{"id": statement_id("SIG", signal), "statement": signal}]},
        "humanQualityGates": [{"id": statement_id("HG", gate), "statement": gate}],
    }


def _catalog() -> dict:
    return {"cases": [{"id": "CASE-001", "maxClaimLevel": "ACCEPTED"}]}


def _contract(*, claim: str = "VERIFIED", human: bool = False) -> dict:
    positioning = _positioning()
    checkpoints = [{
        "id": "CP-001", "sourceRefs": [positioning["firstVerticalSlice"]["successSignals"][0]["id"]],
        "objectiveRefs": ["KO-001"], "statement": "the command passes", "type": "AUTOMATED",
        "requiredForClaim": "VERIFIED", "caseIds": ["CASE-001"],
        "assertions": [{"id": "ASRT-001", "statement": "stdout exposes success", "caseIds": ["CASE-001"]}],
        "expected": {"status": "PASS", "minExecuted": 1, "maxFailed": 0, "maxSkipped": 0, "artifacts": "AS_DECLARED"}, "notProven": [],
    }]
    if human:
        checkpoints.append({
            "id": "CP-002", "sourceRefs": [positioning["humanQualityGates"][0]["id"]],
            "objectiveRefs": ["KO-001"], "statement": "owner accepts usefulness", "type": "HUMAN",
            "requiredForClaim": "ACCEPTED", "caseIds": [], "assertions": [],
            "expected": {"status": "PASS", "minExecuted": 1, "maxFailed": 0, "maxSkipped": 0, "artifacts": "AS_DECLARED"}, "notProven": ["automatic product taste"],
        })
    value = {
        "objectiveRefs": ["KO-001", "KF-001"], "requiredCaseIds": ["CASE-001"], "maxClaimLevel": claim,
        "acceptanceCheckpoints": checkpoints, "auditPolicy": AUDIT_POLICY,
        "checkpointConfirmation": {"checkpointSetSha256": "0" * 64},
    }
    value["checkpointConfirmation"]["checkpointSetSha256"] = checkpoint_set_sha256(value)
    return value


def _evidence() -> dict:
    return {"evidenceId": "E-1", "result": "PASS", "counters": {"executed": 1, "passed": 1, "failed": 0, "skipped": 0}}


def _finding(classification: str = "CURRENT_GOAL_DEFECT") -> dict:
    return {
        "id": "F-1", "status": "OPEN", "classification": classification,
        "objectiveRefs": ["KO-001"], "checkpointRefs": ["CP-001"], "coreControlRefs": [],
        "affectedClaims": ["RELEASE_READY"], "evidenceRefs": [{"path": "e.txt"}],
    }


def _expect_control_error(check_id: str, callback) -> ControlError:
    try:
        callback()
    except ControlError as exc:
        assert exc.check_id == check_id, (exc.check_id, check_id)
        return exc
    raise AssertionError(f"expected {check_id}")


def _run(args: list[str], *, cwd: Path | None = None, expect: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)
    assert result.returncode == expect, f"exit={result.returncode} expected={expect}\nstdout={result.stdout}\nstderr={result.stderr}"
    return result


def _run_json(args: list[str], *, cwd: Path | None = None, expect: int) -> dict:
    return json.loads(_run(args, cwd=cwd, expect=expect).stdout)


def _git(root: Path, *args: str) -> str:
    return _run(["git", "-C", str(root), *args]).stdout.strip()


def _commit(root: Path, message: str) -> None:
    _git(root, "add", "-A"); _git(root, "commit", "-m", message)


def _control_snapshot(root: Path) -> dict[str, str]:
    control = root / ".vibe-control"
    return {
        path.relative_to(control).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(value for value in control.rglob("*") if value.is_file())
    }


def _baseline_project(parent: Path, name: str) -> Path:
    project = parent / name
    _run(["git", "clone", "--quiet", "--no-local", str(ROOT), str(project)])
    _git(project, "checkout", "--quiet", "--detach", BASELINE)
    _git(project, "config", "user.email", "fixture@example.invalid")
    _git(project, "config", "user.name", "Fixture")
    shutil.copy2(ROOT / "references" / "0.3.3-migration-spec.json", project / "migration-spec.json")
    _commit(project, "add confirmed migration spec")
    return project


def _development_package(parent: Path) -> Path:
    package = parent / "development-package"
    shutil.copytree(ROOT, package, ignore=shutil.ignore_patterns(".git", ".vibe-control", "__pycache__", "*.pyc"))
    _git(package, "init")
    _git(package, "config", "user.email", "fixture@example.invalid")
    _git(package, "config", "user.name", "Fixture")
    _run([sys.executable, str(package / "scripts" / "build_manifest.py"), "--root", str(package)], cwd=package)
    _commit(package, "materialize development package")
    return package


def test_checkpoint_contract_regressions() -> None:
    contract = _contract()
    assert all(item["status"] == "PASS" for item in checkpoint_contract_checks(contract, _positioning(), _catalog(), "ACCEPTED"))
    missing = copy.deepcopy(contract); missing["acceptanceCheckpoints"][0]["sourceRefs"] = []
    _expect_control_error("HC-CHECKPOINT-SIGNAL-CLOSURE", lambda: checkpoint_contract_checks(missing, _positioning(), _catalog(), "ACCEPTED"))

    duplicate = copy.deepcopy(contract); duplicate["acceptanceCheckpoints"].append(copy.deepcopy(duplicate["acceptanceCheckpoints"][0])); duplicate["acceptanceCheckpoints"][1]["id"] = "CP-002"; duplicate["acceptanceCheckpoints"][1]["assertions"][0]["id"] = "ASRT-002"; duplicate["checkpointConfirmation"]["checkpointSetSha256"] = checkpoint_set_sha256(duplicate)
    _expect_control_error("HC-CHECKPOINT-SIGNAL-CLOSURE", lambda: checkpoint_contract_checks(duplicate, _positioning(), _catalog(), "ACCEPTED"))

    unknown = copy.deepcopy(contract); unknown["acceptanceCheckpoints"][0]["sourceRefs"] = ["SIG-000000000000"]
    _expect_control_error("HC-CHECKPOINT-SOURCE-CLOSURE", lambda: checkpoint_contract_checks(unknown, _positioning(), _catalog(), "ACCEPTED"))
    no_case = copy.deepcopy(contract); no_case["acceptanceCheckpoints"][0]["caseIds"] = []; no_case["acceptanceCheckpoints"][0]["assertions"] = []
    _expect_control_error("HC-CHECKPOINT-AUTOMATED-CLOSURE", lambda: checkpoint_contract_checks(no_case, _positioning(), _catalog(), "ACCEPTED"))
    bad_objective = copy.deepcopy(contract); bad_objective["acceptanceCheckpoints"][0]["objectiveRefs"] = ["KO-999"]
    _expect_control_error("HC-CHECKPOINT-OBJECTIVE-CLOSURE", lambda: checkpoint_contract_checks(bad_objective, _positioning(), _catalog(), "ACCEPTED"))
    bad_claim = copy.deepcopy(contract); bad_claim["acceptanceCheckpoints"][0]["requiredForClaim"] = "ACCEPTED"; bad_claim["checkpointConfirmation"]["checkpointSetSha256"] = checkpoint_set_sha256(bad_claim)
    _expect_control_error("HC-CHECKPOINT-CLAIM-CEILING", lambda: checkpoint_contract_checks(bad_claim, _positioning(), _catalog(), "VERIFIED"))
    drift = copy.deepcopy(contract); drift["acceptanceCheckpoints"][0]["statement"] += " changed"
    _expect_control_error("HC-CHECKPOINT-CONFIRMATION", lambda: checkpoint_contract_checks(drift, _positioning(), _catalog(), "ACCEPTED"))

    accepted_without_human = _contract(claim="ACCEPTED")
    _expect_control_error("HC-CHECKPOINT-HUMAN-CLOSURE", lambda: checkpoint_contract_checks(accepted_without_human, _positioning(), _catalog(), "ACCEPTED"))
    accepted = _contract(claim="ACCEPTED", human=True)
    assert all(item["status"] == "PASS" for item in checkpoint_contract_checks(accepted, _positioning(), _catalog(), "ACCEPTED"))


def test_checkpoint_oracle_and_identity_regressions() -> None:
    assert normalize_statement("  A\tB\nC  ") == "A B C"
    assert statement_id("SIG", "A\tB") == statement_id("SIG", " A B ")
    assert statement_id("SIG", "Case!") != statement_id("SIG", "case!")
    assert statement_id("SIG", "Case!") != statement_id("SIG", "Case")
    validate_statement_objects([{"id": statement_id("SIG", "é"), "statement": "e\u0301"}], "SIG")
    _expect_control_error("HC-CHECKPOINT-SOURCE-DUPLICATE", lambda: validate_statement_objects([
        {"id": statement_id("SIG", "same value"), "statement": "same value"},
        {"id": statement_id("SIG", "same value"), "statement": " same\tvalue "},
    ], "SIG"))
    changed = _contract(); old_hash = checkpoint_set_sha256(changed); changed["acceptanceCheckpoints"][0]["assertions"][0]["statement"] += "!"
    assert checkpoint_set_sha256(changed) != old_hash
    for schema_name in (
        "task-lock", "candidate-manifest", "execution-evidence",
        "review-attestation", "approval-signature", "handoff",
    ):
        schema = json.loads((ROOT / "assets" / "project-control" / "schemas" / f"{schema_name}.schema.json").read_text(encoding="utf-8"))
        assert "checkpointSetSha256" in schema["required"], schema_name
    evidence_schema = json.loads((ROOT / "assets" / "project-control" / "schemas" / "execution-evidence.schema.json").read_text(encoding="utf-8"))
    assert "checkpointIds" in evidence_schema["required"]

    case = {
        "oracle": {"exitCode": 0, "stdoutContainsAll": ["READY", "count=1"], "stderrContainsNone": ["Traceback"]},
        "artifacts": [{"path": "out/result.json", "minBytes": 10}],
    }
    passed, details = evaluate_case_oracle(case, exit_code=0, stdout="READY count=1", stderr="", artifact_sizes={"out/result.json": 10})
    assert passed and not any((details["missingStdout"], details["forbiddenStderr"], details["artifactFailures"]))
    assert not evaluate_case_oracle(case, exit_code=0, stdout="READY", stderr="", artifact_sizes={"out/result.json": 10})[0]
    assert not evaluate_case_oracle(case, exit_code=0, stdout="READY count=1", stderr="Traceback", artifact_sizes={"out/result.json": 10})[0]
    assert not evaluate_case_oracle(case, exit_code=0, stdout="READY count=1", stderr="", artifact_sizes={"out/result.json": 9})[0]
    assert not evaluate_case_oracle(case, exit_code=1, stdout="READY count=1", stderr="", artifact_sizes={"out/result.json": 10})[0]


def test_finding_task_and_claim_scope() -> None:
    contract = _contract()
    objectives = {"objectiveIds": ["KO-001", "KO-005"], "failureModeIds": ["KF-001"]}
    finding = _finding()
    checks = finding_structure_checks(finding, contract, objectives, "VERIFIED")
    assert all(item["status"] == "PASS" for item in checks), checks
    finding["objectiveRefs"] = ["KO-005"]
    checks = finding_structure_checks(finding, contract, objectives, "VERIFIED")
    assert any(item["id"] == "HC-FINDING-TASK-SCOPE" and item["status"] == "FAIL" for item in checks)
    assert not any(item["id"] == "HC-FINDING-CLAIM-ADMISSION" and item["status"] == "BLOCKED" for item in checks)

    release_only = _finding(); release_only["affectedClaims"] = ["RELEASE_READY"]
    assert all(item["status"] == "PASS" for item in finding_structure_checks(release_only, contract, objectives, "VERIFIED"))
    assert any(item["id"] == "HC-FINDING-CLAIM-ADMISSION" and item["status"] == "BLOCKED" for item in finding_structure_checks(release_only, contract, objectives, "RELEASE_READY"))

    minimum = _finding("MINIMUM_CORE_VIOLATION"); minimum["objectiveRefs"] = []; minimum["checkpointRefs"] = []; minimum["coreControlRefs"] = ["RULE-CORE-FAILURE-CONSERVATION"]; minimum["affectedClaims"] = ["VERIFIED", "ACCEPTED", "RELEASE_READY"]
    assert any(item["id"] == "HC-FINDING-CLAIM-ADMISSION" and item["status"] == "BLOCKED" for item in finding_structure_checks(minimum, contract, objectives, "VERIFIED"))
    minimum["coreControlRefs"] = []
    assert any(item["id"] == "HC-FINDING-CORE-REF" and item["status"] == "FAIL" for item in finding_structure_checks(minimum, contract, objectives, "VERIFIED"))

    safety = _finding("SAFETY_OVERRIDE"); safety["objectiveRefs"] = ["KF-001"]; safety["checkpointRefs"] = []; safety["affectedClaims"] = ["VERIFIED", "ACCEPTED", "RELEASE_READY"]
    assert any(item["id"] == "HC-FINDING-CLAIM-ADMISSION" and item["status"] == "BLOCKED" for item in finding_structure_checks(safety, contract, objectives, "VERIFIED"))

    advisory = _finding("PROCESS_WARNING"); advisory["affectedClaims"] = []
    assert not any(item["status"] == "BLOCKED" for item in finding_structure_checks(advisory, contract, objectives, "VERIFIED"))


def test_bounded_exploration_and_stop_closure() -> None:
    contract = _contract(); evidence = {"CASE-001": _evidence()}
    review = {"checkpointResults": [{"checkpointId": "CP-001", "expectedStatus": "PASS", "observedStatus": "PASS", "evidenceIds": ["E-1"], "deviationFindingId": None}], "findings": [], "result": "PASS"}
    assert all(item["status"] == "PASS" for item in review_checkpoint_checks(review, contract, evidence))
    review["findings"] = [{"id": f"I-{index}", "classification": "INVESTIGATION"} for index in range(3)]
    assert all(item["status"] == "PASS" for item in review_checkpoint_checks(review, contract, evidence))
    review["findings"].append({"id": "I-4", "classification": "INVESTIGATION"})
    checks = review_checkpoint_checks(review, contract, evidence)
    assert any(item["id"] == "HC-AUDIT-EXPLORATION-BUDGET" and item["status"] == "FAIL" for item in checks)

    missing = copy.deepcopy(review); missing["findings"] = []; missing["checkpointResults"] = []
    _expect_control_error("HC-CHECKPOINT-REVIEW-CLOSURE", lambda: review_checkpoint_checks(missing, contract, evidence))
    duplicate = copy.deepcopy(review); duplicate["findings"] = []; duplicate["checkpointResults"].append(copy.deepcopy(duplicate["checkpointResults"][0]))
    _expect_control_error("HC-CHECKPOINT-REVIEW-CLOSURE", lambda: review_checkpoint_checks(duplicate, contract, evidence))
    forged = copy.deepcopy(review); forged["findings"] = []; forged["checkpointResults"][0]["observedStatus"] = "FAIL"; forged["checkpointResults"][0]["deviationFindingId"] = "F-MISSING"; forged["result"] = "FAIL"
    checks = review_checkpoint_checks(forged, contract, evidence)
    assert any(item["id"] == "HC-CHECKPOINT-RESULT-MISMATCH" and item["status"] == "FAIL" for item in checks)

    # A failed over-budget review must close the budget for the candidate,
    # rather than letting a new session submit a disjoint set of three.
    import test_v2_support as fx

    temp, root, _ = fx.setup_project(
        risk="R2", task_ceiling="VERIFIED", case_ceiling="VERIFIED",
        release_intent="LOCAL_EXPERIMENT", include_keys=False,
    )
    try:
        fx.execute_and_verify(root)
        control = root / ".vibe-control"
        candidate = fx.load(next((control / "candidates").glob("*.json")))
        evidence_path = fx.main_evidence_path(root); execution = fx.load(evidence_path)

        def attempt(review_id: str, first: int, count: int) -> tuple[int, dict]:
            transcript = control / "reviews" / f"{review_id}.transcript.txt"
            transcript.parent.mkdir(parents=True, exist_ok=True); transcript.write_text("checkpoint review\n", encoding="utf-8")
            fx.commit(root, f"track {review_id} transcript")
            findings = [{
                "id": f"INV-{index:02d}", "severity": "P3", "status": "OPEN", "classification": "INVESTIGATION",
                "objectiveRefs": ["KO-001"], "checkpointRefs": [], "coreControlRefs": [], "affectedClaims": [],
                "reproduction": f"bounded observation {index}", "evidenceRefs": [],
                "minimumFix": "no current-task fix", "addedGovernanceCost": "none",
            } for index in range(first, first + count)]
            value = {
                "schemaVersion": "3.2", "reviewId": review_id, "taskId": "TASK-001",
                "candidateId": candidate["candidateId"], "candidateCommit": candidate["commit"],
                "checkpointSetSha256": candidate["checkpointSetSha256"], "keyObjectives": candidate["keyObjectives"],
                "positioning": candidate["positioning"], "resolvedRuleSet": candidate["resolvedRuleSet"],
                "auditor": {"actorId": "bounded-auditor", "sessionId": review_id.lower()},
                "evidenceIds": [execution["evidenceId"]], "evidenceRefs": [fx.ref(root, evidence_path)],
                "checkpointResults": [{"checkpointId": "CP-001", "expectedStatus": "PASS", "observedStatus": "PASS", "evidenceIds": [execution["evidenceId"]], "deviationFindingId": None}],
                "findings": findings, "transcript": fx.ref(root, transcript), "result": "PASS",
                "reviewedAt": "2026-07-28T05:00:00+00:00",
            }
            review_path = root.parent / f"{review_id}.json"; fx.write(review_path, value)
            result, report = fx.command(root, "audit", "--review", str(review_path), expect=None)
            return result.returncode, report

        code, first_report = attempt("REVIEW-BUDGET-1", 1, 4)
        assert code == 3 and first_report["error"]["id"] == "HC-AUDIT-EXPLORATION-BUDGET"
        closure_path = control / "reviews" / "audit-closures" / f"{candidate['candidateId']}.json"
        closure = fx.load(closure_path)
        assert closure["candidateId"] == candidate["candidateId"] and closure["findingIds"] == ["INV-01", "INV-02", "INV-03", "INV-04"]
        fx.commit(root, "record candidate audit closure")
        code, second_report = attempt("REVIEW-BUDGET-2", 5, 3)
        assert code == 2 and second_report["error"]["id"] == "HC-AUDIT-STOP-CLOSURE"
        assert fx.load(control / "stage-state.json")["phase"] == "VERIFIED"
    finally:
        temp.cleanup()


def test_human_checkpoint_decision_closure() -> None:
    contract = _contract(claim="ACCEPTED", human=True)
    complete = {"checkpointDecisions": [{"checkpointId": "CP-002", "decision": "PASS"}]}
    assert all(item["status"] == "PASS" for item in owner_checkpoint_checks(complete, contract))
    assert any(item["status"] == "BLOCKED" for item in owner_checkpoint_checks({"checkpointDecisions": []}, contract))
    assert any(item["status"] == "BLOCKED" for item in owner_checkpoint_checks({"checkpointDecisions": [
        {"checkpointId": "CP-002", "decision": "PASS"}, {"checkpointId": "CP-002", "decision": "PASS"},
    ]}, contract))
    assert any(item["status"] == "BLOCKED" for item in owner_checkpoint_checks({"checkpointDecisions": [{"checkpointId": "CP-002", "decision": "REJECT"}]}, contract))


def test_schema32_migration_plan_and_apply() -> None:
    old = {"firstVerticalSlice": {"successSignals": [" A  signal "]}, "humanQualityGates": ["A gate"]}
    catalog = {"cases": [{"id": "CASE-001", "oracle": {"exitCode": 0, "stdoutContains": "OK"}, "artifacts": ["out.txt"]}]}
    signals, gates, cases = _migration_conversion(old, catalog)
    assert signals == [{"id": statement_id("SIG", "A signal"), "statement": "A signal"}]
    assert gates == [{"id": statement_id("HG", "A gate"), "statement": "A gate"}]
    assert cases[0]["oracle"] == {"exitCode": 0, "stdoutContainsAll": ["OK"], "stderrContainsNone": []}
    assert cases[0]["artifacts"] == [{"path": "out.txt", "minBytes": 1}]
    reordered_signals, reordered_gates, _ = _migration_conversion(
        {"firstVerticalSlice": {"successSignals": ["second", " A  signal "]}, "humanQualityGates": ["second gate", "A gate"]},
        catalog,
    )
    assert {item["statement"]: item["id"] for item in reordered_signals}["A signal"] == signals[0]["id"]
    assert {item["statement"]: item["id"] for item in reordered_gates}["A gate"] == gates[0]["id"]

    with tempfile.TemporaryDirectory(prefix="vibe-control-atomic-migration-") as temp_name:
        base = Path(temp_name); live = base / ".vibe-control"; staging = base / "staging"; backup = base / "backup"
        live.mkdir(); staging.mkdir(); (live / "old.txt").write_text("old", encoding="utf-8"); (staging / "new.txt").write_text("new", encoding="utf-8")
        real_replace = os.replace; calls = 0

        def fail_second(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected swap failure")
            return real_replace(source, target)

        with mock.patch.object(controller_module.os, "replace", side_effect=fail_second):
            try:
                _atomic_replace_control_plane(live, staging, backup)
            except OSError as exc:
                assert "injected" in str(exc)
            else:
                raise AssertionError("injected atomic-swap failure was ignored")
        assert (live / "old.txt").read_text(encoding="utf-8") == "old"
        assert not backup.exists()


def test_schema32_migration_fail_closed_mutations() -> None:
    assert statement_id("SIG", "Case") != statement_id("SIG", "case")
    assert statement_id("SIG", "value!") != statement_id("SIG", "value")

    # Exercise the public two-stage migration against the exact 0.3.2 control
    # plane. The controller package is rebuilt in an isolated development repo.
    Path("C:/vc33").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="m33-", dir="C:/vc33") as temp_name:
        parent = Path(temp_name)
        package_root = _development_package(parent)
        wrapper = package_root / "scripts" / "vibe_control.py"
        project = _baseline_project(parent, "project")
        spec = project / "migration-spec.json"
        before = _control_snapshot(project)

        discovery = _run_json([sys.executable, str(wrapper), "migrate", "--project", str(project), "--plan"], expect=2)
        assert discovery["status"] == "BLOCKED" and discovery["data"]["specSha256"] is None
        assert discovery["data"]["unresolvedMappings"] and _control_snapshot(project) == before

        bad_confirmation = json.loads(spec.read_text(encoding="utf-8")); bad_confirmation["confirmation"]["summarySha256"] = "0" * 64
        bad_spec = parent / "bad-confirmation.json"; bad_spec.write_text(json.dumps(bad_confirmation), encoding="utf-8")
        report = _run_json([sys.executable, str(wrapper), "migrate", "--project", str(project), "--plan", "--spec", str(bad_spec)], expect=3)
        assert report["error"]["id"] == "HC-MIGRATION-CONFIRMATION" and _control_snapshot(project) == before

        planned = _run_json([sys.executable, str(wrapper), "migrate", "--project", str(project), "--plan", "--spec", str(spec)], expect=2)
        plan_hash = planned["data"]["planHash"]
        report = _run_json([sys.executable, str(wrapper), "migrate", "--project", str(project), "--apply", "0" * 64, "--spec", str(spec)], expect=4)
        assert report["error"]["id"] == "HC-MIGRATION-PLAN-HASH" and _control_snapshot(project) == before

        skill_path = project / "SKILL.md"; skill_path.write_text(skill_path.read_text(encoding="utf-8") + "\ndirty\n", encoding="utf-8")
        report = _run_json([sys.executable, str(wrapper), "migrate", "--project", str(project), "--apply", plan_hash, "--spec", str(spec)], expect=2)
        assert report["error"]["id"] == "HC-WORKTREE-CLEAN" and _control_snapshot(project) == before
        _git(project, "restore", "--", "SKILL.md")

        drift = project / ".vibe-control" / "plan-drift.txt"; drift.write_text("drift", encoding="utf-8"); _commit(project, "change migration source snapshot")
        report = _run_json([sys.executable, str(wrapper), "migrate", "--project", str(project), "--apply", plan_hash, "--spec", str(spec)], expect=4)
        assert report["error"]["id"] == "HC-MIGRATION-PLAN-HASH"
        drift.unlink(); _commit(project, "restore migration source snapshot")

        legacy_evidence = project / ".vibe-control" / "evidence" / "legacy-evidence.json"
        legacy_task = project / ".vibe-control" / "tasks" / "legacy-task.json"
        legacy_evidence.parent.mkdir(); legacy_task.parent.mkdir()
        legacy_evidence.write_text('{"legacy":true}\n', encoding="utf-8"); legacy_task.write_text('{"legacy":true}\n', encoding="utf-8")
        _commit(project, "add legacy downstream records")
        planned = _run_json([sys.executable, str(wrapper), "migrate", "--project", str(project), "--plan", "--spec", str(spec)], expect=2)
        plan_hash = planned["data"]["planHash"]
        report = _run_json([sys.executable, str(wrapper), "migrate", "--project", str(project), "--apply", plan_hash, "--spec", str(spec)], expect=2)
        assert any(item["id"] == "HC-MIGRATION-INVALIDATION" for item in report["integrity"]["checks"]), report

        control = project / ".vibe-control"
        state = json.loads((control / "stage-state.json").read_text(encoding="utf-8"))
        assert (state["phase"], state["health"], state["claimLevel"]) == ("DRAFT", "BLOCKED", "DIAGNOSTIC")
        assert not (control / "evidence").exists() and not (control / "tasks").exists()
        assert (control / "runtime" / "0.3.2").is_dir() and (control / "runtime" / "0.3.4").is_dir()
        archive = control / "legacy" / "schema-3.1" / plan_hash
        assert (archive / "control-plane" / "evidence" / "legacy-evidence.json").is_file()
        manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
        for item in manifest["files"]:
            source = archive / "control-plane" / item["path"]
            assert source.stat().st_size == item["bytes"]
            assert hashlib.sha256(source.read_bytes()).hexdigest() == item["sha256"]
        assert not list(project.glob(".vibe-control.migrate-*.tmp"))
        assert not list(project.glob(".vibe-control.migrate-*.backup"))


TESTS = [
    test_checkpoint_contract_regressions,
    test_checkpoint_oracle_and_identity_regressions,
    test_finding_task_and_claim_scope,
    test_bounded_exploration_and_stop_closure,
    test_human_checkpoint_decision_closure,
    test_schema32_migration_plan_and_apply,
    test_schema32_migration_fail_closed_mutations,
]


if __name__ == "__main__":
    for test in TESTS:
        test()
    print(json.dumps({"status": "PASS", "tests": len(TESTS), "schemaVersion": "3.2"}))
