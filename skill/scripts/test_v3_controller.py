#!/usr/bin/env python3
"""Black-box and controller-boundary tests for the Schema 3.2 control plane."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "assets" / "project-control" / "runtime"
CONTROL = RUNTIME / "control.py"
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(ROOT / "scripts"))

from vibe_runtime import cli  # noqa: E402
from vibe_runtime.common import ControlError  # noqa: E402
from vibe_runtime.controller import initial_state  # noqa: E402
from vibe_runtime.positioning_control import positioning_summary  # noqa: E402
from vibe_runtime.schema import validate_object  # noqa: E402


def _run(*args: str, expect: int | None = None) -> tuple[subprocess.CompletedProcess[str], dict]:
    result = subprocess.run(list(args), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    if expect is not None and result.returncode != expect:
        raise AssertionError(f"exit={result.returncode}, expected={expect}\nstdout={result.stdout}\nstderr={result.stderr}")
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"controller leaked non-JSON output: {result.stdout!r} / {result.stderr!r}") from exc
    return result, report


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _adapter_binding(adapter_id: str = "generic-command") -> dict:
    catalog = json.loads((RUNTIME / "rules" / "v1" / "adapters.json").read_text(encoding="utf-8-sig"))
    descriptor = next(item for item in catalog["adapters"] if item["id"] == adapter_id)
    digest = hashlib.sha256(json.dumps(descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"id": descriptor["id"], "version": descriptor["version"], "sha256": digest}


def _source_id(prefix: str, statement: str) -> str:
    normalized = " ".join(statement.strip().split())
    return f"{prefix}-{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:12]}"


def _task_contract(*, task_id: str = "TASK-001", risk: str = "R2", max_claim: str = "ACCEPTED") -> dict:
    automated = {
        "id": "CP-001", "sourceRefs": [_source_id("SIG", "locked case passes")],
        "objectiveRefs": ["KO-001"], "statement": "the locked case passes", "type": "AUTOMATED",
        "requiredForClaim": "VERIFIED", "caseIds": ["CASE-001"],
        "assertions": [{"id": "ASRT-001", "statement": "the locked command emits OK", "caseIds": ["CASE-001"]}],
        "expected": {"status": "PASS", "minExecuted": 1, "maxFailed": 0, "maxSkipped": 0, "artifacts": "AS_DECLARED"},
        "notProven": [],
    }
    checkpoints = [automated]
    if max_claim in {"ACCEPTED", "RELEASE_READY"}:
        checkpoints.append({
            "id": "CP-002", "sourceRefs": [_source_id("HG", "owner accepts the first slice")],
            "objectiveRefs": ["KO-001"], "statement": "owner accepts the first slice", "type": "HUMAN",
            "requiredForClaim": "ACCEPTED", "caseIds": [], "assertions": [],
            "expected": {"status": "PASS", "minExecuted": 1, "maxFailed": 0, "maxSkipped": 0, "artifacts": "AS_DECLARED"},
            "notProven": ["subjective owner judgment"],
        })
    policy = {"mode": "CONFORMANCE_PLUS_BOUNDED_EXPLORATION", "maxExploratoryFindings": 3, "stopCondition": "ALL_REQUIRED_CHECKPOINTS_REPORTED"}
    digest = hashlib.sha256(json.dumps({"acceptanceCheckpoints": checkpoints, "auditPolicy": policy}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "schemaVersion": "3.2", "taskId": task_id, "goal": "prove case coverage",
        "objectiveRefs": ["KO-001"], "allowedPaths": ["fixture.py"], "forbiddenPaths": [],
        "requiredCaseIds": ["CASE-001"], "risk": risk, "maxClaimLevel": max_claim,
        "authorityRefs": ["PROJECT_BRIEF.md"], "nonGoals": [], "humanDecisionPoints": ["acceptance"] if max_claim in {"ACCEPTED", "RELEASE_READY"} else [],
        "acceptanceCheckpoints": checkpoints,
        "checkpointConfirmation": {"actorId": "owner", "summary": "fixture checkpoints confirmed", "checkpointSetSha256": digest, "record": "CHECKPOINT_CONFIRMATION.json", "confirmedAt": "2026-07-28T00:00:00+08:00"},
        "auditPolicy": policy,
    }


def _spec(*, confirmed: bool = True, incomplete_case_coverage: bool = False) -> dict:
    summary = "SERVICE|BACKEND_API|VERTICAL_SLICE|PRIVATE_OPERATION|local-python"
    satisfies = ["RULE-CORE-OBSERVABLE-CANDIDATE"] if incomplete_case_coverage else ["RULE-CORE-OBSERVABLE-CANDIDATE", "RULE-CORE-FAILURE-CONSERVATION", "RULE-PROFILE-API-CONTRACT", "RULE-ADAPTER-GENERIC_COMMAND"]
    value = {
        "schemaVersion": "3.2",
        "projectId": "fixture-v3",
        "primaryExperience": "SERVICE",
        "capabilityDomains": ["BACKEND_API"],
        "deliveryObjective": "VERTICAL_SLICE",
        "releaseIntent": "PRIVATE_OPERATION",
        "runtimeTargets": ["python-local"],
        "targetEnvironments": [{"id": "dev-win", "operatingSystem": "Windows", "deviceClass": "desktop", "architecture": "x86_64"}],
        "distributionChannels": ["private-local"],
        "humanQualityGates": [{"id": _source_id("HG", "owner accepts the first slice"), "statement": "owner accepts the first slice"}],
        "nonGoals": ["external release"],
        "firstVerticalSlice": {
            "outcome": "one API command completes",
            "included": ["fixture command"],
            "excluded": ["deployment"],
            "successSignals": [{"id": _source_id("SIG", "locked case passes"), "statement": "locked case passes"}],
        },
        "confirmation": {"actorId": "owner", "summary": summary, "summarySha256": "pending", "record": "POSITIONING_CONFIRMATION.json"},
        "keyObjectives": {
            "document": "KEY_OBJECTIVES.md", "documentId": "FIXTURE-V3-OBJECTIVES", "revision": 1, "status": "CONFIRMED",
            "sourceDocuments": ["PROJECT_BRIEF.md"], "objectiveIds": ["KO-001"], "failureModeIds": ["KF-001"], "nonGoalIds": ["NG-001"],
            "confirmation": {"actorId": "owner", "summary": "fixture objectives confirmed", "summarySha256": hashlib.sha256(b"fixture objectives confirmed").hexdigest(), "record": "OBJECTIVES_CONFIRMATION.json"},
        },
        "capabilityProfiles": [{"id": "backend-api"}],
        "profileBindings": [{"id": "backend-api"}],
        "runtimeAdapters": ["generic-command"],
        "skillBindings": [],
        "projectOverlay": [],
        "authorityFiles": ["PROJECT_BRIEF.md"],
        "trustedKeys": [],
        "cases": [{
            "id": "CASE-001",
            "command": [sys.executable, "fixture.py"],
            "observation": "runtime-observed",
            "maxClaimLevel": "ACCEPTED",
            "oracle": {"exitCode": 0, "stdoutContainsAll": ["OK"], "stderrContainsNone": []},
            "artifacts": [],
            "satisfiesRuleIds": satisfies,
            "capabilities": ["generic-command-execution", "api-contract-runtime", "candidate-integrity", "failure-conservation"],
            "adapter": _adapter_binding(),
        }],
    }
    if incomplete_case_coverage:
        value["cases"].append({
            "id": "CASE-002", "command": [sys.executable, "fixture.py"], "observation": "runtime-observed",
            "maxClaimLevel": "ACCEPTED", "oracle": {"exitCode": 0, "stdoutContainsAll": ["OK"], "stderrContainsNone": []}, "artifacts": [],
            "satisfiesRuleIds": ["RULE-CORE-FAILURE-CONSERVATION", "RULE-PROFILE-API-CONTRACT", "RULE-ADAPTER-GENERIC_COMMAND"],
            "capabilities": ["failure-conservation", "api-contract-runtime", "generic-command-execution"], "adapter": _adapter_binding(),
        })
    summary_hash = hashlib.sha256(json.dumps(positioning_summary(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    value["confirmation"]["summarySha256"] = summary_hash if confirmed else "0" * 64
    return value


def _new_project(base: Path) -> Path:
    root = base / "project"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "Fixture")
    (root / "PROJECT_BRIEF.md").write_text("# Fixture\n", encoding="utf-8")
    (root / "POSITIONING_CONFIRMATION.json").write_text('{"actorId":"owner","decision":"CONFIRM"}\n', encoding="utf-8")
    (root / "KEY_OBJECTIVES.md").write_text("# Objectives\n\n- `KO-001`: outcome\n- `KF-001`: false proof\n- `NG-001`: deployment\n", encoding="utf-8", newline="\n")
    (root / "OBJECTIVES_CONFIRMATION.json").write_text('{"actorId":"owner","decision":"CONFIRM"}\n', encoding="utf-8")
    (root / "CHECKPOINT_CONFIRMATION.json").write_text('{"actorId":"owner","decision":"CONFIRM"}\n', encoding="utf-8")
    _git(root, "add", "PROJECT_BRIEF.md", "POSITIONING_CONFIRMATION.json", "KEY_OBJECTIVES.md", "OBJECTIVES_CONFIRMATION.json", "CHECKPOINT_CONFIRMATION.json")
    _git(root, "commit", "-m", "initial authority")
    return root


def _failing_ids(report: dict) -> set[str]:
    return {item["id"] for item in report.get("integrity", {}).get("checks", []) if item.get("status") != "PASS"}


def test_cli_surface_and_envelope_are_schema3() -> None:
    parser = cli.parser()
    subparsers = next(action for action in parser._actions if hasattr(action, "choices") and action.choices)
    assert {"resolve-rules", "reposition", "revise-objectives"} <= set(subparsers.choices)
    result, report = _run(sys.executable, str(CONTROL), "risk", "--score", "10", expect=0)
    assert result.stderr == ""
    assert report["schemaVersion"] == "3.2"
    assert report["runtimeVersion"] == "0.3.5"
    assert set(report) >= {"status", "integrity", "formal", "state"}


def test_inspect_handles_an_unborn_git_repository() -> None:
    with tempfile.TemporaryDirectory(prefix="vibe-control-v3-unborn-") as temp:
        project = Path(temp) / "project"
        project.mkdir()
        _git(project, "init")
        result, report = _run(sys.executable, str(CONTROL), "inspect", "--project", str(project), expect=0)
        assert result.stderr == ""
        assert report["status"] == "PASS"
        assert report["data"]["head"] is None
        assert report["data"]["dirtyEntries"] == []


def test_controller_state_and_schemas_reject_v2_objects() -> None:
    assert initial_state("fixture")["schemaVersion"] == "3.2"
    legacy = {
        "schemaVersion": "2.0",
        "taskId": "TASK-OLD",
        "goal": "legacy",
        "allowedPaths": ["fixture.py"],
        "forbiddenPaths": [],
        "requiredCaseIds": ["CASE-001"],
        "risk": "R1",
        "maxClaimLevel": "VERIFIED",
        "authorityRefs": [],
    }
    try:
        validate_object("task-contract", legacy)
    except ControlError as exc:
        assert exc.check_id == "HC-SCHEMA-CONTRACT"
    else:
        raise AssertionError("Schema 2.0 task contract was accepted by Schema 3.2 runtime")


def test_resolve_rules_is_read_only_and_requires_confirmation() -> None:
    with tempfile.TemporaryDirectory(prefix="vibe-control-v3-resolve-") as temp:
        base = Path(temp)
        project = _new_project(base)
        valid = base / "valid.json"
        invalid = base / "invalid.json"
        _write(valid, _spec())
        _write(invalid, _spec(confirmed=False))
        _, preview = _run(sys.executable, str(CONTROL), "resolve-rules", "--project", str(project), "--spec", str(valid), expect=0)
        assert preview["schemaVersion"] == "3.2" and preview["status"] == "PASS"
        assert "positioning" in preview["data"] and "ruleSet" in preview["data"]
        assert not (project / ".vibe-control").exists(), "resolve-rules must not write a control plane"

        result, rejected = _run(sys.executable, str(CONTROL), "resolve-rules", "--project", str(project), "--spec", str(invalid), expect=None)
        assert result.returncode == 3
        assert "HC-POSITIONING-CONFIRMED" in _failing_ids(rejected)
        assert not (project / ".vibe-control").exists()


def test_schema2_control_plane_returns_reinstall_required_without_writes() -> None:
    with tempfile.TemporaryDirectory(prefix="vibe-control-v3-reinstall-") as temp:
        project = _new_project(Path(temp))
        control = project / ".vibe-control"
        control.mkdir()
        old = control / "stage-state.json"
        _write(old, {"schemaVersion": "2.0", "projectId": "legacy"})
        before = old.read_bytes()
        result, report = _run(sys.executable, str(CONTROL), "inspect", "--project", str(project), expect=None)
        assert result.returncode == 2
        assert "VC-REINSTALL-REQUIRED" in _failing_ids(report)
        assert old.read_bytes() == before
        assert not (control / "legacy").exists(), "0.3.5 must not migrate/import Schema 2.0 evidence"


def test_lock_task_fails_when_cases_do_not_cover_all_applicable_rules() -> None:
    import test_package_release_audit as package_fixture

    package_temp, sealed = package_fixture.package_copy()
    try:
        package_fixture.seal(sealed)
        with tempfile.TemporaryDirectory(prefix="vibe-control-v3-case-coverage-") as temp:
            base = Path(temp)
            project = _new_project(base)
            spec_path = base / "bootstrap.json"
            _write(spec_path, _spec(incomplete_case_coverage=True))
            wrapper = sealed / "scripts" / "vibe_control.py"
            _, bootstrap = _run(sys.executable, str(wrapper), "bootstrap", "--project", str(project), "--spec", str(spec_path), expect=2)
            assert bootstrap["schemaVersion"] == "3.2"
            _git(project, "add", "-A")
            _git(project, "commit", "-m", "bootstrap v3")
            contract = _task_contract()
            contract_path = project / ".vibe-control" / "tasks" / "TASK-001.json"
            _write(contract_path, contract)
            _git(project, "add", "-A")
            _git(project, "commit", "-m", "add task")
            pinned = project / ".vibe-control" / "runtime" / "0.3.5" / "control.py"
            result, report = _run(sys.executable, str(pinned), "lock-task", "--project", str(project), "--contract", str(contract_path), expect=None)
            assert result.returncode == 3, json.dumps(report, ensure_ascii=False)
            assert "HC-RULE-CASE-COVERAGE" in _failing_ids(report)
            assert not any((project / ".vibe-control" / "task-locks").glob("*.json"))
    finally:
        package_temp.cleanup()


def test_bootstrap_recompiles_rules() -> None:
    import test_package_release_audit as package_fixture
    package_temp, sealed = package_fixture.package_copy()
    try:
        package_fixture.seal(sealed)
        with tempfile.TemporaryDirectory(prefix="vibe-control-v3-recompile-") as temp:
            base = Path(temp); project = _new_project(base); spec_path = base / "bootstrap.json"; spec = _spec(); _write(spec_path, spec)
            wrapper = sealed / "scripts" / "vibe_control.py"
            _run(sys.executable, str(wrapper), "bootstrap", "--project", str(project), "--spec", str(spec_path), expect=2)
            recorded = json.loads((project / ".vibe-control" / "resolved-rule-set.json").read_text(encoding="utf-8"))
            sys.path.insert(0, str(sealed / "assets" / "project-control" / "runtime"))
            from vibe_runtime.project_rules import compile_positioning
            fresh = compile_positioning(spec, project, sealed / "assets" / "project-control" / "runtime")
            assert recorded["canonicalSha256"] == fresh["canonicalSha256"]
            assert "resolvedRuleSet" not in spec, "bootstrap input must not contain caller-computed rule output"
    finally:
        package_temp.cleanup()


def test_reposition_invalidates_downstream() -> None:
    import test_package_release_audit as package_fixture
    package_temp, sealed = package_fixture.package_copy()
    try:
        package_fixture.seal(sealed)
        with tempfile.TemporaryDirectory(prefix="vibe-control-v3-reposition-") as temp:
            base = Path(temp); project = _new_project(base); spec_path = base / "bootstrap.json"; _write(spec_path, _spec())
            wrapper = sealed / "scripts" / "vibe_control.py"
            _run(sys.executable, str(wrapper), "bootstrap", "--project", str(project), "--spec", str(spec_path), expect=2)
            _git(project, "add", "-A"); _git(project, "commit", "-m", "bootstrap")
            marker = project / ".vibe-control" / "evidence" / "old.json"; _write(marker, {"diagnostic": True})
            confirmation = project / "REPOSITION_CONFIRMATION.json"; confirmation.write_text('{"actorId":"owner","decision":"CONFIRM"}\n', encoding="utf-8")
            _git(project, "add", "-A"); _git(project, "commit", "-m", "prepare reposition")
            current = json.loads((project / ".vibe-control" / "project-positioning.json").read_text(encoding="utf-8"))
            current["deliveryObjective"] = "PRODUCTION_CANDIDATE"; current["positioningId"] = "positioning-fixture-v3-production"
            current["confirmation"] = {"actorId": "owner", "summary": "production candidate", "summarySha256": "pending", "record": {"path": "REPOSITION_CONFIRMATION.json", "bytes": confirmation.stat().st_size, "sha256": hashlib.sha256(confirmation.read_bytes()).hexdigest(), "tracked": True}}
            current["confirmation"]["summarySha256"] = hashlib.sha256(json.dumps(positioning_summary(current), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            positioning_path = project / "reposition.json"; _write(positioning_path, current); _git(project, "add", "reposition.json"); _git(project, "commit", "-m", "record reposition spec")
            _, planned = _run(sys.executable, str(project / ".vibe-control" / "runtime" / "0.3.5" / "control.py"), "reposition", "--project", str(project), "--spec", str(positioning_path), "--plan", expect=2)
            _, applied = _run(sys.executable, str(project / ".vibe-control" / "runtime" / "0.3.5" / "control.py"), "reposition", "--project", str(project), "--spec", str(positioning_path), "--apply", planned["data"]["planHash"], expect=2)
            state = json.loads((project / ".vibe-control" / "stage-state.json").read_text(encoding="utf-8"))
            assert state["phase"] == "DRAFT" and state["claimLevel"] == "DIAGNOSTIC"
            assert not marker.exists() and any(path.name == "old.json" for path in (project / ".vibe-control" / "legacy").rglob("old.json"))
            assert "execution-evidence" in applied["data"]["invalidated"]
    finally:
        package_temp.cleanup()


def test_execute_aggregate_fails_when_any_case_fails() -> None:
    """A failing execution must fail the command, not merely leave bad evidence for validate."""
    import test_package_release_audit as package_fixture
    package_temp, sealed = package_fixture.package_copy()
    try:
        package_fixture.seal(sealed)
        with tempfile.TemporaryDirectory(prefix="vibe-control-v3-execute-fail-") as temp:
            base = Path(temp); project = _new_project(base); spec_path = base / "bootstrap.json"; _write(spec_path, _spec())
            wrapper = sealed / "scripts" / "vibe_control.py"
            _run(sys.executable, str(wrapper), "bootstrap", "--project", str(project), "--spec", str(spec_path), expect=2)
            _git(project, "add", "-A"); _git(project, "commit", "-m", "bootstrap")
            contract = _task_contract(task_id="TASK-FAIL", risk="R1", max_claim="VERIFIED")
            contract["goal"] = "prove execute aggregation"
            contract_path = project / ".vibe-control" / "tasks" / "TASK-FAIL.json"; _write(contract_path, contract)
            _git(project, "add", "-A"); _git(project, "commit", "-m", "add failing task")
            pinned = project / ".vibe-control" / "runtime" / "0.3.5" / "control.py"
            _run(sys.executable, str(pinned), "lock-task", "--project", str(project), "--contract", str(contract_path), expect=0)
            _git(project, "add", "-A"); _git(project, "commit", "-m", "lock failing task")
            (project / "fixture.py").write_text("raise SystemExit(7)\n", encoding="utf-8")
            _git(project, "add", "fixture.py"); _git(project, "commit", "-m", "implement failing fixture")
            _run(sys.executable, str(pinned), "freeze", "--project", str(project), "--actor", "implementer", "--session", "impl-fail", expect=0)
            _git(project, "add", "-A"); _git(project, "commit", "-m", "freeze failing candidate")
            result, report = _run(sys.executable, str(pinned), "execute", "--project", str(project), "--actor", "executor", "--session", "exec-fail", expect=None)
            assert result.returncode == 3 and report["status"] == "FAIL"
            execute_check = next(item for item in report["integrity"]["checks"] if item["id"] == "HC-EXECUTE")
            assert execute_check["status"] == "FAIL"
            evidence_path = next(path for path in (project / ".vibe-control" / "evidence").glob("evidence-*.json") if not path.name.endswith("adapter-invocation.json"))
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            assert evidence["result"] == "FAIL" and evidence["counters"] == {"executed": 1, "passed": 0, "failed": 1, "skipped": 0}
    finally:
        package_temp.cleanup()


TESTS = [
    test_cli_surface_and_envelope_are_schema3,
    test_inspect_handles_an_unborn_git_repository,
    test_controller_state_and_schemas_reject_v2_objects,
    test_resolve_rules_is_read_only_and_requires_confirmation,
    test_schema2_control_plane_returns_reinstall_required_without_writes,
    test_lock_task_fails_when_cases_do_not_cover_all_applicable_rules,
    test_bootstrap_recompiles_rules,
    test_reposition_invalidates_downstream,
    test_execute_aggregate_fails_when_any_case_fails,
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
    print(json.dumps({"status": "PASS" if ok else "FAIL", "suite": "v3-controller", "tests": results}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
