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
    SCHEMA_VERSION, ControlError, canonical_bytes, check, clean_status, envelope, file_ref, git,
    git_root, load_json, now_iso, safe_relative, sha256_bytes, sha256_file,
    verify_ref, write_json_atomic,
)
from .checkpoint_control import guard_effects
from .schema import validate_object


STOP_CONDITIONS = frozenset({
    "AUTOMATED_CHECKPOINTS_COMPLETE",
    "HUMAN_CHECKPOINT",
    "OWNER_DECISION",
    "BOUNDARY_CHANGE",
    "R3_OR_IRREVERSIBLE_ACTION",
    "ACTION_GUARD",
    "ENVIRONMENT_BLOCKED",
    "PUSH_CONFLICT",
    "USER_INTERRUPT",
})
ACTIONS = frozenset({"dispatch", "continue", "commit", "push"})
PRE_CANDIDATE_CLAIM_BLOCKERS = frozenset({
    # These checks describe facts that can only close after implementation is
    # committed and a candidate is frozen.  They deny claims, but they do not
    # make an in-scope implementation commit unsafe.
    "HC-DEVELOPMENT-PACKAGE-CLAIM-CAP",
    "HC-ASSURANCE-MATRIX-INDEPENDENT",
    "HC-ASSURANCE-MATRIX-FORMAL",
    "HC-PROJECT-REVIEW-GATE",
})
PRE_CANDIDATE_COMMIT_BLOCKERS = PRE_CANDIDATE_CLAIM_BLOCKERS | {"HC-WORKTREE-CLEAN"}
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
TEAM_CAPABILITIES = frozenset({
    "team.create", "team.message", "team.wait", "team.inspect",
    "team.persistent_tasks", "team.independent_context", "team.work_isolation",
})
SUBAGENT_CAPABILITIES = frozenset({"subagent.spawn", "subagent.message", "subagent.wait"})
LEGACY_BACKENDS = {"CODEX_THREADS": "TEAM", "SUBAGENTS": "SUBAGENT"}


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
        "coordination": policy.get("coordination"),
    }
    if "remoteBinding" in policy:
        value["remoteBinding"] = policy["remoteBinding"]
    return value


def _canonical_summary(policy: dict[str, Any]) -> str:
    return canonical_bytes(_semantic(policy)).decode("utf-8")


def _expected_policy_id(policy: dict[str, Any]) -> str:
    return f"automation-{sha256_bytes(_canonical_summary(policy).encode('utf-8'))[:12]}"


def resolve_coordination_backend(
    capabilities: list[str] | set[str], requested: str = "AUTO", *,
    host_requires_authorization: bool = False, authorization_granted: bool = False,
) -> dict[str, Any]:
    """Resolve host capability data without imposing a Skill-level worker limit."""
    normalized_request = LEGACY_BACKENDS.get(requested, requested)
    observed = set(capabilities)
    if normalized_request not in {"AUTO", "TEAM", "SUBAGENT", "SERIAL"}:
        raise ControlError("HC-AUTOMATION-COORDINATION", "unknown coordination backend")
    available = []
    if TEAM_CAPABILITIES.issubset(observed):
        available.append("TEAM")
    if SUBAGENT_CAPABILITIES.issubset(observed):
        available.append("SUBAGENT")
    available.append("SERIAL")
    team_requires_prompt = "TEAM" in available and host_requires_authorization and not authorization_granted
    selectable = [item for item in available if item != "TEAM" or not team_requires_prompt]
    resolved = selectable[0] if normalized_request == "AUTO" else normalized_request
    if resolved == "TEAM" and team_requires_prompt:
        resolved = "SUBAGENT" if "SUBAGENT" in selectable else "SERIAL"
    if resolved == "TEAM" and "TEAM" not in available:
        resolved = "SUBAGENT" if "SUBAGENT" in available else "SERIAL"
    if resolved == "SUBAGENT" and "SUBAGENT" not in available:
        resolved = "SERIAL"
    return {
        "requestedBackend": normalized_request,
        "resolvedBackend": resolved,
        "observedCapabilities": sorted(observed),
        "workerLimit": "HOST_CAPACITY_ONLY",
        "hostRequiresAuthorization": host_requires_authorization,
        "authorizationGranted": authorization_granted,
        "authorizationPromptRequired": team_requires_prompt,
    }


