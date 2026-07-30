#!/usr/bin/env python3
"""Schema 4.0 key-objective restraint regressions and historical migration checks."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "assets" / "project-control" / "runtime"
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(ROOT / "scripts"))

from vibe_runtime.common import ControlError  # noqa: E402
from vibe_runtime.controller import (  # noqa: E402
    _key_objectives_from_spec, assert_objective_refs, key_objective_checks,
    objective_path_changes, review_finding_checks,
)
from vibe_runtime.schema import validate_object  # noqa: E402


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def objective_source(*, revision: int = 1, objective_ids: list[str] | None = None, confirmation: str = "OBJECTIVES_CONFIRMATION.json") -> dict:
    summary = f"confirmed objective revision {revision}"
    return {
        "document": "KEY_OBJECTIVES.md", "documentId": "FIXTURE-OBJECTIVES", "revision": revision, "status": "CONFIRMED",
        "sourceDocuments": ["REQUIREMENTS.md"], "objectiveIds": objective_ids or ["KO-001"],
        "failureModeIds": ["KF-001"], "nonGoalIds": ["NG-001"],
        "confirmation": {"actorId": "owner", "summary": summary, "summarySha256": hashlib.sha256(summary.encode()).hexdigest(), "record": confirmation},
    }


def small_project() -> tuple[tempfile.TemporaryDirectory, Path]:
    temp = tempfile.TemporaryDirectory(prefix="vibe-control-objectives-")
    root = Path(temp.name) / "project"; root.mkdir()
    git(root, "init"); git(root, "config", "user.email", "fixture@example.invalid"); git(root, "config", "user.name", "Fixture")
    (root / "REQUIREMENTS.md").write_text("# Requirements\n", encoding="utf-8")
    (root / "KEY_OBJECTIVES.md").write_text("# Objectives\n\n- `KO-001`: outcome\n- `KF-001`: false proof\n- `NG-001`: deployment\n", encoding="utf-8", newline="\n")
    write_json(root / "OBJECTIVES_CONFIRMATION.json", {"decision": "CONFIRM"})
    git(root, "add", "-A"); git(root, "commit", "-m", "authority")
    return temp, root


def test_key_objective_schema_and_reference_closure() -> None:
    temp, root = small_project()
    try:
        value = _key_objectives_from_spec(root, {"keyObjectives": objective_source()})
        checks = key_objective_checks(root, value)
        assert checks and all(item["status"] == "PASS" for item in checks)
        assert value["objectiveIds"] == ["KO-001"] and value["document"]["tracked"] is True
    finally:
        temp.cleanup()


def test_installed_key_objective_document_has_a_non_colliding_identity() -> None:
    summary = "root objective restraint confirmation"
    source = {
        "document": "KEY_OBJECTIVES.md", "documentId": "KOD-VIBE-CONTROL", "revision": 1, "status": "CONFIRMED",
        "sourceDocuments": ["SKILL.md", "references/incident-2026-07-25.md", "references/controller-assurance.md"],
        "objectiveIds": [f"KO-{index:03d}" for index in range(1, 6)],
        "failureModeIds": [f"KF-{index:03d}" for index in range(1, 6)],
        "nonGoalIds": [f"NG-{index:03d}" for index in range(1, 6)],
        "confirmation": {
            "actorId": "owner", "summary": summary,
            "summarySha256": hashlib.sha256(summary.encode()).hexdigest(),
            "record": "references/0.3.2-consolidated-confirmation.json",
        },
    }
    value = _key_objectives_from_spec(ROOT, {"keyObjectives": source})
    assert value["documentId"] == "KOD-VIBE-CONTROL"
    assert all(item["status"] == "PASS" for item in key_objective_checks(ROOT, value))


def test_task_unknown_objective_fails() -> None:
    objective_lock = {"objectiveIds": ["KO-001"], "failureModeIds": ["KF-001"]}
    try:
        assert_objective_refs({"objectiveRefs": ["KO-UNKNOWN"]}, objective_lock)
    except ControlError as exc:
        assert exc.check_id == "HC-TASK-OBJECTIVE-CLOSURE"
    else:
        raise AssertionError("unknown objective reference was accepted")

    missing = json.loads((ROOT / "assets" / "project-control" / "templates" / "task-contract-light.json").read_text(encoding="utf-8-sig"))
    missing["taskId"] = "TASK-NO-OBJECTIVE"
    missing.pop("objectiveRefs")
    try:
        validate_object("task-contract", missing)
    except ControlError as exc:
        assert exc.check_id == "HC-SCHEMA-CONTRACT"
    else:
        raise AssertionError("task without objectiveRefs was accepted")


def test_missing_and_untracked_objectives_fail_closed() -> None:
    first, root = small_project()
    try:
        (root / "KEY_OBJECTIVES.md").unlink()
        try:
            _key_objectives_from_spec(root, {"keyObjectives": objective_source()})
        except ControlError as exc:
            assert exc.check_id == "HC-FILE-MISSING"
        else:
            raise AssertionError("missing KEY_OBJECTIVES.md was accepted")
    finally:
        first.cleanup()

    second, root = small_project()
    try:
        git(root, "rm", "--cached", "KEY_OBJECTIVES.md")
        try:
            _key_objectives_from_spec(root, {"keyObjectives": objective_source()})
        except ControlError as exc:
            assert exc.check_id == "HC-FILE-TRACKED"
        else:
            raise AssertionError("untracked KEY_OBJECTIVES.md was accepted")
    finally:
        second.cleanup()


def test_objective_drift_and_worker_or_reviewer_write_fail_closed() -> None:
    temp, root = small_project()
    try:
        value = _key_objectives_from_spec(root, {"keyObjectives": objective_source()})
        (root / "KEY_OBJECTIVES.md").write_text("# drift\n- `KO-001`: changed\n- `KF-001`: false proof\n- `NG-001`: deployment\n", encoding="utf-8", newline="\n")
        checks = key_objective_checks(root, value)
        assert any(item["id"] == "HC-OBJECTIVES-DOCUMENT" and item["status"] == "INVALIDATED" for item in checks)
        assert objective_path_changes(["src/app.py", "KEY_OBJECTIVES.md"]) == ["KEY_OBJECTIVES.md"]
    finally:
        temp.cleanup()


def test_revise_objectives_invalidates_downstream() -> None:
    import test_v2_support as fx

    temp, root, _ = fx.setup_project(risk="R1", task_ceiling="VERIFIED")
    try:
        old_receipt = root / ".vibe-control" / "runtime" / fx.RUNTIME_VERSION / "release-receipt.json"
        write_json(old_receipt, {"diagnostic": "old receipt must not survive objective revision"})
        git(root, "add", "-f", old_receipt.relative_to(root).as_posix()); git(root, "commit", "-m", "record old diagnostic receipt")
        (root / "KEY_OBJECTIVES.md").write_text("# Fixture objectives\n\n- `KO-001`: prove outcome\n- `KO-002`: keep scope\n- `KF-001`: prevent false evidence\n- `NG-001`: no deployment\n", encoding="utf-8", newline="\n")
        write_json(root / "OBJECTIVES_CONFIRMATION_R2.json", {"decision": "CONFIRM"})
        git(root, "add", "KEY_OBJECTIVES.md", "OBJECTIVES_CONFIRMATION_R2.json")
        source = objective_source(revision=2, objective_ids=["KO-001", "KO-002"], confirmation="OBJECTIVES_CONFIRMATION_R2.json")
        source["sourceDocuments"] = ["PROJECT_BRIEF.md"]
        spec_path = Path(root.parent) / "objective-revision.json"; write_json(spec_path, {"schemaVersion": "4.0", "projectId": "fixture", "keyObjectives": source})
        _, planned = fx.command(root, "revise-objectives", "--spec", str(spec_path), "--plan", expect=2)
        _, applied = fx.command(root, "revise-objectives", "--spec", str(spec_path), "--apply", planned["data"]["planHash"], expect=2)
        state = fx.load(root / ".vibe-control" / "stage-state.json")
        assert state["phase"] == "DRAFT" and state["claimLevel"] == "DIAGNOSTIC"
        assert applied["formal"]["eligible"] is False and "candidate" in applied["data"]["invalidated"]
        assert any((root / ".vibe-control" / "legacy").glob("objectives-r1-*/candidates"))
        assert not old_receipt.exists() and any((root / ".vibe-control" / "legacy").glob("objectives-r1-*/release-receipt.json"))
    finally:
        temp.cleanup()


def test_finding_admission_respects_classification() -> None:
    objective_lock = {"objectiveIds": ["KO-001"], "failureModeIds": ["KF-001"]}
    contract = {"objectiveRefs": ["KO-001"], "acceptanceCheckpoints": [{"id": "CP-001", "objectiveRefs": ["KO-001"]}]}
    evidence_path = ROOT / "KEY_OBJECTIVES.md"
    evidence_ref = {"path": "KEY_OBJECTIVES.md", "bytes": evidence_path.stat().st_size, "sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(), "tracked": True}
    base = {"severity": "P1", "status": "OPEN", "checkpointRefs": [], "coreControlRefs": [], "affectedClaims": [], "reproduction": "repeat", "evidenceRefs": [evidence_ref], "minimumFix": "minimal fix", "addedGovernanceCost": "one check"}
    advisory = {"findings": [
        {**base, "id": "W-1", "classification": "PROCESS_WARNING", "objectiveRefs": []},
        {**base, "id": "F-1", "classification": "FUTURE_PROPOSAL", "objectiveRefs": []},
    ]}
    advisory_checks = review_finding_checks(ROOT, advisory, objective_lock, "VERIFIED", contract)
    assert all(item["status"] == "PASS" for item in advisory_checks)
    unmapped_safety = {"findings": [{**base, "id": "S-0", "classification": "SAFETY_OVERRIDE", "objectiveRefs": [], "affectedClaims": ["VERIFIED", "ACCEPTED", "RELEASE_READY"], "evidenceRefs": []}]}
    unmapped_checks = review_finding_checks(ROOT, unmapped_safety, objective_lock, "VERIFIED", contract)
    assert any(item["id"] == "HC-FINDING-TASK-SCOPE" and item["status"] == "FAIL" for item in unmapped_checks)
    assert not any(item["id"] == "HC-FINDING-CLAIM-ADMISSION" and item["status"] == "BLOCKED" for item in unmapped_checks)
    safety = {"findings": [{**base, "id": "S-1", "classification": "SAFETY_OVERRIDE", "objectiveRefs": ["KF-001"], "affectedClaims": ["VERIFIED", "ACCEPTED", "RELEASE_READY"]}]}
    minimum = {"findings": [{**base, "id": "M-1", "classification": "MINIMUM_CORE_VIOLATION", "objectiveRefs": [], "coreControlRefs": ["RULE-CORE-OBSERVABLE-CANDIDATE"], "affectedClaims": ["VERIFIED", "ACCEPTED", "RELEASE_READY"]}]}
    assert any(item["status"] == "BLOCKED" for item in review_finding_checks(ROOT, safety, objective_lock, "VERIFIED", contract))
    assert any(item["status"] == "BLOCKED" for item in review_finding_checks(ROOT, minimum, objective_lock, "VERIFIED", contract))
    human = {"findings": [{**base, "id": "H-1", "classification": "HUMAN_DECISION", "objectiveRefs": ["KO-001"], "affectedClaims": ["ACCEPTED", "RELEASE_READY"]}]}
    assert all(item["status"] == "PASS" for item in review_finding_checks(ROOT, human, objective_lock, "VERIFIED", contract))
    assert any(item["status"] == "BLOCKED" for item in review_finding_checks(ROOT, human, objective_lock, "ACCEPTED", contract))


def test_review_schema_requires_bound_direct_blockers() -> None:
    schema = json.loads((ROOT / "assets" / "project-control" / "schemas" / "review-attestation.schema.json").read_text(encoding="utf-8-sig"))
    validator = Draft202012Validator(schema)
    ref = {"path": "evidence.json", "bytes": 1, "sha256": "0" * 64, "tracked": True}
    base_finding = {
        "id": "F-1", "severity": "P1", "status": "OPEN", "classification": "SAFETY_OVERRIDE",
        "objectiveRefs": ["KF-001"], "checkpointRefs": [], "coreControlRefs": [],
        "affectedClaims": ["VERIFIED", "ACCEPTED", "RELEASE_READY"], "reproduction": "repeat",
        "evidenceRefs": [ref], "minimumFix": "minimal fix", "addedGovernanceCost": "one check",
    }
    review = {
        "schemaVersion": "4.0", "reviewId": "REVIEW-1", "taskId": "TASK-1", "candidateId": "candidate-1",
        "candidateCommit": "a" * 40, "checkpointSetSha256": "b" * 64, "executionPlanSha256": "c" * 64,
        "keyObjectives": ref, "positioning": ref, "resolvedRuleSet": ref,
        "reviewForm": "FRESH_INDEPENDENT_REVIEW", "reviewRoles": ["INDEPENDENT_AUDITOR"],
        "auditor": {"actorId": "auditor", "sessionId": "session"}, "evidenceIds": ["E-1"], "evidenceRefs": [ref],
        "checkpointResults": [{"checkpointId": "CP-001", "expectedStatus": "PASS", "observedStatus": "FAIL", "evidenceIds": ["E-1"], "deviationFindingId": "F-1"}],
        "findings": [base_finding], "transcript": ref, "result": "FAIL", "reviewedAt": "2026-07-27T00:00:00+08:00",
    }
    assert not list(validator.iter_errors(review))
    review["findings"] = [{**base_finding, "objectiveRefs": [], "evidenceRefs": []}]
    assert list(validator.iter_errors(review)), "unmapped and evidence-free safety override passed Schema 4.0"
    review["findings"] = [{**base_finding, "classification": "CURRENT_GOAL_DEFECT", "objectiveRefs": ["KO-001"], "affectedClaims": [], "evidenceRefs": []}]
    assert list(validator.iter_errors(review)), "claimless and evidence-free current-goal blocker passed Schema 4.0"


def main() -> int:
    tests = [test_key_objective_schema_and_reference_closure, test_installed_key_objective_document_has_a_non_colliding_identity, test_task_unknown_objective_fails, test_missing_and_untracked_objectives_fail_closed, test_objective_drift_and_worker_or_reviewer_write_fail_closed, test_revise_objectives_invalidates_downstream, test_finding_admission_respects_classification, test_review_schema_requires_bound_direct_blockers]
    import test_v033_checkpoints as checkpoint_regressions
    tests.extend(checkpoint_regressions.TESTS)
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
