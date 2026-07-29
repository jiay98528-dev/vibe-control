from __future__ import annotations

import copy
import datetime as dt
import fnmatch
import hashlib
import json
import shutil
import subprocess
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Any

from .common import (
    ControlError, canonical_bytes, check, clean_status, envelope, file_ref, git,
    git_root, load_json, now_iso, safe_relative, sha256_bytes, sha256_file,
    verify_ref, write_json_atomic,
)
from .schema import validate_object


STOP_CONDITIONS = frozenset({
    "AUTOMATED_CHECKPOINTS_COMPLETE",
    "HUMAN_CHECKPOINT",
    "OWNER_DECISION",
    "BOUNDARY_CHANGE",
    "R3_OR_IRREVERSIBLE_ACTION",
    "HARD_FAILURE",
    "PUSH_CONFLICT",
    "USER_INTERRUPT",
})
ACTIONS = frozenset({"dispatch", "continue", "commit", "push"})
AUTOMATED_CONTROL_OUTPUTS = (
    ".vibe-control/stage-state.json",
    ".vibe-control/candidates/**",
    ".vibe-control/evidence/**",
    ".vibe-control/reviews/**",
    ".vibe-control/decisions/**",
    ".vibe-control/audit-closures/**",
    ".vibe-control/external-audits/**",
    ".vibe-control/handoffs/**",
)


def _paths(project: Path) -> dict[str, Path]:
    root = git_root(project.resolve())
    control = root / ".vibe-control"
    return {
        "root": root,
        "control": control,
        "lock": control / "project-governance-lock.json",
        "policy": control / "automation-policy.json",
        "state": control / "stage-state.json",
        "tasks": control / "tasks",
        "task_locks": control / "task-locks",
        "candidates": control / "candidates",
        "legacy": control / "legacy",
    }


def _content_ref(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.resolve().relative_to(root.resolve()).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "tracked": True,
    }


def _semantic(policy: dict[str, Any]) -> dict[str, Any]:
    value: dict[str, Any] = {
        "projectId": policy.get("projectId"),
        "mode": policy.get("mode"),
        "commitPolicy": policy.get("commitPolicy"),
        "pushPolicy": policy.get("pushPolicy"),
        "stopConditions": sorted(policy.get("stopConditions", [])),
    }
    if "remoteBinding" in policy:
        value["remoteBinding"] = policy["remoteBinding"]
    return value


def _canonical_summary(policy: dict[str, Any]) -> str:
    return canonical_bytes(_semantic(policy)).decode("utf-8")


def _expected_policy_id(policy: dict[str, Any]) -> str:
    return f"automation-{sha256_bytes(_canonical_summary(policy).encode('utf-8'))[:12]}"


def normalized_remote_url(value: str) -> str:
    """Remove credentials and query/fragment material before hashing a remote identity."""
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme and parsed.netloc:
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urllib.parse.urlunsplit((parsed.scheme.lower(), host, parsed.path, "", ""))
    return value


def _validate_semantics(policy: dict[str, Any], *, project_id: str | None = None) -> None:
    validate_object("automation-policy", policy)
    if set(policy["stopConditions"]) != STOP_CONDITIONS:
        raise ControlError("HC-AUTOMATION-STOP-CONDITIONS", "automation policy must contain the complete fixed stop set")
    if project_id is not None and policy["projectId"] != project_id:
        raise ControlError("HC-AUTOMATION-PROJECT", "automation policy targets a different project")
    summary = _canonical_summary(policy)
    digest = sha256_bytes(summary.encode("utf-8"))
    confirmation = policy["confirmation"]
    if confirmation["summary"] != summary or confirmation["summarySha256"] != digest:
        raise ControlError("HC-AUTOMATION-CONFIRMATION", "automation confirmation does not bind the canonical policy summary")
    if policy["policyId"] != f"automation-{digest[:12]}":
        raise ControlError("HC-AUTOMATION-POLICY-ID", "automation policy ID does not match its canonical semantics")