def guard_effect_from_checks(contract: dict[str, Any], checks: list[dict[str, Any]]) -> str:
    """Classify observed failures; health alone never implies an environment failure."""
    active = {item.get("id", "") for item in checks if item.get("status") != "PASS"}
    effects = guard_effects(contract)
    if any(value == "DEPENDENCY_BLOCKED" or value.startswith(("HC-DEPENDENCY-", "HC-EXECUTABLE-RESOLUTION", "HC-EVIDENCE-GIT-BYTE-POLICY", "VC-GIT-")) for value in active):
        return effects["ENVIRONMENT"]
    if any(value.startswith(("HC-AUTOMATION-BOUNDARY-CHANGE", "HC-AUTOMATION-PATH-SCOPE", "HC-CANDIDATE-DRIFT")) for value in active):
        return effects["MUTATION"]
    if any(value.startswith(("HC-CHECKPOINT-HUMAN-DECISION", "HC-AUTOMATION-REVIEW-POINT", "HC-AUTOMATION-R3-STOP")) for value in active):
        return effects["HUMAN"]
    if active:
        return effects["CLAIM"]
    return effects["PROCESS"]


def failure_disposition(
    contract: dict[str, Any],
    action: str,
    health: str,
    active_effect: str | None = None,
    *,
    allow_claim_guarded_milestone: bool = False,
) -> dict[str, Any]:
    """Keep claim failures repairable; admit side effects only via a narrow caller proof."""
    effects = guard_effects(contract)
    effect = active_effect or (effects["CLAIM"] if health in {"BLOCKED", "FAILED"} else effects["PROCESS"])
    repair_required = health in {"BLOCKED", "FAILED"} or effect == effects["CLAIM"]
    if effect in {effects["ENVIRONMENT"], effects["MUTATION"], effects["HUMAN"]}:
        return {"allowed": False, "repairRequired": repair_required, "effect": effect, "claimEligible": False}
    if repair_required and action in {"commit", "push"} and allow_claim_guarded_milestone and effect == effects["CLAIM"]:
        return {"allowed": True, "repairRequired": True, "effect": effect, "claimEligible": False}
    if repair_required and action in {"commit", "push"}:
        return {"allowed": False, "repairRequired": True, "effect": effects["MUTATION"], "claimEligible": False}
    return {"allowed": True, "repairRequired": repair_required, "effect": effect, "claimEligible": False}


def pre_candidate_milestone_side_effect_allowed(
    action: str,
    state: dict[str, Any],
    candidate_id: str | None,
    validation_checks: list[dict[str, Any]],
    observed_effect: str,
    contract: dict[str, Any],
) -> bool:
    """Allow only the narrow claim-blocked side effect before candidate freeze.

    A pre-candidate project is expected to lack later review/claim facts.  That
    absence must not prevent saving an authorized implementation milestone.
    Any integrity failure, invalidation, unknown blocker, candidate-bound
    failure, non-clear declared health, or non-claim guard keeps the old
    fail-closed behavior.  A dirty-worktree blocker is expected for commit but
    is deliberately ineligible for push; the existing action-specific scope,
    history, remote and fast-forward checks still run after this admission.
    """
    if (
        action not in {"commit", "push"}
        or candidate_id is not None
        or state.get("phase") not in {"CONTRACT_LOCKED", "IMPLEMENTING"}
        or state.get("health") != "CLEAR"
        or observed_effect != guard_effects(contract)["CLAIM"]
    ):
        return False
    nonpassing = [item for item in validation_checks if item.get("status") != "PASS"]
    allowed_blockers = PRE_CANDIDATE_COMMIT_BLOCKERS if action == "commit" else PRE_CANDIDATE_CLAIM_BLOCKERS
    return bool(nonpassing) and all(
        item.get("status") == "BLOCKED" and item.get("id") in allowed_blockers
        for item in nonpassing
    )


