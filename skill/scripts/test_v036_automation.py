#!/usr/bin/env python3
"""Locked black-box acceptance cases for vibe-control 0.3.6 automation and dashboard."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "assets" / "project-control" / "runtime" / "control.py"
RUNTIME_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
STOP_CONDITIONS = [
    "AUTOMATED_CHECKPOINTS_COMPLETE",
    "HUMAN_CHECKPOINT",
    "OWNER_DECISION",
    "BOUNDARY_CHANGE",
    "R3_OR_IRREVERSIBLE_ACTION",
    "HARD_FAILURE",
    "PUSH_CONFLICT",
    "USER_INTERRUPT",
]


def run(*args: str, cwd: Path | None = None, expect: int | None = None, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(args), cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    if expect is not None and result.returncode != expect:
        raise AssertionError(
            f"exit={result.returncode}, expected={expect}: {' '.join(args)}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


def report(result: subprocess.CompletedProcess[str]) -> dict:
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"controller did not emit JSON: {result.stdout!r} / {result.stderr!r}") from exc
    assert isinstance(value, dict)
    return value


def git(root: Path, *args: str, expect: int = 0) -> str:
    result = run("git", "-C", str(root), *args, expect=expect, timeout=60)
    return result.stdout.strip()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def source_id(prefix: str, statement: str) -> str:
    normalized = " ".join(statement.strip().split())
    return f"{prefix}-{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:12]}"


def adapter_binding() -> dict:
    catalog = json.loads((ROOT / "assets/project-control/runtime/rules/v1/adapters.json").read_text(encoding="utf-8-sig"))
    descriptor = next(item for item in catalog["adapters"] if item["id"] == "generic-command")
    return {
        "id": descriptor["id"],
        "version": descriptor["version"],
        "sha256": hashlib.sha256(canonical(descriptor).encode("utf-8")).hexdigest(),
    }


def automation_policy(project_id: str, mode: str, *, remote_binding: dict | None = None) -> dict:
    commit_policy = "MANUAL" if mode == "MANUAL_STAGE_CONFIRMATION" else "MILESTONE_COMMITS"
    push_policy = "EXISTING_UPSTREAM_MILESTONES" if mode == "AUTO_PUSH_TO_REVIEW" else "NONE"
    semantic = {
        "projectId": project_id,
        "mode": mode,
        "commitPolicy": commit_policy,
        "pushPolicy": push_policy,
        "stopConditions": sorted(STOP_CONDITIONS),
    }
    if remote_binding is not None:
        semantic["remoteBinding"] = remote_binding
    summary = canonical(semantic)
    digest = hashlib.sha256(summary.encode("utf-8")).hexdigest()
    return {
        "schemaVersion": "1.0",
        "policyId": f"automation-{digest[:12]}",
        **semantic,
        "confirmation": {
            "actorId": "owner",
            "summary": summary,
            "summarySha256": digest,
            "record": "AUTOMATION_CONFIRMATION.json",
            "confirmedAt": "2026-07-29T00:00:00+08:00",
        },
    }


def bootstrap_spec(project_id: str, policy: dict | None) -> dict:
    signal = "fixture command passes"
    gate = "owner reviews fixture"
    positioning = {
        "primaryExperience": "SERVICE",
        "capabilityDomains": ["BACKEND_API"],
        "deliveryObjective": "VERTICAL_SLICE",
        "releaseIntent": "PRIVATE_OPERATION",
        "runtimeTargets": ["python-local"],
        "targetEnvironments": [{"id": "win", "operatingSystem": "Windows", "deviceClass": "desktop", "architecture": "x86_64"}],
        "distributionChannels": ["private-local"],
        "humanQualityGates": [{"id": source_id("HG", gate), "statement": gate}],
        "nonGoals": ["external release"],
        "firstVerticalSlice": {
            "outcome": "one command succeeds",
            "included": ["fixture command"],
            "excluded": ["deployment"],
            "successSignals": [{"id": source_id("SIG", signal), "statement": signal}],
        },
    }
    position_hash = hashlib.sha256(canonical(positioning).encode("utf-8")).hexdigest()
    objective_summary = "fixture objectives confirmed"
    value = {
        "schemaVersion": "3.2",
        "projectId": project_id,
        **positioning,
        "confirmation": {"actorId": "owner", "summary": "fixture positioning", "summarySha256": position_hash, "record": "POSITIONING_CONFIRMATION.json"},
        "keyObjectives": {
            "document": "KEY_OBJECTIVES.md", "documentId": "FIXTURE-OBJECTIVES", "revision": 1, "status": "CONFIRMED",
            "sourceDocuments": ["PROJECT_BRIEF.md"], "objectiveIds": ["KO-001"], "failureModeIds": ["KF-001"], "nonGoalIds": ["NG-001"],
            "confirmation": {"actorId": "owner", "summary": objective_summary, "summarySha256": hashlib.sha256(objective_summary.encode()).hexdigest(), "record": "OBJECTIVES_CONFIRMATION.json"},
        },
        "capabilityProfiles": [], "profileBindings": [], "runtimeAdapters": ["generic-command"],
        "skillBindings": [], "projectOverlay": [], "authorityFiles": ["PROJECT_BRIEF.md"], "trustedKeys": [],
        "cases": [{
            "id": "CASE-001", "command": [sys.executable, "fixture.py"], "observation": "runtime-observed", "maxClaimLevel": "DEVELOPMENT_CHECKED",
            "oracle": {"exitCode": 0, "stdoutContainsAll": ["OK"], "stderrContainsNone": ["Traceback"]}, "artifacts": [],
            "satisfiesRuleIds": ["RULE-CORE-OBSERVABLE-CANDIDATE", "RULE-CORE-FAILURE-CONSERVATION", "RULE-PROFILE-API-CONTRACT", "RULE-ADAPTER-GENERIC_COMMAND"],
            "capabilities": ["candidate-integrity", "failure-conservation", "api-contract-runtime", "generic-command-execution"],
            "adapter": adapter_binding(),
        }],
    }
    if policy is not None:
        value["automationPolicy"] = policy
    return value


def task_contract(*, risk: str = "R2", goal: str = "<script>alert(1)</script> is inert") -> dict:
    signal_id = source_id("SIG", "fixture command passes")
    checkpoints = [{
        "id": "CP-001", "sourceRefs": [signal_id], "objectiveRefs": ["KO-001"],
        "statement": "fixture command passes", "type": "AUTOMATED", "requiredForClaim": "DEVELOPMENT_CHECKED",
        "caseIds": ["CASE-001"], "assertions": [{"id": "ASRT-001", "statement": "fixture emits OK", "caseIds": ["CASE-001"]}],
        "expected": {"status": "PASS", "minExecuted": 1, "maxFailed": 0, "maxSkipped": 0, "artifacts": "AS_DECLARED"}, "notProven": [],
    }]
    audit_policy = {"mode": "CONFORMANCE_PLUS_BOUNDED_EXPLORATION", "maxExploratoryFindings": 3, "stopCondition": "ALL_REQUIRED_CHECKPOINTS_REPORTED"}
    checkpoint_hash = hashlib.sha256(canonical({"acceptanceCheckpoints": checkpoints, "auditPolicy": audit_policy}).encode()).hexdigest()
    return {
        "schemaVersion": "3.2", "taskId": "TASK-001", "goal": goal,
        "objectiveRefs": ["KO-001", "KF-001"], "allowedPaths": ["fixture.py"], "forbiddenPaths": ["forbidden.txt"],
        "requiredCaseIds": ["CASE-001"], "risk": risk, "maxClaimLevel": "DEVELOPMENT_CHECKED",
        "authorityRefs": ["PROJECT_BRIEF.md"], "nonGoals": [], "humanDecisionPoints": [],
        "acceptanceCheckpoints": checkpoints,
        "checkpointConfirmation": {"actorId": "owner", "summary": "fixture checkpoint confirmed", "checkpointSetSha256": checkpoint_hash, "record": "CHECKPOINT_CONFIRMATION.json", "confirmedAt": "2026-07-29T00:00:00+08:00"},
        "auditPolicy": audit_policy,
    }


class Fixture:
    def __init__(self, mode: str, *, remote: bool = False, risk: str = "R2") -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="vc036-", ignore_cleanup_errors=True)
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        git(self.root, "init")
        git(self.root, "config", "user.email", "fixture@example.invalid")
        git(self.root, "config", "user.name", "Fixture")
        remote_binding = None
        if remote:
            self.remote = self.base / "remote.git"
            run("git", "init", "--bare", str(self.remote), expect=0)
            remote_url = self.remote.resolve().as_uri()
            git(self.root, "remote", "add", "origin", remote_url)
            remote_binding = {
                "remote": "origin", "branch": "master", "upstream": "origin/master",
                "remoteUrlSha256": hashlib.sha256(remote_url.encode("utf-8")).hexdigest(),
            }
        self.policy = automation_policy("fixture", mode, remote_binding=remote_binding)
        files = {
            "PROJECT_BRIEF.md": "# Fixture\n",
            "KEY_OBJECTIVES.md": "# Objectives\n\n- `KO-001`: outcome\n- `KF-001`: false proof\n- `NG-001`: deployment\n",
            "OBJECTIVES_CONFIRMATION.json": '{}\n', "POSITIONING_CONFIRMATION.json": '{}\n',
            "AUTOMATION_CONFIRMATION.json": '{}\n', "CHECKPOINT_CONFIRMATION.json": '{}\n',
        }
        for name, content in files.items():
            (self.root / name).write_text(content, encoding="utf-8", newline="\n")
        git(self.root, "add", "-A"); git(self.root, "commit", "-m", "authority")
        if remote:
            git(self.root, "push", "-u", "origin", "master")
        spec_path = self.base / "bootstrap.json"; write_json(spec_path, bootstrap_spec("fixture", self.policy))
        value = report(run(sys.executable, str(CONTROL), "bootstrap", "--project", str(self.root), "--spec", str(spec_path)))
        assert value["status"] == "BLOCKED", value
        git(self.root, "add", "-A"); git(self.root, "commit", "-m", "bootstrap")
        contract = task_contract(risk=risk)
        contract_path = self.root / ".vibe-control/tasks/TASK-001.json"; write_json(contract_path, contract)
        git(self.root, "add", "-A"); git(self.root, "commit", "-m", "contract")
        value = report(run(sys.executable, str(self.root / f".vibe-control/runtime/{RUNTIME_VERSION}/control.py"), "lock-task", "--project", str(self.root), "--contract", str(contract_path)))
        assert value["status"] == "PASS", value
        git(self.root, "add", "-A"); git(self.root, "commit", "-m", "lock task")

    @property
    def control(self) -> Path:
        return self.root / f".vibe-control/runtime/{RUNTIME_VERSION}/control.py"

    def command(self, *args: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        result = run(sys.executable, str(self.control), *args, "--project", str(self.root))
        return result, report(result)

    def close(self) -> None:
        self.temp.cleanup()


def test_bootstrap_requires_explicit_policy_without_writes() -> None:
    with tempfile.TemporaryDirectory(prefix="vc036-missing-", ignore_cleanup_errors=True) as name:
        root = Path(name) / "project"; root.mkdir(); git(root, "init"); git(root, "config", "user.email", "fixture@example.invalid"); git(root, "config", "user.name", "Fixture")
        for file, text in {"PROJECT_BRIEF.md": "# Fixture\n", "KEY_OBJECTIVES.md": "# O\n- `KO-001`: x\n- `KF-001`: y\n- `NG-001`: z\n", "OBJECTIVES_CONFIRMATION.json": '{}\n', "POSITIONING_CONFIRMATION.json": '{}\n'}.items():
            (root / file).write_text(text, encoding="utf-8", newline="\n")
        git(root, "add", "-A"); git(root, "commit", "-m", "authority")
        spec = bootstrap_spec("missing", None); spec_path = Path(name) / "spec.json"; write_json(spec_path, spec)
        result = run(sys.executable, str(CONTROL), "bootstrap", "--project", str(root), "--spec", str(spec_path))
        value = report(result)
        assert result.returncode != 0 and value["error"]["id"] == "HC-AUTOMATION-POLICY-REQUIRED", value
        assert not (root / ".vibe-control").exists()


def test_modes_and_task_binding() -> None:
    for mode in ("MANUAL_STAGE_CONFIRMATION", "AUTO_LOCAL_TO_REVIEW", "AUTO_PUSH_TO_REVIEW"):
        fixture = Fixture(mode, remote=mode == "AUTO_PUSH_TO_REVIEW")
        try:
            lock = json.loads((fixture.root / ".vibe-control/project-governance-lock.json").read_text(encoding="utf-8"))
            task_lock = json.loads((fixture.root / ".vibe-control/task-locks/TASK-001.json").read_text(encoding="utf-8"))
            assert "automationPolicy" in lock and task_lock["automationPolicy"] == lock["automationPolicy"]
            _, action = fixture.command("automation", "--action", "dispatch")
            if mode == "MANUAL_STAGE_CONFIRMATION":
                assert action["status"] == "BLOCKED" and action["error"]["id"] == "HC-AUTOMATION-MANUAL", action
            else:
                assert action["status"] == "PASS", action
        finally:
            fixture.close()


def test_legacy_opt_in_and_plan_hash() -> None:
    fixture = Fixture("MANUAL_STAGE_CONFIRMATION")
    try:
        policy_path = fixture.root / ".vibe-control/automation-policy.json"
        lock_path = fixture.root / ".vibe-control/project-governance-lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8")); lock.pop("automationPolicy", None); write_json(lock_path, lock)
        policy_path.unlink(); git(fixture.root, "add", "-A"); git(fixture.root, "commit", "-m", "legacy control")
        _, legacy = fixture.command("automation", "--action", "dispatch")
        assert legacy["status"] == "BLOCKED" and legacy["error"]["id"] == "HC-AUTOMATION-MANUAL", legacy
        proposed = automation_policy("fixture", "AUTO_LOCAL_TO_REVIEW"); spec = fixture.base / "policy.json"; write_json(spec, proposed)
        result = run(sys.executable, str(fixture.control), "automation", "--project", str(fixture.root), "--spec", str(spec), "--plan")
        planned = report(result); assert planned["status"] == "BLOCKED" and planned["data"]["planHash"]
        bad = run(sys.executable, str(fixture.control), "automation", "--project", str(fixture.root), "--spec", str(spec), "--apply", "0" * 64)
        assert report(bad)["error"]["id"] == "HC-AUTOMATION-PLAN-HASH"
        applied = report(run(sys.executable, str(fixture.control), "automation", "--project", str(fixture.root), "--spec", str(spec), "--apply", planned["data"]["planHash"]))
        assert applied["status"] == "BLOCKED" and (fixture.root / ".vibe-control/automation-policy.json").is_file(), applied
    finally:
        fixture.close()


def test_action_guards_and_r3_stop() -> None:
    local = Fixture("AUTO_LOCAL_TO_REVIEW")
    try:
        _, allowed = local.command("automation", "--action", "continue"); assert allowed["status"] == "PASS", allowed
        (local.root / "fixture.py").write_text("print('OK')\n", encoding="utf-8", newline="\n")
        _, commit = local.command("automation", "--action", "commit"); assert commit["status"] == "PASS", commit
        _, push = local.command("automation", "--action", "push"); assert push["error"]["id"] == "HC-AUTOMATION-PUSH-POLICY", push
    finally:
        local.close()
    r3 = Fixture("AUTO_LOCAL_TO_REVIEW", risk="R3")
    try:
        _, blocked = r3.command("automation", "--action", "dispatch"); assert blocked["error"]["id"] == "HC-AUTOMATION-R3-STOP", blocked
    finally:
        r3.close()


def test_push_binding_dirty_and_remote_drift() -> None:
    fixture = Fixture("AUTO_PUSH_TO_REVIEW", remote=True)
    try:
        _, clean = fixture.command("automation", "--action", "push"); assert clean["status"] == "PASS", clean
        (fixture.root / "fixture.py").write_text("print('dirty')\n", encoding="utf-8", newline="\n")
        _, dirty = fixture.command("automation", "--action", "push"); assert dirty["error"]["id"] == "HC-AUTOMATION-WORKTREE-CLEAN", dirty
        (fixture.root / "fixture.py").unlink()
        other = fixture.base / "other.git"; run("git", "init", "--bare", str(other), expect=0)
        git(fixture.root, "remote", "set-url", "origin", other.resolve().as_uri())
        _, drift = fixture.command("automation", "--action", "push"); assert drift["error"]["id"] == "HC-AUTOMATION-REMOTE-DRIFT", drift
    finally:
        fixture.close()


def test_dashboard_is_offline_escaped_and_non_authoritative() -> None:
    fixture = Fixture("AUTO_LOCAL_TO_REVIEW")
    try:
        output = fixture.base / "dashboard"
        result = run(sys.executable, str(fixture.control), "dashboard", "--project", str(fixture.root), "--output-dir", str(output), expect=0)
        value = report(result); assert value["status"] == "PASS", value
        assert set(value["data"]["files"]) == {"html", "status", "summary"}
        html = (output / "index.html").read_text(encoding="utf-8")
        status = json.loads((output / "status.json").read_text(encoding="utf-8"))
        summary = (output / "summary.md").read_text(encoding="utf-8")
        assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        assert "http://" not in html and "https://" not in html
        assert "prefers-color-scheme" in html and "prefers-reduced-motion" in html and "@media" in html
        assert status["formal"]["eligible"] is False and status["source"] == "DERIVED_NON_AUTHORITATIVE"
        assert value["data"]["statusSha256"] == hashlib.sha256((output / "status.json").read_bytes()).hexdigest()
        assert "尚未证明" in summary
        state_before = (fixture.root / ".vibe-control/stage-state.json").read_bytes()
        run(sys.executable, str(fixture.control), "dashboard", "--project", str(fixture.root), "--output-dir", str(output), expect=0)
        assert (fixture.root / ".vibe-control/stage-state.json").read_bytes() == state_before
        inside = run(sys.executable, str(fixture.control), "dashboard", "--project", str(fixture.root), "--output-dir", str(fixture.root / "dashboard"))
        assert report(inside)["error"]["id"] == "HC-DASHBOARD-OUTPUT-SCOPE"
    finally:
        fixture.close()


def main() -> int:
    tests = [
        ("bootstrap-explicit-policy", test_bootstrap_requires_explicit_policy_without_writes),
        ("modes-and-task-binding", test_modes_and_task_binding),
        ("legacy-opt-in-plan-hash", test_legacy_opt_in_and_plan_hash),
        ("action-guards-r3-stop", test_action_guards_and_r3_stop),
        ("push-binding-dirty-drift", test_push_binding_dirty_and_remote_drift),
        ("dashboard-offline-non-authoritative", test_dashboard_is_offline_escaped_and_non_authoritative),
    ]
    results = []
    for case_id, test in tests:
        started = time.monotonic()
        try:
            test(); results.append({"case": case_id, "status": "PASS", "durationSeconds": round(time.monotonic() - started, 3)})
        except Exception as exc:
            results.append({"case": case_id, "status": "FAIL", "durationSeconds": round(time.monotonic() - started, 3), "errorType": type(exc).__name__, "error": str(exc)})
    passed = sum(item["status"] == "PASS" for item in results)
    counters = {"total": len(results), "passed": passed, "failed": len(results) - passed, "timedOut": 0, "skipped": 0}
    value = {"test": "vibe-control-0.3.6-automation", "status": "PASS" if passed == len(results) else "FAIL", "counters": counters, "cases": results}
    print(json.dumps(value, ensure_ascii=False))
    return 0 if value["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
