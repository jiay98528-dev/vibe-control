from __future__ import annotations

import html
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .common import (
    canonical_bytes,
    ControlError,
    check,
    clean_status,
    envelope,
    git,
    git_root,
    now_iso,
    sha256_file,
    sha256_bytes,
    write_json_atomic,
)
from .checkpoint_control import derive_checkpoint_result
from .controller import validate as controller_validate


SOURCE = "DERIVED_NON_AUTHORITATIVE"
MANUAL_MODE = "MANUAL_STAGE_CONFIRMATION"
_OBJECTIVE_LINE = re.compile(
    r"^\s*[-*]\s+`(?P<id>(?:KO|KF|NG)-[A-Z0-9][A-Z0-9._-]*)`\s*[:：]\s*(?P<statement>.+?)\s*$"
)
_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_id, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle_id, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
            if not value.endswith("\n"):
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _safe_component(value: Any, fallback: str) -> str:
    normalized = _SAFE_COMPONENT.sub("-", str(value or "")).strip("-._")
    return normalized[:80] or fallback


def _inside(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _default_output(root: Path, state: dict[str, Any]) -> Path:
    if os.name == "nt":
        base = Path(
            os.environ.get("LOCALAPPDATA")
            or (Path.home() / "AppData" / "Local")
        )
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    project_key = _safe_component(root.name, "project")
    task_key = _safe_component(state.get("taskId"), "no-active-task")
    candidate = (base / "vibe-control" / "dashboards" / project_key / task_key).resolve()
    if _inside(candidate, root):
        candidate = (
            root.parent
            / ".vibe-control-dashboards"
            / project_key
            / task_key
        ).resolve()
    return candidate


def _control_fingerprint(control: Path) -> str:
    inventory: list[dict[str, Any]] = []
    if control.is_dir():
        for path in sorted(control.rglob("*"), key=lambda item: item.as_posix()):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(control)
            if relative.parts and relative.parts[0] in {"runtime", "legacy"}:
                continue
            inventory.append(
                {
                    "path": relative.as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    return sha256_bytes(canonical_bytes(inventory))


def _read_optional_json(path: Path, issues: list[str]) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        issues.append(f"HC-DASHBOARD-INPUT-SAFETY:{path.name}")
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        issues.append(f"HC-DASHBOARD-INPUT-MALFORMED:{path.name}")
        return None
    if not isinstance(value, dict):
        issues.append(f"HC-DASHBOARD-INPUT-SHAPE:{path.name}")
        return None
    return value


def _read_project_ref(
    root: Path, ref: Any, issues: list[str]
) -> dict[str, Any] | None:
    if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
        issues.append("HC-DASHBOARD-CONTRACT-REF")
        return None
    relative = Path(ref["path"])
    if relative.is_absolute() or ".." in relative.parts:
        issues.append("HC-DASHBOARD-CONTRACT-REF")
        return None
    path = (root / relative).resolve()
    if not _inside(path, root):
        issues.append("HC-DASHBOARD-CONTRACT-REF")
        return None
    return _read_optional_json(path, issues)


def _matching_objects(
    directory: Path,
    *,
    task_id: str | None,
    candidate_id: str | None,
    identity_key: str,
    issues: list[str],
) -> list[dict[str, Any]]:
    if not directory.is_dir():
        return []
    values: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        value = _read_optional_json(path, issues)
        if value is None or identity_key not in value:
            continue
        if task_id is not None and value.get("taskId") != task_id:
            continue
        if candidate_id is not None and value.get("candidateId") != candidate_id:
            continue
        values.append(value)
    return values


def _latest(values: list[dict[str, Any]], time_key: str) -> dict[str, Any] | None:
    if not values:
        return None
    return max(values, key=lambda value: str(value.get(time_key, "")))


def _objective_statements(root: Path, lock: dict[str, Any] | None) -> dict[str, str]:
    if not lock:
        return {}
    ref = lock.get("document")
    if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
        return {}
    relative = Path(ref["path"])
    if relative.is_absolute() or ".." in relative.parts:
        return {}
    path = (root / relative).resolve()
    if not _inside(path, root) or not path.is_file() or path.is_symlink():
        return {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return {}
    result: dict[str, str] = {}
    for line in lines:
        match = _OBJECTIVE_LINE.match(line)
        if match:
            result[match.group("id")] = match.group("statement")
    return result


def _number(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _coverage_projection(
    validation: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    checks = validation.get("integrity", {}).get("checks", [])
    coverage = next(
        (
            item
            for item in reversed(checks)
            if isinstance(item, dict) and item.get("id") == "HC-REQUIRED-CASE-COVERAGE"
        ),
        None,
    )
    raw_mapping = (
        coverage.get("details", {}).get("eligibleEvidenceByCase", {})
        if isinstance(coverage, dict)
        else {}
    )
    if not isinstance(raw_mapping, dict):
        raw_mapping = {}
    by_id = {
        str(item.get("evidenceId")): item
        for item in evidence
        if isinstance(item.get("evidenceId"), str)
    }
    eligible: dict[str, dict[str, Any]] = {}
    for case_id, evidence_id in raw_mapping.items():
        if not isinstance(case_id, str) or not isinstance(evidence_id, str):
            continue
        item = by_id.get(evidence_id)
        if item is None or item.get("caseId") != case_id:
            continue
        eligible[case_id] = item
    return eligible


def _case_failure_status(validation: dict[str, Any], case_id: str) -> str:
    statuses = []
    for item in validation.get("integrity", {}).get("checks", []):
        if not isinstance(item, dict):
            continue
        details = item.get("details")
        if isinstance(details, dict) and details.get("caseId") == case_id:
            statuses.append(str(item.get("status") or "PASS"))
    if "FAIL" in statuses or "INVALIDATED" in statuses:
        return "FAIL"
    if "BLOCKED" in statuses:
        return "BLOCKED"
    return "PENDING"


def _evidence_summary(
    eligible: dict[str, dict[str, Any]],
    required_cases: list[str],
    validation: dict[str, Any],
    raw_evidence: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    counters = {"executed": 0, "passed": 0, "failed": 0, "skipped": 0, "timedOut": 0}
    for item in eligible.values():
        raw = item.get("counters") if isinstance(item.get("counters"), dict) else {}
        for name in counters:
            counters[name] += _number(raw.get(name))
    raw_cases = {
        str(item.get("caseId"))
        for item in raw_evidence
        if isinstance(item.get("caseId"), str)
    }
    case_rows: list[dict[str, Any]] = []
    for case_id in required_cases:
        if case_id in eligible:
            observed = "PASS"
            evidence_ids = [str(eligible[case_id]["evidenceId"])]
        else:
            observed = _case_failure_status(validation, case_id)
            if observed == "PENDING" and case_id in raw_cases:
                observed = "BLOCKED"
            evidence_ids = []
        case_rows.append(
            {
                "caseId": case_id,
                "status": observed,
                "evidenceIds": evidence_ids,
            }
        )
    summary = {
        "records": len(eligible),
        "rawRecords": len(raw_evidence),
        "requiredCases": len(required_cases),
        "reportedCases": sum(1 for row in case_rows if row["status"] != "PENDING"),
        "caseResults": {
            name: sum(1 for row in case_rows if row["status"] == name)
            for name in ("PASS", "FAIL", "BLOCKED", "PENDING")
        },
        "counters": counters,
    }
    return summary, case_rows


def _checkpoint_rows(
    contract: dict[str, Any] | None,
    evidence_by_case: dict[str, dict[str, Any]],
    decision: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    checkpoints = (
        contract.get("acceptanceCheckpoints", [])
        if isinstance(contract, dict)
        else []
    )
    decisions = {
        item.get("checkpointId"): item.get("decision")
        for item in (decision or {}).get("checkpointDecisions", [])
        if isinstance(item, dict)
    }
    rows: list[dict[str, Any]] = []
    for checkpoint in checkpoints:
        if not isinstance(checkpoint, dict):
            continue
        checkpoint_id = str(checkpoint.get("id") or "UNKNOWN")
        checkpoint_type = str(checkpoint.get("type") or "UNKNOWN")
        case_ids = (
            [str(value) for value in checkpoint.get("caseIds", [])]
            if isinstance(checkpoint.get("caseIds", []), list)
            else []
        )
        if checkpoint_type == "HUMAN":
            observed = decisions.get(checkpoint_id, "PENDING")
            evidence_ids = []
        else:
            try:
                observed, evidence_ids = derive_checkpoint_result(
                    checkpoint, evidence_by_case
                )
            except (KeyError, TypeError):
                observed, evidence_ids = "BLOCKED", []
        rows.append(
            {
                "id": checkpoint_id,
                "type": checkpoint_type,
                "statement": str(checkpoint.get("statement") or ""),
                "requiredForClaim": str(checkpoint.get("requiredForClaim") or "DIAGNOSTIC"),
                "status": observed,
                "caseIds": case_ids,
                "evidenceIds": evidence_ids,
                "notProven": [str(value) for value in checkpoint.get("notProven", [])],
            }
        )
    return rows


def _git_status(root: Path) -> dict[str, Any]:
    branch = git(root, "branch", "--show-current", required=False) or "DETACHED"
    upstream = git(
        root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", required=False
    )
    dirty_entries = clean_status(root)
    head = git(root, "rev-parse", "HEAD", required=False)
    latest = git(root, "log", "-1", "--format=%H%x09%s", required=False)
    latest_commit: dict[str, Any] | None = None
    if latest:
        commit, _, subject = latest.partition("\t")
        latest_commit = {"commit": commit, "subject": subject}
    ahead = 0
    behind = 0
    if upstream:
        counts = git(
            root,
            "rev-list",
            "--left-right",
            "--count",
            "HEAD...@{u}",
            required=False,
        ).split()
        if len(counts) == 2 and all(value.isdigit() for value in counts):
            ahead, behind = int(counts[0]), int(counts[1])
    sync = (
        "NO_UPSTREAM"
        if not upstream
        else "DIVERGED"
        if ahead and behind
        else "AHEAD"
        if ahead
        else "BEHIND"
        if behind
        else "SYNCHRONIZED"
    )
    return {
        "head": head or None,
        "branch": branch,
        "upstream": upstream or None,
        "remoteSync": sync,
        "ahead": ahead,
        "behind": behind,
        "latestCommit": latest_commit,
        "dirty": bool(dirty_entries),
        "dirtyEntryCount": len(dirty_entries),
    }


def _automation_state(
    control: Path, lock: dict[str, Any] | None, issues: list[str]
) -> dict[str, Any]:
    policy_ref = lock.get("automationPolicy") if isinstance(lock, dict) else None
    policy: dict[str, Any] | None = None
    if isinstance(policy_ref, dict) and isinstance(policy_ref.get("path"), str):
        candidate = control.parent / policy_ref["path"]
        if _inside(candidate, control.parent):
            policy = _read_optional_json(candidate.resolve(), issues)
    if policy is None:
        policy = _read_optional_json(control / "automation-policy.json", issues)
    if policy is None:
        return {
            "mode": MANUAL_MODE,
            "commitPolicy": "MANUAL",
            "pushPolicy": "NONE",
            "policyId": None,
            "source": "BACKWARD_COMPATIBLE_DEFAULT",
        }
    return {
        "mode": str(policy.get("mode") or MANUAL_MODE),
        "commitPolicy": str(policy.get("commitPolicy") or "MANUAL"),
        "pushPolicy": str(policy.get("pushPolicy") or "NONE"),
        "policyId": policy.get("policyId"),
        "source": "LOCKED_POLICY",
        "remoteBinding": policy.get("remoteBinding"),
    }


def _human_decision(
    checkpoints: list[dict[str, Any]],
    candidate: dict[str, Any] | None,
    blockers: list[str],
    contract: dict[str, Any] | None,
    review: dict[str, Any] | None,
    decision: dict[str, Any] | None,
) -> dict[str, Any]:
    automated = [item for item in checkpoints if item["type"] == "AUTOMATED"]
    automated_closed = bool(automated) and all(item["status"] == "PASS" for item in automated)
    risk = str((contract or {}).get("risk") or "R0")
    review_closed = risk not in {"R2", "R3"} or (
        review is not None and review.get("result") == "PASS"
    )
    if not candidate or not automated_closed or not review_closed or blockers or decision:
        return {
            "required": False,
            "id": None,
            "prompt": None,
            "kind": None,
        }
    pending_human = next(
        (
            item
            for item in checkpoints
            if item["type"] == "HUMAN" and item["status"] != "PASS"
        ),
        None,
    )
    if pending_human:
        return {
            "required": True,
            "id": pending_human["id"],
            "prompt": pending_human["statement"],
            "kind": "HUMAN_CHECKPOINT",
        }
    human_finding = next(
        (
            item
            for item in (review or {}).get("findings", [])
            if isinstance(item, dict)
            and item.get("status") == "OPEN"
            and item.get("classification") == "HUMAN_DECISION"
        ),
        None,
    )
    if human_finding:
        return {
            "required": True,
            "id": str(human_finding.get("id") or "HUMAN-DECISION"),
            "prompt": str(
                human_finding.get("minimumFix")
                or human_finding.get("reproduction")
                or "裁决审核者登记的人工决定事项。"
            ),
            "kind": "AUDIT_HUMAN_DECISION",
        }
    return {
        "required": True,
        "id": "OWNER-CANDIDATE-REVIEW",
        "prompt": "复核当前候选的已证明范围、未证明事项和变更范围。",
        "kind": "OWNER_REVIEW",
    }


def _state_projection(
    validation: dict[str, Any], declared: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    report_state = validation.get("state")
    report_declared = (
        report_state.get("declared")
        if isinstance(report_state, dict)
        and isinstance(report_state.get("declared"), dict)
        else declared
    )
    raw_derived = (
        report_state.get("derived")
        if isinstance(report_state, dict)
        and isinstance(report_state.get("derived"), dict)
        else {}
    )
    declared_view = {
        "phase": str(report_declared.get("phase") or "DRAFT"),
        "health": str(report_declared.get("health") or "BLOCKED"),
        "claimLevel": str(report_declared.get("claimLevel") or "DIAGNOSTIC"),
        "taskId": report_declared.get("taskId"),
        "candidateId": report_declared.get("candidateId"),
        "updatedAt": report_declared.get("updatedAt"),
    }
    derived_view = {
        "phase": str(raw_derived.get("phase") or declared_view["phase"]),
        "health": str(raw_derived.get("health") or declared_view["health"]),
        "claimLevel": str(
            raw_derived.get("claimLevel") or declared_view["claimLevel"]
        ),
        "taskId": declared_view["taskId"],
        "candidateId": declared_view["candidateId"],
        "updatedAt": declared_view["updatedAt"],
    }
    return declared_view, derived_view


def _build_snapshot(project: Path) -> dict[str, Any]:
    root = git_root(project.resolve())
    control = root / ".vibe-control"
    issues: list[str] = []
    state = _read_optional_json(control / "stage-state.json", issues) or {
        "phase": "DRAFT",
        "health": "BLOCKED",
        "claimLevel": "DIAGNOSTIC",
        "taskId": None,
        "candidateId": None,
    }
    validation = controller_validate(root, mutate_state=False)
    declared_state, derived_state = _state_projection(validation, state)
    lock = _read_optional_json(control / "project-governance-lock.json", issues)
    objective_lock = _read_optional_json(control / "key-objectives-lock.json", issues)
    task_id = (
        declared_state.get("taskId")
        if isinstance(declared_state.get("taskId"), str)
        else None
    )
    candidate_id = (
        declared_state.get("candidateId")
        if isinstance(declared_state.get("candidateId"), str)
        else None
    )

    task_lock = (
        _read_optional_json(control / "task-locks" / f"{task_id}.json", issues)
        if task_id
        else None
    )
    contract = (
        _read_project_ref(root, task_lock.get("contract"), issues)
        if isinstance(task_lock, dict)
        else None
    )
    candidate = (
        _read_optional_json(control / "candidates" / f"{candidate_id}.json", issues)
        if candidate_id
        else None
    )
    evidence = _matching_objects(
        control / "evidence",
        task_id=task_id,
        candidate_id=candidate_id,
        identity_key="evidenceId",
        issues=issues,
    )
    reviews = _matching_objects(
        control / "reviews",
        task_id=task_id,
        candidate_id=candidate_id,
        identity_key="reviewId",
        issues=issues,
    )
    decisions = _matching_objects(
        control / "decisions",
        task_id=task_id,
        candidate_id=candidate_id,
        identity_key="decisionId",
        issues=issues,
    )
    review = _latest(reviews, "reviewedAt")
    decision = _latest(decisions, "decidedAt")

    required_cases = (
        [str(value) for value in contract.get("requiredCaseIds", [])]
        if isinstance(contract, dict)
        else []
    )
    eligible_evidence = _coverage_projection(validation, evidence)
    evidence_summary, case_rows = _evidence_summary(
        eligible_evidence, required_cases, validation, evidence
    )
    checkpoint_rows = _checkpoint_rows(contract, eligible_evidence, decision)
    automation = _automation_state(control, lock, issues)
    git_state = _git_status(root)

    objective_text = _objective_statements(root, objective_lock)
    objective_refs = (
        [str(value) for value in contract.get("objectiveRefs", [])]
        if isinstance(contract, dict)
        else [str(value) for value in (objective_lock or {}).get("objectiveIds", [])]
    )
    objectives = [
        {"id": value, "statement": objective_text.get(value)} for value in objective_refs
    ]

    formal = validation.get("formal")
    validation_blockers = (
        formal.get("blockers", []) if isinstance(formal, dict) else []
    )
    if not isinstance(validation_blockers, list):
        validation_blockers = ["HC-DASHBOARD-VALIDATION-SHAPE"]
    blockers = list(dict.fromkeys([*issues, *validation_blockers]))

    automated_rows = [item for item in checkpoint_rows if item["type"] == "AUTOMATED"]
    automated_closed = bool(automated_rows) and all(
        item["status"] == "PASS" for item in automated_rows
    )
    review_required = str((contract or {}).get("risk") or "R0") in {"R2", "R3"}
    review_closed = not review_required or (
        review is not None and review.get("result") == "PASS"
    )
    stop_reason = None
    if blockers:
        stop_reason = "HARD_FAILURE_OR_BLOCKED_STATE"
    elif candidate and automated_closed and review_required and not review_closed:
        stop_reason = "AUDIT_REQUIRED"
    elif candidate and automated_closed and review_closed and any(
        item["type"] == "HUMAN" and item["status"] != "PASS"
        for item in checkpoint_rows
    ):
        stop_reason = "HUMAN_CHECKPOINT"
    elif candidate and automated_closed and review_closed and decision is None:
        stop_reason = "AUTOMATED_CHECKPOINTS_COMPLETE"

    human_decision = _human_decision(
        checkpoint_rows, candidate, blockers, contract, review, decision
    )
    proven = [
        f"{item['id']}: {item['statement']}"
        for item in checkpoint_rows
        if item["status"] == "PASS"
    ]
    not_proven: list[str] = []
    for item in checkpoint_rows:
        if item["status"] != "PASS":
            not_proven.append(f"{item['id']}: {item['statement']} ({item['status']})")
        not_proven.extend(item["notProven"])
    not_proven.append("此 Dashboard 是只读派生视图，不证明正式资格或人工验收。")

    candidate_view = None
    if candidate:
        candidate_view = {
            "candidateId": candidate.get("candidateId"),
            "commit": candidate.get("commit"),
            "tree": candidate.get("tree"),
            "changedPaths": candidate.get("changedPaths", []),
            "frozenAt": candidate.get("frozenAt"),
        }

    snapshot = {
        "schemaVersion": "1.0",
        "source": SOURCE,
        "generatedAt": now_iso(),
        "phase": derived_state["phase"],
        "health": derived_state["health"],
        "claim": derived_state["claimLevel"],
        "validationStatus": validation.get("status", "BLOCKED"),
        "project": {
            "id": (lock or {}).get("projectId") or state.get("projectId") or root.name,
            "root": str(root),
            "releaseIntent": (lock or {}).get("releaseIntent"),
            "packageMode": (lock or {}).get("packageMode"),
        },
        "formal": {
            "eligible": False,
            "maxClaimLevel": "DIAGNOSTIC",
            "blockers": ["HC-DASHBOARD-NON-AUTHORITATIVE"],
        },
        "state": derived_state,
        "declaredState": declared_state,
        "derivedState": derived_state,
        "stateDrift": {
            "detected": any(
                declared_state[name] != derived_state[name]
                for name in ("phase", "health", "claimLevel")
            ),
            "fields": [
                name
                for name in ("phase", "health", "claimLevel")
                if declared_state[name] != derived_state[name]
            ],
        },
        "automation": automation,
        "objectives": {
            "goal": contract.get("goal") if isinstance(contract, dict) else None,
            "items": objectives,
        },
        "checkpoints": checkpoint_rows,
        "candidate": candidate_view,
        "cases": case_rows,
        "evidence": evidence_summary,
        "review": {
            "reviewId": review.get("reviewId") if review else None,
            "result": review.get("result") if review else None,
            "findingCount": len(review.get("findings", [])) if review else 0,
            "findings": [
                {
                    "id": item.get("id"),
                    "severity": item.get("severity"),
                    "status": item.get("status"),
                    "classification": item.get("classification"),
                    "affectedClaims": item.get("affectedClaims", []),
                }
                for item in (review or {}).get("findings", [])
                if isinstance(item, dict)
            ],
        },
        "git": git_state,
        "blockers": blockers,
        "stopReason": stop_reason,
        "humanDecision": human_decision,
        "proven": list(dict.fromkeys(proven)),
        "notProven": list(dict.fromkeys(not_proven)),
    }
    snapshot["snapshotSha256"] = sha256_bytes(canonical_bytes(snapshot))
    return snapshot


def _escape(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        value = "是" if value else "否"
    return html.escape(str(value), quote=True)


def _markdown(value: Any) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    return html.escape(text, quote=False).replace("`", "\\`")


def _badge(value: Any) -> str:
    text = str(value or "UNKNOWN")
    tone = (
        "good"
        if text in {"PASS", "CLEAR"}
        else "bad"
        if text in {"FAIL", "FAILED", "INVALIDATED", "REJECT"}
        else "warn"
        if text in {"BLOCKED", "PENDING"}
        else "info"
    )
    return f'<span class="badge {tone}">{_escape(text)}</span>'


def _list(items: list[Any], *, empty: str = "暂无") -> str:
    if not items:
        return f'<p class="empty">{_escape(empty)}</p>'
    return "<ul>" + "".join(f"<li>{_escape(value)}</li>" for value in items) + "</ul>"


def _render_html(snapshot: dict[str, Any]) -> str:
    state = snapshot["state"]
    declared = snapshot["declaredState"]
    drift = snapshot["stateDrift"]
    evidence = snapshot["evidence"]
    candidate = snapshot["candidate"] or {}
    human = snapshot["humanDecision"]
    objectives = snapshot["objectives"]["items"]
    checkpoints = snapshot["checkpoints"]
    cases = snapshot["cases"]
    findings = snapshot["review"]["findings"]
    changed_paths = candidate.get("changedPaths", [])
    objective_markup = _list(
        [
            f"{item['id']} · {item.get('statement') or '已锁定，未提取到文字'}"
            for item in objectives
        ],
        empty="当前没有活动任务目标",
    )
    checkpoint_markup = "".join(
        "<tr>"
        f"<td><code>{_escape(item['id'])}</code></td>"
        f"<td>{_escape(item['type'])}</td>"
        f"<td>{_escape(item['statement'])}</td>"
        f"<td>{_badge(item['status'])}</td>"
        f"<td>{_escape(', '.join(item['caseIds']))}</td>"
        "</tr>"
        for item in checkpoints
    ) or '<tr><td colspan="5" class="empty">当前没有检查点</td></tr>'
    case_markup = "".join(
        "<tr>"
        f"<td><code>{_escape(item['caseId'])}</code></td>"
        f"<td>{_badge(item['status'])}</td>"
        f"<td>{_escape(', '.join(item['evidenceIds']))}</td>"
        "</tr>"
        for item in cases
    ) or '<tr><td colspan="3" class="empty">当前没有 required case</td></tr>'
    finding_markup = _list(
        [
            f"{item.get('id')} · {item.get('severity')} · {item.get('classification')} · {item.get('status')}"
            for item in findings
        ],
        empty="当前审核没有登记 finding",
    )
    decision_markup = (
        f'<div class="decision"><span class="eyebrow">唯一待人工决定</span>'
        f'<h2>{_escape(human["id"])}</h2><p>{_escape(human["prompt"])}</p></div>'
        if human["required"]
        else '<div class="decision muted"><span class="eyebrow">人工决定</span><h2>当前无需决定</h2><p>继续依据阻断项或自动化状态推进。</p></div>'
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>vibe-control · 人工复核</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f1e9; --surface: #fbfaf6; --surface-2: #ece8de;
      --text: #202321; --muted: #676d68; --line: #c9cbc4;
      --good: #176b49; --good-bg: #e0f1e8; --warn: #8a5908; --warn-bg: #faedc9;
      --bad: #a03b35; --bad-bg: #f7dfdc; --info: #315f81; --info-bg: #deebf2;
      --focus: #246b9b; --shadow: rgba(32,35,33,.08);
    }}
    [data-theme="dark"] {{
      color-scheme: dark; --bg: #171a18; --surface: #202421; --surface-2: #292e2a;
      --text: #f1f2ed; --muted: #aeb5af; --line: #414841;
      --good: #75c9a2; --good-bg: #18392c; --warn: #f0c86a; --warn-bg: #3e3218;
      --bad: #f08e87; --bad-bg: #472522; --info: #8ec2e3; --info-bg: #203746;
      --focus: #8ec2e3; --shadow: rgba(0,0,0,.22);
    }}
    @media (prefers-color-scheme: dark) {{
      :root:not([data-theme="light"]) {{
        color-scheme: dark; --bg: #171a18; --surface: #202421; --surface-2: #292e2a;
        --text: #f1f2ed; --muted: #aeb5af; --line: #414841;
        --good: #75c9a2; --good-bg: #18392c; --warn: #f0c86a; --warn-bg: #3e3218;
        --bad: #f08e87; --bad-bg: #472522; --info: #8ec2e3; --info-bg: #203746;
        --focus: #8ec2e3; --shadow: rgba(0,0,0,.22);
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--text); font-family: "Segoe UI Variable", "Segoe UI", sans-serif; line-height: 1.5; }}
    button, a {{ font: inherit; }}
    button:focus-visible, a:focus-visible {{ outline: 3px solid var(--focus); outline-offset: 3px; }}
    code {{ font-family: "Cascadia Mono", "Consolas", monospace; overflow-wrap: anywhere; }}
    .shell {{ max-width: 1440px; margin: 0 auto; padding: 28px; }}
    header {{ display: flex; justify-content: space-between; gap: 24px; align-items: flex-start; border-bottom: 1px solid var(--line); padding-bottom: 22px; }}
    .eyebrow {{ display: block; color: var(--muted); font-size: .75rem; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; }}
    h1 {{ font-size: clamp(1.8rem, 4vw, 3.25rem); line-height: 1.03; letter-spacing: -.035em; margin: 8px 0 10px; }}
    h2 {{ font-size: 1.15rem; margin: 6px 0 10px; }}
    h3 {{ font-size: .92rem; margin: 0 0 12px; }}
    p {{ margin: 0; }}
    .lede {{ color: var(--muted); max-width: 72ch; }}
    .toolbar {{ display: flex; gap: 8px; align-items: center; }}
    .theme {{ border: 1px solid var(--line); background: var(--surface); color: var(--text); border-radius: 8px; padding: 9px 12px; cursor: pointer; }}
    .notice {{ margin: 18px 0; padding: 12px 14px; border: 1px solid var(--warn); background: var(--warn-bg); color: var(--warn); border-radius: 8px; font-weight: 650; }}
    .status-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 10px; margin: 18px 0; }}
    .metric, .panel, .decision {{ background: var(--surface); border: 1px solid var(--line); border-radius: 9px; box-shadow: 0 1px 3px var(--shadow); }}
    .metric {{ padding: 15px; min-height: 100px; }}
    .metric strong {{ display: block; font-size: 1.08rem; margin-top: 12px; overflow-wrap: anywhere; }}
    .layout {{ display: grid; grid-template-columns: minmax(0, 1.65fr) minmax(290px, .8fr); gap: 14px; align-items: start; }}
    .stack {{ display: grid; gap: 14px; }}
    .panel, .decision {{ padding: 18px; overflow: hidden; }}
    .decision {{ border-color: var(--info); background: var(--info-bg); }}
    .decision.muted {{ border-color: var(--line); background: var(--surface-2); }}
    .badge {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 3px 8px; font-size: .75rem; font-weight: 750; white-space: nowrap; }}
    .badge.good {{ background: var(--good-bg); color: var(--good); }} .badge.warn {{ background: var(--warn-bg); color: var(--warn); }}
    .badge.bad {{ background: var(--bad-bg); color: var(--bad); }} .badge.info {{ background: var(--info-bg); color: var(--info); }}
    .table-wrap {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 7px; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 620px; }}
    th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }}
    th {{ color: var(--muted); font-size: .72rem; letter-spacing: .06em; text-transform: uppercase; background: var(--surface-2); }}
    tr:last-child td {{ border-bottom: 0; }}
    ul {{ margin: 0; padding-left: 19px; }} li + li {{ margin-top: 7px; }}
    .empty, .muted-text {{ color: var(--muted); }}
    .facts {{ display: grid; grid-template-columns: auto 1fr; gap: 7px 12px; margin: 0; }}
    .facts dt {{ color: var(--muted); }} .facts dd {{ margin: 0; overflow-wrap: anywhere; }}
    .split {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    footer {{ color: var(--muted); font-size: .78rem; margin-top: 18px; border-top: 1px solid var(--line); padding-top: 14px; }}
    @media (max-width: 900px) {{ .status-grid {{ grid-template-columns: repeat(2,1fr); }} .layout {{ grid-template-columns: 1fr; }} }}
    @media (max-width: 600px) {{ .shell {{ padding: 18px 14px; }} header {{ display: grid; }} .status-grid, .split {{ grid-template-columns: 1fr; }} .toolbar {{ justify-content: flex-start; }} }}
    @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ scroll-behavior: auto !important; transition-duration: .01ms !important; animation-duration: .01ms !important; animation-iteration-count: 1 !important; }} }}
  </style>
</head>
<body>
  <main class="shell" id="main-content">
    <header>
      <div><span class="eyebrow">vibe-control · 人工复核点</span><h1>{_escape(snapshot['project']['id'])}</h1><p class="lede">{_escape(snapshot['objectives']['goal'] or '当前没有活动任务。此页面仍展示控制面与 Git 实况。')}</p></div>
      <div class="toolbar"><button class="theme" id="theme-toggle" type="button" aria-label="切换深浅主题">切换主题</button></div>
    </header>
    <div class="notice" role="note">只读派生视图：不能授予 PASS、人工验收或正式发行资格。</div>
    <section class="status-grid" aria-label="核心状态">
      <div class="metric"><span class="eyebrow">派生阶段</span><strong>{_escape(state['phase'])}</strong></div>
      <div class="metric"><span class="eyebrow">派生健康</span><strong>{_badge(state['health'])}</strong></div>
      <div class="metric"><span class="eyebrow">派生声明</span><strong>{_escape(state['claimLevel'])}</strong></div>
      <div class="metric"><span class="eyebrow">自动化</span><strong>{_escape(snapshot['automation']['mode'])}</strong></div>
    </section>
    <div class="layout">
      <div class="stack">
        {decision_markup}
        <section class="panel"><h2>验收检查点</h2><div class="table-wrap"><table><thead><tr><th>ID</th><th>类型</th><th>可观察事实</th><th>结果</th><th>Case</th></tr></thead><tbody>{checkpoint_markup}</tbody></table></div></section>
        <section class="panel"><h2>Case 与证据</h2><div class="table-wrap"><table><thead><tr><th>Case</th><th>结果</th><th>证据记录</th></tr></thead><tbody>{case_markup}</tbody></table></div><p class="muted-text" style="margin-top:12px">执行 {evidence['counters']['executed']} · 通过 {evidence['counters']['passed']} · 失败 {evidence['counters']['failed']} · Skip {evidence['counters']['skipped']} · Timeout {evidence['counters']['timedOut']}</p></section>
        <section class="panel"><h2>证明边界</h2><div class="split"><div><h3>已经证明</h3>{_list(snapshot['proven'], empty='尚无已闭合检查点')}</div><div><h3>尚未证明</h3>{_list(snapshot['notProven'])}</div></div></section>
      </div>
      <aside class="stack" aria-label="候选与操作状态">
        <section class="panel"><h2>关键目标</h2>{objective_markup}</section>
        <section class="panel"><h2>候选</h2><dl class="facts"><dt>ID</dt><dd><code>{_escape(candidate.get('candidateId'))}</code></dd><dt>Commit</dt><dd><code>{_escape(candidate.get('commit'))}</code></dd><dt>Tree</dt><dd><code>{_escape(candidate.get('tree'))}</code></dd><dt>变更文件</dt><dd>{len(changed_paths)}</dd></dl>{_list(changed_paths, empty='没有冻结候选或没有产品路径变更')}</section>
        <section class="panel"><h2>Git 实况</h2><dl class="facts"><dt>分支</dt><dd>{_escape(snapshot['git']['branch'])}</dd><dt>Upstream</dt><dd>{_escape(snapshot['git']['upstream'])}</dd><dt>远端同步</dt><dd>{_escape(snapshot['git']['remoteSync'])} · ahead {snapshot['git']['ahead']} / behind {snapshot['git']['behind']}</dd><dt>工作树污染</dt><dd>{_escape(snapshot['git']['dirty'])}</dd><dt>HEAD</dt><dd><code>{_escape(snapshot['git']['head'])}</code></dd></dl></section>
        <section class="panel"><h2>状态派生</h2><dl class="facts"><dt>校验结论</dt><dd>{_badge(snapshot['validationStatus'])}</dd><dt>声明状态</dt><dd>{_escape(declared['phase'])} / {_escape(declared['health'])} / {_escape(declared['claimLevel'])}</dd><dt>派生状态</dt><dd>{_escape(state['phase'])} / {_escape(state['health'])} / {_escape(state['claimLevel'])}</dd><dt>漂移</dt><dd>{_escape(drift['detected'])} · {_escape(', '.join(drift['fields']))}</dd></dl></section>
        <section class="panel"><h2>审核发现</h2>{finding_markup}</section>
        <section class="panel"><h2>停止与阻断</h2><dl class="facts"><dt>停止原因</dt><dd>{_escape(snapshot['stopReason'])}</dd></dl><div style="margin-top:12px">{_list(snapshot['blockers'], empty='当前快照未观察到阻断项')}</div></section>
      </aside>
    </div>
    <footer>生成时间：{_escape(snapshot['generatedAt'])} · 来源：{_escape(snapshot['source'])} · 快照 SHA-256：<code>{_escape(snapshot['snapshotSha256'])}</code></footer>
  </main>
  <script>
    (() => {{
      const root = document.documentElement;
      const button = document.getElementById('theme-toggle');
      button.addEventListener('click', () => {{
        const dark = root.getAttribute('data-theme') === 'dark' || (!root.hasAttribute('data-theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);
        root.setAttribute('data-theme', dark ? 'light' : 'dark');
      }});
    }})();
  </script>
</body>
</html>"""


def _render_summary(snapshot: dict[str, Any]) -> str:
    state = snapshot["state"]
    declared = snapshot["declaredState"]
    human = snapshot["humanDecision"]
    evidence = snapshot["evidence"]
    lines = [
        "# vibe-control 人工复核摘要",
        "",
        "> 本文件由控制对象与 Git 实况派生，不构成证据、人工批准或正式发行资格。",
        "",
        f"- 项目：`{_markdown(snapshot['project']['id'])}`",
        f"- 派生阶段／健康／声明：`{_markdown(state['phase'])} / {_markdown(state['health'])} / {_markdown(state['claimLevel'])}`",
        f"- 声明阶段／健康／声明：`{_markdown(declared['phase'])} / {_markdown(declared['health'])} / {_markdown(declared['claimLevel'])}`",
        f"- 状态漂移：`{_markdown(snapshot['stateDrift']['detected'])}`；字段：`{_markdown(', '.join(snapshot['stateDrift']['fields']) or 'NONE')}`",
        f"- 自动化模式：`{_markdown(snapshot['automation']['mode'])}`",
        f"- 停止原因：`{_markdown(snapshot['stopReason'] or 'NONE')}`",
        f"- 快照 SHA-256：`{_markdown(snapshot['snapshotSha256'])}`",
        f"- 证据计数：executed={evidence['counters']['executed']}, passed={evidence['counters']['passed']}, failed={evidence['counters']['failed']}, skipped={evidence['counters']['skipped']}",
        "",
        "## 唯一待人工决定",
        "",
        (
            f"- `{_markdown(human['id'])}`：{_markdown(human['prompt'])}"
            if human["required"]
            else "- 当前无需人工决定；请按阻断项或自动化状态继续。"
        ),
        "",
        "## 已经证明",
        "",
        *([f"- {_markdown(value)}" for value in snapshot["proven"]] or ["- 尚无已闭合检查点。"]),
        "",
        "## 尚未证明",
        "",
        *[f"- {_markdown(value)}" for value in snapshot["notProven"]],
        "",
        "## 阻断项",
        "",
        *([f"- `{_markdown(value)}`" for value in snapshot["blockers"]] or ["- 当前快照未观察到阻断项。"]),
        "",
        "## 审核发现",
        "",
        *(
            [
                f"- `{_markdown(item.get('id'))}` · {_markdown(item.get('severity'))} · {_markdown(item.get('classification'))} · {_markdown(item.get('status'))}"
                for item in snapshot["review"]["findings"]
            ]
            or ["- 当前审核没有登记 finding。"]
        ),
    ]
    return "\n".join(lines)


def generate_dashboard(project: Path, output_dir: Path | None) -> dict[str, Any]:
    """Generate a non-authoritative, offline human-review projection.

    Validation runs through the controller's explicit read-only projection. The mutable control
    plane is fingerprinted before and after projection so accidental writes fail closed.
    """

    root = git_root(project.resolve())
    control_fingerprint_before = _control_fingerprint(root / ".vibe-control")
    snapshot = _build_snapshot(root)
    control_fingerprint_after = _control_fingerprint(root / ".vibe-control")
    if control_fingerprint_after != control_fingerprint_before:
        raise ControlError(
            "HC-DASHBOARD-READONLY-DRIFT",
            "dashboard projection changed project control files",
            status="FAIL",
            details={
                "before": control_fingerprint_before,
                "after": control_fingerprint_after,
            },
        )
    if output_dir is None:
        destination = _default_output(root, snapshot["declaredState"])
    else:
        destination = output_dir.expanduser().resolve()
        if _inside(destination, root):
            raise ControlError(
                "HC-DASHBOARD-OUTPUT-SCOPE",
                "dashboard output must remain outside the project Git root",
                status="BLOCKED",
                details={"project": str(root), "output": str(destination)},
            )

    destination.mkdir(parents=True, exist_ok=True)
    status_path = destination / "status.json"
    html_path = destination / "index.html"
    summary_path = destination / "summary.md"
    write_json_atomic(status_path, snapshot)
    _atomic_text(html_path, _render_html(snapshot))
    _atomic_text(summary_path, _render_summary(snapshot))
    status_sha = sha256_file(status_path)
    return envelope(
        status="PASS",
        checks=[
            check(
                "HC-DASHBOARD-SNAPSHOT",
                "PASS",
                "offline dashboard files bind one non-authoritative snapshot without mutating project control state",
                controlFingerprint=control_fingerprint_after,
            ),
            check(
                "HC-DASHBOARD-OUTPUT-SCOPE",
                "PASS",
                "dashboard output is outside the project Git root",
            ),
        ],
        formal={
            "eligible": False,
            "maxClaimLevel": "DIAGNOSTIC",
            "blockers": ["HC-DASHBOARD-NON-AUTHORITATIVE"],
        },
        state={
            "declared": snapshot["declaredState"],
            "derived": snapshot["derivedState"],
        },
        data={
            "files": {
                "html": str(html_path),
                "status": str(status_path),
                "summary": str(summary_path),
            },
            "statusSha256": status_sha,
            "snapshotSha256": snapshot["snapshotSha256"],
            "source": SOURCE,
        },
    )