def _normalize_policy_spec(spec: dict[str, Any]) -> dict[str, Any]:
    policy = copy.deepcopy(spec)
    legacy = policy.get("schemaVersion") == "1.0"
    policy["schemaVersion"] = SCHEMA_VERSION
    stop_conditions = set(policy.get("stopConditions", []))
    if "HARD_FAILURE" in stop_conditions:
        stop_conditions.remove("HARD_FAILURE")
        stop_conditions.update({"ACTION_GUARD", "ENVIRONMENT_BLOCKED"})
    policy["stopConditions"] = sorted(stop_conditions or STOP_CONDITIONS)
    raw_coordination = policy.get("coordination") or {}
    requested = raw_coordination.get("requestedBackend", raw_coordination.get("backend", "AUTO"))
    capabilities = raw_coordination.get("observedCapabilities", raw_coordination.get("capabilities", []))
    policy["coordination"] = resolve_coordination_backend(
        capabilities, requested,
        host_requires_authorization=bool(raw_coordination.get("hostRequiresAuthorization", False)),
        authorization_granted=bool(raw_coordination.get("authorizationGranted", False)),
    )
    if legacy:
        confirmation = policy.get("confirmation", {})
        summary = _canonical_summary(policy)
        confirmation["summary"] = summary
        confirmation["summarySha256"] = sha256_bytes(summary.encode("utf-8"))
        policy["confirmation"] = confirmation
        policy["policyId"] = _expected_policy_id(policy)
    return policy