def policy_scope_binding(key_objectives: dict[str, Any], positioning: dict[str, Any]) -> dict[str, str]:
    return {
        "keyObjectivesObjectSha256": sha256_bytes(canonical_bytes(key_objectives)),
        "positioningObjectSha256": sha256_bytes(canonical_bytes(positioning)),
    }


def materialize_policy(
    root: Path,
    spec: dict[str, Any],
    *,
    project_id: str,
    scope_binding: dict[str, str] | None = None,
) -> dict[str, Any]:
    policy = copy.deepcopy(spec)
    _validate_semantics(policy, project_id=project_id)
    if scope_binding is not None:
        policy["scopeBinding"] = copy.deepcopy(scope_binding)
    record = policy["confirmation"]["record"]
    if isinstance(record, str):
        policy["confirmation"]["record"] = file_ref(root, safe_relative(root, record))
    elif isinstance(record, dict):
        result = verify_ref(root, record, "HC-AUTOMATION-CONFIRMATION-RECORD")
        if result["status"] != "PASS":
            raise ControlError(result["id"], result["message"], status=result["status"])
    else:
        raise ControlError("HC-AUTOMATION-CONFIRMATION-RECORD", "automation confirmation record must be a tracked path or content reference")
    validate_object("automation-policy", policy)
    return policy


def verify_policy(
    root: Path,
    policy: dict[str, Any],
    *,
    project_id: str,
    expected_scope_binding: dict[str, str] | None = None,
) -> None:
    _validate_semantics(policy, project_id=project_id)
    record = policy["confirmation"]["record"]
    if not isinstance(record, dict):
        raise ControlError("HC-AUTOMATION-POLICY-DRIFT", "materialized automation policy lacks a content-bound confirmation record", status="INVALIDATED")
    result = verify_ref(root, record, "HC-AUTOMATION-CONFIRMATION-RECORD")
    if result["status"] != "PASS":
        raise ControlError("HC-AUTOMATION-POLICY-DRIFT", result["message"], status="INVALIDATED", details=result.get("details"))
    if expected_scope_binding is not None and policy.get("scopeBinding") != expected_scope_binding:
        raise ControlError("HC-AUTOMATION-POLICY-DRIFT", "automation policy no longer binds the current objectives and positioning", status="INVALIDATED")


def scope_binding_from_lock(root: Path, lock: dict[str, Any]) -> dict[str, str]:
    objective_ref = lock.get("keyObjectives")
    positioning_ref = lock.get("positioning")
    if not isinstance(objective_ref, dict) or not isinstance(positioning_ref, dict):
        raise ControlError("HC-AUTOMATION-POLICY-DRIFT", "governance lock lacks objective or positioning identity", status="INVALIDATED")
    return policy_scope_binding(
        load_json(safe_relative(root, objective_ref["path"])),
        load_json(safe_relative(root, positioning_ref["path"])),
    )


def automation_plan(project: Path, spec_path: Path) -> dict[str, Any]:
    p = _paths(project)
    if not p["lock"].is_file():
        raise ControlError("VC-CONTROL-PLANE-MISSING", ".vibe-control does not exist", status="BLOCKED")
    lock = load_json(p["lock"]); validate_object("project-governance-lock", lock)
    raw = load_json(spec_path.resolve())
    if not isinstance(raw, dict):
        raise ControlError("HC-SCHEMA-AUTOMATION-POLICY", "automation policy must be an object")
    proposed = materialize_policy(p["root"], raw, project_id=lock["projectId"], scope_binding=scope_binding_from_lock(p["root"], lock))
    current = lock.get("automationPolicy")
    plan = {
        "schemaVersion": "1.0",
        "operation": "configure-automation",
        "projectId": lock["projectId"],
        "from": current or {"mode": "MANUAL_STAGE_CONFIRMATION", "legacyDefault": True},
        "to": {"policyId": proposed["policyId"], "sha256": sha256_bytes(canonical_bytes(proposed)), "mode": proposed["mode"]},
        "specSha256": sha256_file(spec_path.resolve()),
        "invalidates": ["task-lock", "candidate", "execution-evidence", "review", "decision", "handoff", "release-receipt"],
    }
    plan["planHash"] = sha256_bytes(canonical_bytes(plan))
    return envelope(
        status="BLOCKED",
        checks=[check("HC-AUTOMATION-APPROVAL", "BLOCKED", "apply requires the exact content-bound plan hash")],
        data=plan,
    )


def automation_apply(project: Path, spec_path: Path, plan_hash: str) -> dict[str, Any]:
    p = _paths(project)
    planned = automation_plan(project, spec_path)["data"]
    if planned["planHash"] != plan_hash:
        raise ControlError("HC-AUTOMATION-PLAN-HASH", "automation plan hash mismatch", status="INVALIDATED")
    if clean_status(p["root"]):
        raise ControlError("HC-AUTOMATION-WORKTREE-CLEAN", "automation policy apply requires a clean worktree", status="BLOCKED")
    lock = load_json(p["lock"])
    proposed = materialize_policy(p["root"], load_json(spec_path.resolve()), project_id=lock["projectId"], scope_binding=scope_binding_from_lock(p["root"], lock))
    legacy = p["legacy"] / f"automation-{now_iso().replace(':', '-')}"
    legacy.mkdir(parents=True, exist_ok=False)
    for snapshot in (p["policy"], p["lock"], p["state"]):
        if snapshot.is_file():
            shutil.copy2(snapshot, legacy / snapshot.name)
    for name in ("tasks", "task-locks", "candidates", "evidence", "reviews", "decisions", "external-audits", "handoffs"):
        source = p["control"] / name
        if source.exists():
            shutil.move(str(source), str(legacy / name))
    write_json_atomic(p["policy"], proposed)
    lock["automationPolicy"] = _content_ref(p["root"], p["policy"])
    lock["lockedAt"] = now_iso()
    validate_object("project-governance-lock", lock)
    write_json_atomic(p["lock"], lock)
    state = load_json(p["state"])
    state = {
        "schemaVersion": "3.2", "projectId": lock["projectId"],
        "positioningId": state.get("positioningId"), "ruleSetId": state.get("ruleSetId"),
        "phase": "DRAFT", "health": "BLOCKED", "claimLevel": "DIAGNOSTIC",
        "taskId": None, "candidateId": None, "revision": 0, "phaseHistory": [], "updatedAt": now_iso(),
    }
    validate_object("stage-state", state); write_json_atomic(p["state"], state)
    return envelope(
        status="BLOCKED",
        checks=[check("HC-AUTOMATION-INVALIDATION", "BLOCKED", "automation policy changed; downstream records were archived and cannot be inherited")],
        state={"declared": state, "derived": state},
        data={"policy": str(p["policy"]), "legacy": str(legacy), "invalidated": planned["invalidates"], "next": "commit and lock a fresh task"},
    )