def default_policy_spec(
    project_id: str,
    confirmation: dict[str, Any],
    *,
    mode: str = "AUTO_LOCAL_TO_REVIEW",
) -> dict[str, Any]:
    """Derive the low-friction default from the already confirmed project positioning."""
    if mode not in {"AUTO_LOCAL_TO_REVIEW", "MANUAL_STAGE_CONFIRMATION"}:
        raise ControlError("HC-AUTOMATION-MODE", "derived local policy supports automatic-local or explicit manual mode")
    automatic = mode == "AUTO_LOCAL_TO_REVIEW"
    policy: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "policyId": "pending",
        "projectId": project_id,
        "mode": mode,
        "commitPolicy": "MILESTONE_COMMITS" if automatic else "MANUAL",
        "pushPolicy": "NONE",
        "stopConditions": sorted(STOP_CONDITIONS),
        "coordination": resolve_coordination_backend([], "AUTO"),
        "confirmation": {
            "actorId": confirmation["actorId"],
            "summary": "pending",
            "summarySha256": "0" * 64,
            "record": confirmation["record"],
            # The default is derived from an already confirmed project record.
            # A fixed fallback keeps bootstrap/upgrade materialization reproducible
            # when the source confirmation format has no timestamp field.
            "confirmedAt": confirmation.get("confirmedAt", "1970-01-01T00:00:00+00:00"),
        },
    }
    summary = _canonical_summary(policy)
    policy["confirmation"]["summary"] = summary
    policy["confirmation"]["summarySha256"] = sha256_bytes(summary.encode("utf-8"))
    policy["policyId"] = _expected_policy_id(policy)
    return policy


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
    expected_coordination = resolve_coordination_backend(
        policy["coordination"]["observedCapabilities"],
        policy["coordination"]["requestedBackend"],
        host_requires_authorization=policy["coordination"]["hostRequiresAuthorization"],
        authorization_granted=policy["coordination"]["authorizationGranted"],
    )
    if policy["coordination"] != expected_coordination:
        raise ControlError("HC-AUTOMATION-COORDINATION", "coordination result does not match observed host capabilities")


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
    policy = _normalize_policy_spec(spec)
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
        "schemaVersion": SCHEMA_VERSION,
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
        "schemaVersion": SCHEMA_VERSION, "projectId": lock["projectId"],
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
    # A dirty tree is a concrete push precondition failure, not a generic claim
    # blocker.  Report it before the read-only validation projection so callers
    # receive the stable, actionable reason while the push remains fail-closed.
    if action == "push":
        dirty_for_push = clean_status(p["root"])
        if dirty_for_push:
            raise ControlError(
                "HC-AUTOMATION-WORKTREE-CLEAN",
                "automatic push requires a clean worktree",
                status="BLOCKED",
                details={"entries": dirty_for_push},
            )
    from .controller import validate as project_validate
    projection = project_validate(project, mutate_state=False)
    validation_checks = projection.get("integrity", {}).get("checks", [])
    observed_effect = guard_effect_from_checks(contract, validation_checks)
    allow_claim_guarded_milestone = pre_candidate_milestone_side_effect_allowed(
        action, state, candidate_id, validation_checks, observed_effect, contract,
    )
    disposition = failure_disposition(
        contract,
        action,
        state["health"],
        observed_effect,
        allow_claim_guarded_milestone=allow_claim_guarded_milestone,
    )
    repair_mode = disposition["repairRequired"]
    if not disposition["allowed"]:
        guard_id = {
            "ENVIRONMENT_BLOCKED": "HC-AUTOMATION-ENVIRONMENT-BLOCKED",
            "HUMAN_DECISION": "HC-AUTOMATION-HUMAN-DECISION",
        }.get(disposition["effect"], "HC-AUTOMATION-ACTION-GUARD")
        raise ControlError(guard_id, "the observed guard requires repair, authorization or human review before this action", status="BLOCKED", details={"effect": disposition["effect"]})
    if action in {"dispatch", "continue"} and state["phase"] in {"VERIFIED", "AUDITED", "ACCEPTED", "RELEASE_READY"}:
        raise ControlError("HC-AUTOMATION-REVIEW-POINT", "candidate reached the fixed human review point", status="BLOCKED")
    checks = [check("HC-AUTOMATION-POLICY", "PASS", "automation action is authorized by the current task-bound policy", action=action, mode=policy["mode"])]
    if repair_mode:
        checks.append(check("HC-AUTOMATION-CLAIM-GUARD", "PASS", "failed execution remains repairable while all claims stay ineligible", effect=disposition["effect"]))
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
    data = {"action": action, "mode": policy["mode"], "policyId": policy["policyId"], "authorized": True, "coordination": policy["coordination"], "repairRequired": repair_mode, "claimEligible": False if repair_mode else None}
    if action == "commit":
        data["milestoneCommit"] = git(p["root"], "rev-parse", "HEAD")
        data["commitSubject"] = commit_subject
    if action == "push":
        data["pushedCommit"] = git(p["root"], "rev-parse", "HEAD")
    return envelope(
        status="PASS", checks=checks, data=data,
        plain_language={
            "whatWasDone": "已按当前授权检查并推进了一个安全的自动化动作。" if not repair_mode else "已保留失败事实，并允许在原合同范围内继续修复。",
            "whatWorksNow": "自动推进仍受任务、候选和副作用边界约束。",
            "whatStillDoesNotWork": "失败的验证尚未通过，因此不能形成任何新的验收或发行结论。" if repair_mode else "尚未执行或尚未验证的里程碑仍不算完成。",
            "userImpact": "无需因一次可修复失败立即重做流程；修复完成并重新验证前不会获得声明资格。" if repair_mode else "流程会继续到预设人工复核点，边界变化仍会停止。",
            "canContinue": "可以在原合同范围内继续修复和验证。",
            "canRelease": "验证失败尚未闭合，现在不能作为最终版本交付。",
        },
    )