def _current_policy(p: dict[str, Path], lock: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    ref = lock.get("automationPolicy")
    if ref is None:
        return None, None
    result = verify_ref(p["root"], ref, "HC-AUTOMATION-POLICY-DRIFT")
    if result["status"] != "PASS":
        raise ControlError("HC-AUTOMATION-POLICY-DRIFT", result["message"], status="INVALIDATED", details=result.get("details"))
    policy = load_json(safe_relative(p["root"], ref["path"]))
    verify_policy(p["root"], policy, project_id=lock["projectId"], expected_scope_binding=scope_binding_from_lock(p["root"], lock))
    return policy, ref


def _status_paths(root: Path) -> list[str]:
    values = []
    result = _git_result(root, "status", "--porcelain=v1", "--untracked-files=all")
    if result.returncode != 0:
        raise ControlError("HC-AUTOMATION-WORKTREE-STATUS", "automatic advancement could not read exact Git status", status="BLOCKED")
    for line in result.stdout.splitlines():
        value = line[3:].split(" -> ")[-1].replace("\\", "/") if len(line) >= 4 else line
        if value:
            values.append(value)
    return values


def _path_matches(path: str, pattern: str) -> bool:
    normalized = path.replace("\\", "/")
    pattern = pattern.replace("\\", "/")
    return fnmatch.fnmatchcase(normalized, pattern) or (pattern.endswith("/**") and normalized.startswith(pattern[:-3].rstrip("/") + "/"))


def _git_result(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)


def _milestone_commit_subject(task_id: str, requested: str | None) -> str:
    subject = requested if requested is not None else f"chore(governance): record {task_id} milestone"
    if not isinstance(subject, str) or not subject.strip():
        raise ControlError(
            "HC-AUTOMATION-MILESTONE-MESSAGE",
            "automatic milestone commit subject must not be empty",
            status="BLOCKED",
        )
    if any(unicodedata.category(character) in {"Cc", "Zl", "Zp"} for character in subject):
        raise ControlError(
            "HC-AUTOMATION-MILESTONE-MESSAGE",
            "automatic milestone commit subject must be one line without control characters",
            status="BLOCKED",
        )
    return subject


def _assert_locked_inputs(root: Path, task_lock_path: Path, task_lock: dict[str, Any]) -> None:
    """Reject boundary drift before an automatic side effect.

    The controller may advance state and evidence, but automation never gets to
    rewrite the task's contract, oracle, objectives, policy, or authority files.
    """
    task_lock_relative = task_lock_path.relative_to(root).as_posix()
    if task_lock_relative in _status_paths(root) and git(root, "ls-files", "--error-unmatch", "--", task_lock_relative, required=False):
        raise ControlError("HC-AUTOMATION-BOUNDARY-CHANGE", "tracked task lock changed after authorization", status="BLOCKED")
    refs = [
        task_lock["contract"], task_lock["governanceLock"], task_lock["keyObjectives"],
        task_lock["caseCatalog"], task_lock["positioning"], task_lock["resolvedRuleSet"],
        task_lock["checkpointConfirmation"], *task_lock["authorityBindings"],
    ]
    if "automationPolicy" in task_lock:
        refs.append(task_lock["automationPolicy"])
    failures = []
    for ref in refs:
        result = verify_ref(root, ref, "HC-AUTOMATION-BOUNDARY-CHANGE")
        if result["status"] != "PASS":
            failures.append({"path": ref.get("path"), "status": result["status"]})
    try:
        objectives = load_json(safe_relative(root, task_lock["keyObjectives"]["path"]))
        positioning = load_json(safe_relative(root, task_lock["positioning"]["path"]))
        nested_refs = [
            objectives["document"], *objectives["sourceDocuments"],
            objectives["confirmation"]["record"], positioning["confirmation"]["record"],
        ]
        for ref in nested_refs:
            result = verify_ref(root, ref, "HC-AUTOMATION-BOUNDARY-CHANGE")
            if result["status"] != "PASS":
                failures.append({"path": ref.get("path"), "status": result["status"]})
    except (KeyError, TypeError, ControlError):
        failures.append({"path": "nested-authority-closure", "status": "INVALIDATED"})
    if failures:
        raise ControlError("HC-AUTOMATION-BOUNDARY-CHANGE", "locked task inputs drifted; human review is required", status="BLOCKED", details=failures)


def _assert_push_history_scope(
    root: Path,
    task_lock_path: Path,
    task_lock: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Prove every post-lock commit stays inside the authorized task envelope.

    A clean worktree only proves that bytes were committed. It does not prove
    that the commits about to be pushed were produced inside the active task.
    Walk every non-merge commit after the task baseline so an add-then-delete or
    a pre-created clean forbidden commit cannot bypass the path gate.
    """
    baseline = task_lock.get("baselineCommit")
    if not isinstance(baseline, str) or not baseline:
        raise ControlError("HC-AUTOMATION-MILESTONE-HISTORY", "task lock lacks a push-verifiable baseline commit", status="BLOCKED")
    exists = _git_result(root, "cat-file", "-e", f"{baseline}^{{commit}}")
    ancestor = _git_result(root, "merge-base", "--is-ancestor", baseline, "HEAD")
    if exists.returncode != 0 or ancestor.returncode != 0:
        raise ControlError("HC-AUTOMATION-MILESTONE-HISTORY", "task baseline is not an ancestor of the current milestone", status="BLOCKED", details={"baselineCommit": baseline})
    merges = _git_result(root, "rev-list", "--merges", f"{baseline}..HEAD")
    if merges.returncode != 0:
        raise ControlError("HC-AUTOMATION-MILESTONE-HISTORY", "automatic push could not inspect milestone history", status="BLOCKED")
    merge_commits = [value for value in merges.stdout.splitlines() if value]
    if merge_commits:
        raise ControlError("HC-AUTOMATION-MILESTONE-HISTORY", "automatic push refuses merge commits after the task baseline", status="BLOCKED", details={"commits": merge_commits})
    revision = _git_result(root, "rev-list", "--reverse", f"{baseline}..HEAD")
    if revision.returncode != 0:
        raise ControlError("HC-AUTOMATION-MILESTONE-HISTORY", "automatic push could not enumerate milestone commits", status="BLOCKED")
    commits = [value for value in revision.stdout.splitlines() if value]
    task_lock_relative = task_lock_path.resolve().relative_to(root.resolve()).as_posix()
    forbidden: list[dict[str, str]] = []
    outside: list[dict[str, str]] = []
    unexpected_control: list[dict[str, str]] = []
    observed: list[dict[str, Any]] = []
    for commit in commits:
        changed = _git_result(root, "diff-tree", "--no-commit-id", "--name-only", "--no-renames", "-r", commit)
        if changed.returncode != 0:
            raise ControlError("HC-AUTOMATION-MILESTONE-HISTORY", "automatic push could not inspect a milestone commit", status="BLOCKED", details={"commit": commit})
        paths = sorted({value.replace("\\", "/") for value in changed.stdout.splitlines() if value})
        observed.append({"commit": commit, "paths": paths})
        for path in paths:
            item = {"commit": commit, "path": path}
            if path.startswith(".vibe-control/"):
                if path != task_lock_relative and not any(_path_matches(path, pattern) for pattern in AUTOMATED_CONTROL_OUTPUTS):
                    unexpected_control.append(item)
                continue
            if any(_path_matches(path, pattern) for pattern in contract["forbiddenPaths"]):
                forbidden.append(item)
            elif not any(_path_matches(path, pattern) for pattern in contract["allowedPaths"]):
                outside.append(item)
    if forbidden or outside or unexpected_control:
        raise ControlError(
            "HC-AUTOMATION-PUSH-SCOPE",
            "committed milestone history escapes the locked task envelope",
            status="BLOCKED",
            details={"baselineCommit": baseline, "forbidden": forbidden, "outsideAllowed": outside, "unexpectedControl": unexpected_control},
        )
    return {"baselineCommit": baseline, "commits": commits, "observed": observed}


def automation_action(project: Path, action: str, message: str | None = None) -> dict[str, Any]:
    if action not in ACTIONS:
        raise ControlError("HC-AUTOMATION-ACTION", f"unknown automation action: {action}")
    p = _paths(project)
    if not p["lock"].is_file():
        raise ControlError("VC-CONTROL-PLANE-MISSING", ".vibe-control does not exist", status="BLOCKED")
    lock = load_json(p["lock"]); validate_object("project-governance-lock", lock)
    policy, policy_ref = _current_policy(p, lock)
    if policy is None or policy["mode"] == "MANUAL_STAGE_CONFIRMATION":
        raise ControlError("HC-AUTOMATION-MANUAL", "project is in manual stage-confirmation mode", status="BLOCKED")
    state = load_json(p["state"]); validate_object("stage-state", state)
    task_id = state.get("taskId")
    if not task_id:
        raise ControlError("HC-AUTOMATION-TASK", "automatic advancement requires a locked task", status="BLOCKED")
    task_lock_path = p["task_locks"] / f"{task_id}.json"
    task_lock = load_json(task_lock_path); validate_object("task-lock", task_lock)
    if task_lock.get("automationPolicy") != policy_ref:
        raise ControlError("HC-AUTOMATION-POLICY-DRIFT", "active task does not bind the current automation policy", status="INVALIDATED")
    candidate_id = state.get("candidateId")
    if candidate_id:
        candidate = load_json(p["candidates"] / f"{candidate_id}.json")
        validate_object("candidate-manifest", candidate)
        if candidate.get("taskId") != task_id or candidate.get("automationPolicy") != policy_ref:
            raise ControlError("HC-AUTOMATION-POLICY-DRIFT", "active candidate does not bind the current task and automation policy", status="INVALIDATED")
    contract = load_json(safe_relative(p["root"], task_lock["contract"]["path"])); validate_object("task-contract", contract)
    _assert_locked_inputs(p["root"], task_lock_path, task_lock)
    if contract["risk"] == "R3":
        raise ControlError("HC-AUTOMATION-R3-STOP", "R3 work requires an explicit human review before automatic advancement", status="BLOCKED")
    if state["health"] in {"FAILED"}:
        raise ControlError("HC-AUTOMATION-HARD-FAILURE", "failed state stops automatic advancement", status="BLOCKED")
    if action in {"dispatch", "continue"} and state["phase"] in {"VERIFIED", "AUDITED", "ACCEPTED", "RELEASE_READY"}:
        raise ControlError("HC-AUTOMATION-REVIEW-POINT", "candidate reached the fixed human review point", status="BLOCKED")
    checks = [check("HC-AUTOMATION-POLICY", "PASS", "automation action is authorized by the current task-bound policy", action=action, mode=policy["mode"])]
    if action == "commit":
        commit_subject = _milestone_commit_subject(task_id, message)
        dirty = _status_paths(p["root"])
        if not dirty:
            raise ControlError("HC-AUTOMATION-MILESTONE-EMPTY", "there are no task changes to commit", status="BLOCKED")
        staged = [value for value in git(p["root"], "diff", "--cached", "--name-only", required=False).splitlines() if value]
        if staged:
            raise ControlError("HC-AUTOMATION-WORKTREE-CLEAN", "automatic milestone commit refuses pre-staged user changes", status="BLOCKED", details={"staged": staged})
        product = [value for value in dirty if not value.startswith(".vibe-control/")]
        if state["phase"] in {"CANDIDATE_FROZEN", "VERIFIED", "AUDITED", "ACCEPTED", "RELEASE_READY"} and product:
            raise ControlError("HC-AUTOMATION-CANDIDATE-DRIFT", "product changes after candidate freeze require a new candidate", status="BLOCKED", details={"paths": product})
        control_changes = [value for value in dirty if value.startswith(".vibe-control/")]
        active_task_lock = task_lock_path.relative_to(p["root"]).as_posix()
        unexpected_control = [
            value for value in control_changes
            if value != active_task_lock and not any(_path_matches(value, pattern) for pattern in AUTOMATED_CONTROL_OUTPUTS)
        ]
        if unexpected_control:
            raise ControlError("HC-AUTOMATION-CONTROL-SCOPE", "automatic commit encountered an unowned control-plane path", status="BLOCKED", details={"paths": unexpected_control})
        forbidden = [value for value in product if any(_path_matches(value, pattern) for pattern in contract["forbiddenPaths"])]
        outside = [value for value in product if not any(_path_matches(value, pattern) for pattern in contract["allowedPaths"])]
        if forbidden or outside:
            raise ControlError("HC-AUTOMATION-PATH-SCOPE", "automatic commit scope escapes the locked task", details={"forbidden": forbidden, "outsideAllowed": outside})
        checks.append(check("HC-AUTOMATION-COMMIT-SCOPE", "PASS", "dirty paths stay inside the task envelope", paths=dirty))
        staged_result = _git_result(p["root"], "add", "-A")
        if staged_result.returncode != 0:
            raise ControlError("HC-AUTOMATION-MILESTONE-COMMIT", "automatic staging failed", status="BLOCKED")
        committed = _git_result(p["root"], "commit", "-m", commit_subject)
        if committed.returncode != 0:
            reset = _git_result(p["root"], "reset", "--quiet")
            raise ControlError(
                "HC-AUTOMATION-MILESTONE-COMMIT",
                "automatic milestone commit failed; task files remain in the worktree",
                status="BLOCKED",
                details={
                    "returnCode": committed.returncode,
                    "stderr": committed.stderr.strip(),
                    "stdout": committed.stdout.strip(),
                    "stagingCleared": reset.returncode == 0,
                    "resetStderr": reset.stderr.strip(),
                },
            )
        milestone = git(p["root"], "rev-parse", "HEAD")
        checks.append(check("HC-AUTOMATION-MILESTONE-COMMIT", "PASS", "task-scoped milestone commit created", commit=milestone, subject=commit_subject))
    if action == "push":
        if policy["pushPolicy"] != "EXISTING_UPSTREAM_MILESTONES":
            raise ControlError("HC-AUTOMATION-PUSH-POLICY", "current automation mode does not authorize push", status="BLOCKED")
        if clean_status(p["root"]):
            raise ControlError("HC-AUTOMATION-WORKTREE-CLEAN", "automatic push requires a clean worktree", status="BLOCKED")
        milestone_history = _assert_push_history_scope(p["root"], task_lock_path, task_lock, contract)
        binding = policy["remoteBinding"]
        branch = git(p["root"], "branch", "--show-current", required=False)
        upstream = git(p["root"], "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", required=False)
        if branch != binding["branch"] or upstream != binding["upstream"]:
            raise ControlError("HC-AUTOMATION-UPSTREAM", "current branch or upstream differs from the authorized target", status="BLOCKED", details={"branch": branch, "upstream": upstream})
        remote_url = git(p["root"], "remote", "get-url", binding["remote"], required=False)
        actual_url_hash = sha256_bytes(normalized_remote_url(remote_url).encode("utf-8"))
        if not remote_url or actual_url_hash != binding["remoteUrlSha256"]:
            raise ControlError("HC-AUTOMATION-REMOTE-DRIFT", "authorized remote identity drifted", status="BLOCKED", details={"actualRemoteUrlSha256": actual_url_hash})
        remote_head = _git_result(p["root"], "ls-remote", "--heads", binding["remote"], f"refs/heads/{binding['branch']}")
        if remote_head.returncode != 0 or not remote_head.stdout.strip():
            raise ControlError("HC-AUTOMATION-UPSTREAM", "authorized upstream cannot be read", status="BLOCKED")
        remote_oid = remote_head.stdout.split()[0]
        ancestor = _git_result(p["root"], "merge-base", "--is-ancestor", remote_oid, "HEAD")
        if ancestor.returncode != 0:
            raise ControlError("HC-AUTOMATION-UPSTREAM", "remote branch is not an ancestor of the local milestone; automatic merge or rebase is forbidden", status="BLOCKED", details={"remoteHead": remote_oid})
        checks.append(check("HC-AUTOMATION-PUSH-SCOPE", "PASS", "every committed path after the task baseline stays inside the authorized milestone envelope", baselineCommit=milestone_history["baselineCommit"], commits=milestone_history["commits"]))
        checks.append(check("HC-AUTOMATION-UPSTREAM", "PASS", "existing upstream identity is bound and the push is fast-forward eligible", remote=binding["remote"], branch=branch, remoteHead=remote_oid))
        pushed = _git_result(p["root"], "push", "--porcelain", binding["remote"], f"HEAD:refs/heads/{binding['branch']}")
        if pushed.returncode != 0:
            raise ControlError("HC-AUTOMATION-PUSH-CONFLICT", "non-force milestone push failed; remote history and credentials were not modified", status="BLOCKED", details={"returnCode": pushed.returncode})
        checks.append(check("HC-AUTOMATION-PUSH", "PASS", "milestone was pushed without force to the bound upstream", commit=git(p["root"], "rev-parse", "HEAD")))
    data = {"action": action, "mode": policy["mode"], "policyId": policy["policyId"], "authorized": True}
    if action == "commit":
        data["milestoneCommit"] = git(p["root"], "rev-parse", "HEAD")
        data["commitSubject"] = commit_subject
    if action == "push":
        data["pushedCommit"] = git(p["root"], "rev-parse", "HEAD")
    return envelope(status="PASS", checks=checks, data=data)
