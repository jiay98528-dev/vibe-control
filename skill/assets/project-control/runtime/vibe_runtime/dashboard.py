from __future__ import annotations

import html
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from .common import (
    canonical_bytes,
    ControlError,
    check,
    clean_status,
    envelope,
    git,
    now_iso,
    sha256_file,
    sha256_bytes,
    write_json_atomic,
)
from .checkpoint_control import derive_checkpoint_result
from .controller import validate as controller_validate
from .progress import (
    DOMAIN_WEIGHTS,
    _PLAIN_FORBIDDEN,
    _exclusive_lock,
    destination_lock_path,
    load_progress,
    project_identity,
    project_lock_path,
    progress_path,
)


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


def _project_root(project: Path) -> tuple[Path, bool]:
    requested = project.expanduser().resolve()
    value = git(requested, "rev-parse", "--show-toplevel", required=False)
    return (Path(value).resolve(), True) if value else (requested, False)


def _uninitialized_validation() -> dict[str, Any]:
    state = {
        "phase": "DRAFT",
        "health": "BLOCKED",
        "claimLevel": "DIAGNOSTIC",
        "taskId": None,
        "candidateId": None,
    }
    return {
        "status": "BLOCKED",
        "formal": {
            "eligible": False,
            "maxClaimLevel": "DIAGNOSTIC",
            "blockers": ["HC-CONTROL-PLANE-NOT-INITIALIZED"],
        },
        "state": {"declared": state, "derived": state},
        "data": {"eligibleEvidenceIds": []},
    }


def _default_output(root: Path, state: dict[str, Any]) -> Path:
    candidate = progress_path(root, state.get("taskId")).parent.resolve()
    if _inside(candidate, root):
        candidate = (
            root.parent
            / ".vibe-control-workspaces"
            / project_identity(root)["projectInstanceId"]
            / _safe_component(state.get("taskId"), "no-active-task")
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


def _git_status(root: Path, *, available: bool = True) -> dict[str, Any]:
    if not available:
        return {
            "available": False,
            "head": None,
            "branch": None,
            "upstream": None,
            "remoteSync": "NOT_APPLICABLE",
            "ahead": 0,
            "behind": 0,
            "latestCommit": None,
            "dirty": None,
            "dirtyEntryCount": None,
        }
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
        "available": True,
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


def _git_output_bytes(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        check=False,
    )
    if result.returncode != 0:
        raise ControlError(
            "HC-DASHBOARD-GIT-FINGERPRINT",
            "could not read the Git worktree while building the dashboard",
            status="BLOCKED",
        )
    return result.stdout


def _nul_paths(value: bytes) -> list[str]:
    return [
        item.decode("utf-8", errors="surrogateescape")
        for item in value.split(b"\0")
        if item
    ]


def _git_worktree_fingerprint(root: Path) -> dict[str, Any]:
    """Bind Git identity plus the bytes of every currently changed path."""

    head = _git_output_bytes(root, "rev-parse", "HEAD").strip()
    status = _git_output_bytes(
        root, "status", "--porcelain=v2", "-z", "--untracked-files=all"
    )
    index = _git_output_bytes(root, "ls-files", "--stage", "-z")
    path_values: set[str] = set()
    for args in (
        ("diff", "--name-only", "-z"),
        ("diff", "--cached", "--name-only", "-z"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    ):
        path_values.update(_nul_paths(_git_output_bytes(root, *args)))
    inventory: list[dict[str, Any]] = []
    resolved_root = root.resolve()
    for raw in sorted(path_values):
        logical = PurePosixPath(raw)
        if logical.is_absolute() or ".." in logical.parts:
            raise ControlError(
                "HC-DASHBOARD-GIT-FINGERPRINT",
                "Git reported an unsafe changed path while building the dashboard",
                status="FAIL",
            )
        path = resolved_root.joinpath(*logical.parts)
        try:
            path.absolute().relative_to(resolved_root)
        except ValueError as exc:
            raise ControlError(
                "HC-DASHBOARD-GIT-FINGERPRINT",
                "a changed path escaped the project root",
                status="FAIL",
            ) from exc
        if path.is_symlink():
            payload = os.fsencode(os.readlink(path))
            inventory.append(
                {
                    "path": raw,
                    "kind": "symlink",
                    "bytes": len(payload),
                    "sha256": sha256_bytes(payload),
                }
            )
        elif path.is_file():
            inventory.append(
                {
                    "path": raw,
                    "kind": "file",
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
        elif path.is_dir():
            inventory.append(
                {"path": raw, "kind": "directory", "bytes": 0, "sha256": None}
            )
        else:
            inventory.append(
                {"path": raw, "kind": "missing", "bytes": 0, "sha256": None}
            )
    return {
        "headSha256": sha256_bytes(head),
        "indexSha256": sha256_bytes(index),
        "statusSha256": sha256_bytes(status),
        "changedPathsSha256": sha256_bytes(canonical_bytes(inventory)),
        "changedPathCount": len(inventory),
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
    review_roles = ((contract or {}).get("auditPolicy") or {}).get("requiredReviewRoles", [])
    review_required = isinstance(review_roles, list) and bool(review_roles)
    review_closed = not review_required or (
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


def _scorecard_projection(
    contract: dict[str, Any] | None,
    checkpoints: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    ledger: dict[str, Any] | None,
    *,
    eligible_evidence: dict[str, dict[str, Any]] | None = None,
    review: dict[str, Any] | None = None,
    candidate: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_plan: Any = (contract or {}).get("scorecardPlan")
    if raw_plan is None and isinstance(ledger, dict):
        raw_plan = ledger.get("scorecardPlan")
    items = raw_plan.get("items", []) if isinstance(raw_plan, dict) else raw_plan
    if not isinstance(items, list):
        items = []
    checkpoint_status = {
        str(item.get("id")): str(item.get("status")) for item in checkpoints
    }
    case_status = {str(item.get("caseId")): str(item.get("status")) for item in cases}
    evidence_by_id = {
        str(item.get("evidenceId")): item
        for item in (eligible_evidence or {}).values()
        if isinstance(item, dict) and isinstance(item.get("evidenceId"), str)
    }
    validation_checks = (
        validation.get("integrity", {}).get("checks", [])
        if isinstance(validation, dict)
        else []
    )

    def source_passes(source: dict[str, Any]) -> bool:
        kind = source.get("kind")
        refs = source.get("refs")
        if not isinstance(refs, list) or not refs:
            return False
        values = [str(value) for value in refs]
        if kind == "CHECKPOINT":
            return all(checkpoint_status.get(value) == "PASS" for value in values)
        if kind == "CASE":
            return all(case_status.get(value) == "PASS" for value in values)
        if kind == "EVIDENCE":
            return all(value in evidence_by_id for value in values)
        if kind == "REVIEW":
            if not isinstance(review, dict) or review.get("result") != "PASS":
                return False
            review_checks = [
                item
                for item in validation_checks
                if isinstance(item, dict)
                and (
                    item.get("id") in {
                        "HC-REVIEW-TRACKED",
                        "HC-REVIEW-RESULT",
                        "HC-PROJECT-REVIEW-GATE",
                        "HC-REVIEW-ATTESTATION",
                        "HC-AUDITOR-SIGNATURE",
                    }
                    or str(item.get("id") or "").startswith("HC-REVIEW-EVIDENCE-REF-")
                    or str(item.get("id") or "").startswith("HC-REVIEW-TRANSCRIPT-")
                )
            ]
            required_checks = {
                "HC-REVIEW-TRACKED",
                "HC-REVIEW-RESULT",
                "HC-PROJECT-REVIEW-GATE",
            }
            present_checks = {str(item.get("id") or "") for item in review_checks}
            has_attestation = bool(
                {"HC-REVIEW-ATTESTATION", "HC-AUDITOR-SIGNATURE"}
                & present_checks
            )
            has_transcript = any(
                value.startswith("HC-REVIEW-TRANSCRIPT-")
                for value in present_checks
            )
            has_evidence_refs = any(
                value.startswith("HC-REVIEW-EVIDENCE-REF-")
                for value in present_checks
            )
            derived_phase = str(
                (validation or {}).get("state", {}).get("derived", {}).get("phase")
                or ""
            )
            review_qualified = bool(
                required_checks <= present_checks
                and has_attestation
                and has_transcript
                and has_evidence_refs
                and review_checks
                and all(item.get("status") == "PASS" for item in review_checks)
                and derived_phase in {"AUDITED", "ACCEPTED", "RELEASE_READY"}
            )
            if not review_qualified:
                return False
            review_id = str(review.get("reviewId") or "")
            return all(
                value == "FRESH-INDEPENDENT-REVIEW" or value == review_id
                for value in values
            )
        if kind == "CORE_CONTROL":
            candidate_checks = [
                item
                for item in validation_checks
                if isinstance(item, dict) and "CANDIDATE" in str(item.get("id") or "")
            ]
            counter_checks = [
                item
                for item in validation_checks
                if isinstance(item, dict) and item.get("id") == "HC-CASE-COUNTERS"
            ]
            core_facts = {
                "RULE-CORE-OBSERVABLE-CANDIDATE": bool(
                    isinstance(candidate, dict)
                    and candidate.get("candidateId")
                    and candidate.get("commit")
                    and candidate.get("tree")
                    and candidate_checks
                    and all(item.get("status") == "PASS" for item in candidate_checks)
                ),
                "RULE-CORE-FAILURE-CONSERVATION": bool(
                    counter_checks
                    and all(item.get("status") == "PASS" for item in counter_checks)
                ),
            }
            return all(core_facts.get(value, False) for value in values)
        return False
    rows: dict[str, dict[str, Any]] = {
        key: {"category": key, "weight": weight, "passed": 0, "total": 0, "ratio": None, "items": []}
        for key, weight in DOMAIN_WEIGHTS.items()
    }
    for raw in items:
        if not isinstance(raw, dict):
            continue
        category = str(raw.get("category") or raw.get("domain") or "")
        if category not in rows:
            continue
        refs = raw.get("checkpointIds", [])
        if not isinstance(refs, list):
            refs = []
        refs = [str(value) for value in refs]
        fact_sources = raw.get("factSources", [])
        if not isinstance(fact_sources, list):
            fact_sources = []
        passed = bool(fact_sources) and all(
            isinstance(source, dict) and source_passes(source) for source in fact_sources
        )
        status = "PASS" if passed else ("PENDING" if fact_sources else "UNKNOWN")
        rows[category]["total"] += 1
        rows[category]["passed"] += int(passed)
        rows[category]["items"].append(
            {
                "id": raw.get("id"),
                "statement": raw.get("statement"),
                "checkpointIds": refs,
                "factSources": fact_sources,
                "status": status,
            }
        )
    baseline = isinstance(contract, dict) and all(row["total"] > 0 for row in rows.values())
    for row in rows.values():
        if baseline and row["total"]:
            row["ratio"] = round(row["passed"] * 100 / row["total"], 1)
    overall = None
    if baseline:
        overall = round(
            sum(float(row["ratio"] or 0) * row["weight"] for row in rows.values()) / 100,
            1,
        )
    required = [item for item in cases if item.get("required") is not False]
    proven = sum(1 for item in required if item.get("status") == "PASS")
    return {
        "baselineEstablished": baseline,
        "domains": list(rows.values()),
        "overall": overall,
        "overallLabel": "交付准备度，不是剩余工时预测",
        "evidenceCoverage": {
            "passed": proven,
            "total": len(required),
            "ratio": round(proven * 100 / len(required), 1) if required else None,
        },
    }


def _progress_projection(
    contract: dict[str, Any] | None,
    ledger: dict[str, Any] | None,
    ledger_issue: str | None = None,
) -> dict[str, Any]:
    nodes = ledger.get("nodes", []) if isinstance(ledger, dict) else []
    if not isinstance(nodes, list) or not nodes:
        nodes = []
        for milestone in (contract or {}).get("milestones", []):
            if not isinstance(milestone, dict):
                continue
            work_nodes = milestone.get("workNodes", [])
            if not isinstance(work_nodes, list) or not work_nodes:
                work_nodes = [{"id": milestone.get("id"), "title": milestone.get("outcome") or milestone.get("statement")}]
            for raw in work_nodes:
                if isinstance(raw, dict):
                    nodes.append(
                        {
                            "id": raw.get("id"),
                            "title": raw.get("title") or raw.get("statement"),
                            "status": "PENDING",
                            "summary": "",
                        }
                    )
    completed = sum(1 for item in nodes if isinstance(item, dict) and item.get("status") == "COMPLETED")
    return {
        "available": isinstance(ledger, dict),
        "recordLost": not isinstance(ledger, dict),
        "recordIssue": ledger_issue,
        "taskId": ((ledger.get("taskBinding") or {}).get("taskId") if isinstance(ledger, dict) and isinstance(ledger.get("taskBinding"), dict) else None),
        "revision": ledger.get("revision") if isinstance(ledger, dict) else None,
        "temporary": bool(ledger.get("temporary")) if isinstance(ledger, dict) else True,
        "nodes": nodes,
        "nodeCounts": {"completed": completed, "total": len(nodes)},
        "events": ledger.get("events", []) if isinstance(ledger, dict) else [],
    }


def _plain_language_projection(
    root: Path,
    contract: dict[str, Any] | None,
    ledger: dict[str, Any] | None,
    checkpoint_rows: list[dict[str, Any]],
    blockers: list[str],
    candidate: dict[str, Any] | None,
    validation: dict[str, Any],
) -> dict[str, str]:
    report = ledger.get("report") if isinstance(ledger, dict) else None
    plain = report.get("plainLanguage") if isinstance(report, dict) else None
    required = (
        "projectPurpose", "whatWasDone", "whatWorksNow", "whatStillDoesNotWork",
        "userImpact", "canContinue", "canRelease",
    )
    if (
        isinstance(plain, dict)
        and set(plain) == set(required)
        and all(isinstance(plain[key], str) and plain[key].strip() for key in required)
    ):
        result = {key: str(plain[key]) for key in required}
    else:
        purpose = (
            ledger.get("projectPurpose")
            if isinstance(ledger, dict)
            else None
        ) or (contract or {}).get("goal") or f"维护 {root.name} 项目"
        if _PLAIN_FORBIDDEN.search(str(purpose)):
            purpose = "帮助用户完成当前项目所面向的实际工作。"
        if checkpoint_rows:
            done = sum(1 for item in checkpoint_rows if item.get("status") == "PASS")
            total = len(checkpoint_rows)
        else:
            local_nodes = ledger.get("nodes", []) if isinstance(ledger, dict) else []
            done = sum(
                1
                for item in local_nodes
                if isinstance(item, dict) and item.get("status") == "COMPLETED"
            )
            total = len(local_nodes) if isinstance(local_nodes, list) else 0
        result = {
            "projectPurpose": str(purpose),
            "whatWasDone": f"已完成 {done} 项计划事项，共有 {total} 项。" if total else "已建立本地进度页面，尚未完成任务检查。",
            "whatWorksNow": "已经通过的项目可以按页面中的已完成清单使用。" if done else "目前还没有足够结果可以确认某项功能已经可用。",
            "whatStillDoesNotWork": "仍有需要处理或确认的事项，请查看未完成清单。" if blockers or done < total else "当前计划内没有已知的未完成事项。",
            "userImpact": "未完成的事项可能让功能表现与预期不一致，因此现在不应把它当作最终成品。" if blockers or done < total else "当前计划已完成，但是否交付仍需负责人最后确认。",
            "canContinue": "可以在原定范围内继续处理。" if not candidate else "可以进入负责人复核。",
            "canRelease": "现在不能作为最终版本交付。",
        }
    formal = validation.get("formal") if isinstance(validation, dict) else None
    report_state = validation.get("state") if isinstance(validation, dict) else None
    derived = report_state.get("derived") if isinstance(report_state, dict) else None
    release_ready = (
        isinstance(formal, dict)
        and formal.get("eligible") is True
        and isinstance(derived, dict)
        and derived.get("claimLevel") == "RELEASE_READY"
    )
    result["canRelease"] = (
        "当前结果已经达到最终交付前的要求，仍需负责人执行实际交付决定。"
        if release_ready
        else "现在没有足够依据把它作为最终版本交付。"
    )
    if validation.get("status") in {"FAIL", "INVALIDATED"}:
        result["canContinue"] = "需要先解决当前明确的问题，再继续推进。"
    return result


def _next_actions_projection(
    ledger: dict[str, Any] | None, stop_reason: str | None
) -> list[dict[str, Any]]:
    report = ledger.get("report") if isinstance(ledger, dict) else None
    actions = report.get("nextActions") if isinstance(report, dict) else None
    if isinstance(actions, list) and actions:
        return actions
    return [
        {
            "type": "RECOMMENDED",
            "statement": "继续处理当前计划中的下一项工作",
            "impact": "保持目标和范围不变，最直接地推进到负责人复核。",
            "risk": "低；不改变当前目标和权限。",
            "humanEffort": "到下一次复核点前无需持续盯守。",
            "sourceRefs": [stop_reason] if stop_reason else [],
        },
        {
            "type": "ALTERNATIVE",
            "statement": "先查看当前结果再决定是否继续",
            "impact": "会暂停自动推进，但便于先核对已完成内容。",
            "risk": "低；主要代价是进度暂停。",
            "humanEffort": "需要现在花时间查看页面与结果。",
            "sourceRefs": [stop_reason] if stop_reason else [],
        },
        {
            "type": "OPEN",
            "statement": "提出其他做法",
            "impact": "可以给出新的方向；若改变目标或范围，需要重新整理计划。",
            "risk": "取决于输入的新方向。",
            "humanEffort": "需要描述希望改变的内容。",
            "sourceRefs": [stop_reason or "CURRENT-GOAL"],
        },
    ]


def _build_snapshot(project: Path, progress_ledger: Path | None = None) -> dict[str, Any]:
    root, has_git = _project_root(project)
    control = root / ".vibe-control"
    issues: list[str] = []
    state = _read_optional_json(control / "stage-state.json", issues) or {
        "phase": "DRAFT",
        "health": "BLOCKED",
        "claimLevel": "DIAGNOSTIC",
        "taskId": None,
        "candidateId": None,
    }
    validation = (
        controller_validate(root, mutate_state=False)
        if has_git and control.is_dir()
        else _uninitialized_validation()
    )
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
    git_state = _git_status(root, available=has_git)
    ledger_issue = None
    try:
        ledger = load_progress(root, task_id, progress_ledger)
    except ControlError as exc:
        ledger = None
        ledger_issue = exc.check_id
        issues.append("HC-PROGRESS-LEDGER-CORRUPT")

    objective_text = _objective_statements(root, objective_lock)
    objective_refs = (
        [str(value) for value in contract.get("objectiveRefs", [])]
        if isinstance(contract, dict)
        else [str(value) for value in (objective_lock or {}).get("objectiveIds", [])]
    )
    objectives = [
        {"id": value, "statement": objective_text.get(value)} for value in objective_refs
    ]
    if not objectives and isinstance(ledger, dict) and ledger.get("currentGoal"):
        objectives = [
            {"id": "LOCAL-CURRENT-GOAL", "statement": ledger.get("currentGoal")}
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
    review_roles = ((contract or {}).get("auditPolicy") or {}).get("requiredReviewRoles", [])
    review_required = isinstance(review_roles, list) and bool(review_roles)
    review_closed = not review_required or (
        review is not None and review.get("result") == "PASS"
    )
    ledger_report = ledger.get("report") if isinstance(ledger, dict) else None
    ledger_events = ledger.get("events", []) if isinstance(ledger, dict) else []
    ledger_stop_reason = (
        ledger_report.get("reason")
        if isinstance(ledger_report, dict)
        else next(
            (
                item.get("stopReason")
                for item in reversed(ledger_events)
                if isinstance(item, dict) and item.get("stopReason")
            ),
            None,
        )
    )
    effective_blockers = [
        value
        for value in blockers
        if not (isinstance(ledger, dict) and value == "HC-CONTROL-PLANE-NOT-INITIALIZED")
    ]
    stop_reason = str(ledger_stop_reason) if ledger_stop_reason else None
    if not stop_reason and effective_blockers:
        stop_reason = "HARD_FAILURE_OR_BLOCKED_STATE"
    elif not stop_reason and candidate and automated_closed and review_required and not review_closed:
        stop_reason = "AUDIT_REQUIRED"
    elif not stop_reason and candidate and automated_closed and review_closed and any(
        item["type"] == "HUMAN" and item["status"] != "PASS"
        for item in checkpoint_rows
    ):
        stop_reason = "HUMAN_CHECKPOINT"
    elif not stop_reason and candidate and automated_closed and review_closed and decision is None:
        stop_reason = "AUTOMATED_CHECKPOINTS_COMPLETE"

    scorecard = _scorecard_projection(
        contract,
        checkpoint_rows,
        case_rows,
        ledger,
        eligible_evidence=eligible_evidence,
        review=review,
        candidate=candidate,
        validation=validation,
    )
    progress = _progress_projection(contract, ledger, ledger_issue)
    plain_language = _plain_language_projection(
        root, contract, ledger, checkpoint_rows, blockers, candidate, validation
    )
    next_actions = _next_actions_projection(ledger, stop_reason)

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
    not_proven.append("这个本地进度页面只负责展示，不代表已经完成最终交付或负责人确认。")

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
        "schemaVersion": "2.0",
        "source": SOURCE,
        "generatedAt": now_iso(),
        "phase": derived_state["phase"],
        "health": derived_state["health"],
        "claim": derived_state["claimLevel"],
        "validationStatus": validation.get("status", "BLOCKED"),
        "project": {
            "id": (
                (lock or {}).get("projectId")
                or state.get("projectId")
                or ((ledger or {}).get("project") or {}).get("projectId")
                or root.name
            ),
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
            "goal": (
                contract.get("goal")
                if isinstance(contract, dict)
                else (ledger or {}).get("currentGoal")
            ),
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
        "progress": progress,
        "scorecard": scorecard,
        "blockers": blockers,
        "stopReason": stop_reason,
        "humanDecision": human_decision,
        "nextActions": next_actions,
        "proven": list(dict.fromkeys(proven)),
        "notProven": list(dict.fromkeys(not_proven)),
    }
    # Bind the plain explanation into the snapshot identity while still keeping
    # it as the final field in the serialized user-facing report.
    snapshot["plainLanguage"] = plain_language
    snapshot_sha256 = sha256_bytes(canonical_bytes(snapshot))
    snapshot.pop("plainLanguage")
    snapshot["snapshotSha256"] = snapshot_sha256
    snapshot["plainLanguage"] = plain_language
    return snapshot


def _escape(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        value = "是" if value else "否"
    return html.escape(str(value), quote=True)


def _markdown(value: Any) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = text.replace("\\", "\\\\")
    for token in ("`", "*", "_", "[", "]", "(", ")", "!", "#", "|"):
        text = text.replace(token, f"\\{token}")
    return html.escape(text, quote=False)


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
    scorecard = snapshot["scorecard"]
    progress = snapshot["progress"]
    plain = snapshot["plainLanguage"]
    domain_labels = {
        "FUNCTIONALITY": "功能完成度",
        "ROBUSTNESS_SECURITY": "健壮性与安全性",
        "AUDIT": "审计完整度",
        "PROCESS": "流程规范度",
    }
    node_status_labels = {
        "PENDING": "待开始", "ACTIVE": "进行中", "COMPLETED": "已完成",
        "BLOCKED": "等待条件", "FAILED": "未成功", "SUPERSEDED": "已被新计划替代",
    }
    action_labels = {"RECOMMENDED": "推荐", "ALTERNATIVE": "备选", "OPEN": "自由输入"}
    def render_score_row(row: dict[str, Any]) -> str:
        label = domain_labels.get(str(row["category"]), str(row["category"]))
        fraction = f"{row['passed']}/{row['total']}" if row["total"] else "尚未建立"
        detail = (
            f"{row['ratio']:.1f}% · 权重 {row['weight']}%"
            if row["ratio"] is not None
            else f"权重 {row['weight']}% · 尚无可评分基线"
        )
        if row["ratio"] is None:
            bar = (
                f'<div class="bar" role="status" aria-label="{_escape(label)}：尚未建立计分基线">'
                '<span style="width:0"></span></div>'
            )
        else:
            ratio = row["ratio"]
            bar = (
                f'<div class="bar" role="progressbar" aria-label="{_escape(label)}" '
                f'aria-valuemin="0" aria-valuemax="100" aria-valuenow="{_escape(ratio)}">'
                f'<span style="width:{_escape(ratio)}%"></span></div>'
            )
        return (
            '<div class="score-row">'
            f'<div class="score-head"><strong>{_escape(label)}</strong><span>{_escape(fraction)}</span></div>'
            f'{bar}'
            f'<small>{_escape(detail)}</small></div>'
        )

    score_markup = "".join(render_score_row(row) for row in scorecard["domains"])
    overall_value = scorecard["overall"]
    overall_markup = (
        f'<div class="overall"><svg viewBox="0 0 120 120" role="img" aria-label="交付准备度 {overall_value:.1f}%"><circle cx="60" cy="60" r="46" class="ring-bg"/><circle cx="60" cy="60" r="46" class="ring-value" pathLength="100" stroke-dasharray="{overall_value} 100"/></svg><strong>{overall_value:.1f}%</strong></div>'
        if overall_value is not None
        else '<div class="overall empty-overall">尚未建立<br>计分基线</div>'
    )
    timeline_markup = "".join(
        '<li>'
        f'<span class="timeline-dot {_escape(str(item.get("status") or "PENDING").lower())}"></span>'
        f'<div><strong>{_escape(item.get("title") or item.get("id"))}</strong><small>{_escape(node_status_labels.get(str(item.get("status") or "PENDING"), "状态未知"))} · {_escape(item.get("summary") or "尚无节点说明")}</small></div>'
        '</li>'
        for item in progress["nodes"]
        if isinstance(item, dict)
    ) or '<li class="empty">尚未建立行动节点。</li>'
    action_markup = "".join(
        '<article class="action">'
        f'<span class="eyebrow">{_escape(action_labels.get(str(item.get("type")), "建议"))}</span>'
        f'<h3>{_escape(item.get("statement"))}</h3><p>{_escape(item.get("impact"))}</p><p><strong>风险：</strong>{_escape(item.get("risk"))}<br><strong>你要投入：</strong>{_escape(item.get("humanEffort"))}</p>'
        '</article>'
        for item in snapshot["nextActions"]
        if isinstance(item, dict)
    )
    objective_markup = _list(
        [
            f"{item.get('statement') or '目标文字尚未载入'}"
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
        f'<h2>需要你的判断</h2><p>{_escape(human["prompt"])}</p></div>'
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
    p, li, dd {{ text-wrap: pretty; }} p {{ margin: 0; }}
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
    .score-layout {{ display: grid; grid-template-columns: minmax(0,1fr) 180px; gap: 24px; align-items: center; }}
    .score-row + .score-row {{ margin-top: 15px; }} .score-head {{ display:flex; justify-content:space-between; gap:12px; }}
    .score-row small {{ color: var(--muted); }} .bar {{ height: 9px; background: var(--surface-2); border: 1px solid var(--line); margin: 6px 0; overflow:hidden; }}
    .bar span {{ display:block; height:100%; background: var(--info); }}
    .overall {{ position:relative; width:160px; height:160px; display:grid; place-items:center; margin:auto; text-align:center; }}
    .overall svg {{ position:absolute; inset:0; transform:rotate(-90deg); }} .overall circle {{ fill:none; stroke-width:12; }}
    .ring-bg {{ stroke:var(--surface-2); }} .ring-value {{ stroke:var(--good); stroke-linecap:round; }}
    .overall strong {{ font-size:1.7rem; }} .empty-overall {{ border:1px dashed var(--line); border-radius:50%; color:var(--muted); }}
    .timeline {{ list-style:none; padding:0; margin:0; }} .timeline li {{ display:grid; grid-template-columns:18px 1fr; gap:10px; padding:7px 0; }}
    .timeline-dot {{ width:11px; height:11px; border-radius:50%; margin-top:5px; background:var(--line); }}
    .timeline-dot.completed {{ background:var(--good); }} .timeline-dot.active {{ background:var(--info); }} .timeline-dot.blocked, .timeline-dot.failed {{ background:var(--bad); }}
    .timeline small {{ display:block; color:var(--muted); margin-top:2px; }}
    .orientation {{ border-left:4px solid var(--info); }} .orientation-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }}
    .orientation-grid article {{ background:var(--surface-2); padding:13px; border-radius:7px; }} .orientation-grid p {{ margin-top:5px; }}
    .actions {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; }} .action {{ border:1px solid var(--line); padding:14px; border-radius:8px; }} .action p {{ color:var(--muted); }}
    details summary {{ cursor:pointer; font-weight:700; }} details[open] summary {{ margin-bottom:12px; }}
    footer {{ color: var(--muted); font-size: .78rem; margin-top: 18px; border-top: 1px solid var(--line); padding-top: 14px; }}
    @media (max-width: 900px) {{ .status-grid {{ grid-template-columns: repeat(2,1fr); }} .layout {{ grid-template-columns: 1fr; }} .actions {{ grid-template-columns:1fr; }} }}
    @media (max-width: 600px) {{ .shell {{ padding: 18px 14px; }} header {{ display: grid; }} .status-grid, .split, .score-layout, .orientation-grid {{ grid-template-columns: 1fr; }} .toolbar {{ justify-content: flex-start; }} }}
    @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ scroll-behavior: auto !important; transition-duration: .01ms !important; animation-duration: .01ms !important; animation-iteration-count: 1 !important; }} }}
  </style>
</head>
<body>
  <main class="shell" id="main-content">
    <header>
      <div><span class="eyebrow">本地项目操作台</span><h1>{_escape(snapshot['project']['id'])}</h1><p class="lede">{_escape(plain['projectPurpose'])}</p></div>
      <div class="toolbar"><button class="theme" id="theme-toggle" type="button" aria-label="切换深浅主题">切换主题</button></div>
    </header>
    <div class="notice" role="note">这是本机上的进度说明页，不会改变项目，也不能代替最终验收。</div>
    <section class="panel orientation" aria-labelledby="orientation-title">
      <span class="eyebrow">不用了解开发细节也能看懂</span><h2 id="orientation-title">现在发生了什么</h2>
      <div class="orientation-grid">
        <article><strong>正在解决</strong><p>{_escape(snapshot['objectives']['goal'] or (progress['nodes'][0].get('title') if progress['nodes'] else '正在建立任务计划'))}</p></article>
        <article><strong>已经完成</strong><p>{_escape(plain['whatWasDone'])}</p></article>
        <article><strong>现在可用</strong><p>{_escape(plain['whatWorksNow'])}</p></article>
        <article><strong>还不能做</strong><p>{_escape(plain['whatStillDoesNotWork'])}</p></article>
        <article><strong>不处理的后果</strong><p>{_escape(plain['userImpact'])}</p></article>
        <article><strong>能否继续／交付</strong><p>{_escape(plain['canContinue'])} {_escape(plain['canRelease'])}</p></article>
      </div>
    </section>
    <section class="status-grid" aria-label="进度概览">
      <div class="metric"><span class="eyebrow">交付准备度</span><strong>{_escape(f'{overall_value:.1f}%' if overall_value is not None else '尚未建立基线')}</strong></div>
      <div class="metric"><span class="eyebrow">行动节点</span><strong>{progress['nodeCounts']['completed']} / {progress['nodeCounts']['total']}</strong></div>
      <div class="metric"><span class="eyebrow">功能检查</span><strong>{sum(1 for item in checkpoints if item['status'] == 'PASS')} / {len(checkpoints)}</strong></div>
      <div class="metric"><span class="eyebrow">可核对结果</span><strong>{scorecard['evidenceCoverage']['passed']} / {scorecard['evidenceCoverage']['total']}</strong></div>
    </section>
    <section class="panel"><span class="eyebrow">标准量化</span><h2>四个方面分别完成到哪里</h2><div class="score-layout"><div>{score_markup}</div>{overall_markup}</div><p class="muted-text" style="margin-top:12px">{_escape(scorecard['overallLabel'])}。未知或尚未证明的项目按未完成计算；没有基线时不显示百分比。</p></section>
    <div class="layout">
      <div class="stack">
        {decision_markup}
        <section class="panel"><h2>行动地图</h2><p class="muted-text">本机记录修订：{_escape(progress['revision'])} · 完成 {progress['nodeCounts']['completed']} / {progress['nodeCounts']['total']}</p><ol class="timeline">{timeline_markup}</ol></section>
        <details class="panel"><summary>查看验收与核对的技术详情</summary><h2>验收检查点</h2><div class="table-wrap"><table><thead><tr><th>ID</th><th>类型</th><th>可观察事实</th><th>结果</th><th>Case</th></tr></thead><tbody>{checkpoint_markup}</tbody></table></div><h2 style="margin-top:18px">Case 与证据</h2><div class="table-wrap"><table><thead><tr><th>Case</th><th>结果</th><th>证据记录</th></tr></thead><tbody>{case_markup}</tbody></table></div><p class="muted-text" style="margin-top:12px">执行 {evidence['counters']['executed']} · 通过 {evidence['counters']['passed']} · 失败 {evidence['counters']['failed']} · Skip {evidence['counters']['skipped']} · Timeout {evidence['counters']['timedOut']}</p></details>
        <section class="panel"><h2>证明边界</h2><div class="split"><div><h3>已经证明</h3>{_list(snapshot['proven'], empty='尚无已闭合检查点')}</div><div><h3>尚未证明</h3>{_list(snapshot['notProven'])}</div></div></section>
      </div>
      <aside class="stack" aria-label="候选与操作状态">
        <section class="panel"><h2>关键目标</h2>{objective_markup}</section>
        <details class="panel"><summary>技术详情</summary><h2>候选内容</h2><dl class="facts"><dt>ID</dt><dd><code>{_escape(candidate.get('candidateId'))}</code></dd><dt>提交</dt><dd><code>{_escape(candidate.get('commit'))}</code></dd><dt>目录树</dt><dd><code>{_escape(candidate.get('tree'))}</code></dd><dt>变更文件</dt><dd>{len(changed_paths)}</dd></dl>{_list(changed_paths, empty='没有冻结候选或没有产品路径变更')}<h2>版本库实况</h2><dl class="facts"><dt>分支</dt><dd>{_escape(snapshot['git']['branch'])}</dd><dt>上游</dt><dd>{_escape(snapshot['git']['upstream'])}</dd><dt>远端同步</dt><dd>{_escape(snapshot['git']['remoteSync'])} · ahead {snapshot['git']['ahead']} / behind {snapshot['git']['behind']}</dd><dt>工作区变化</dt><dd>{_escape(snapshot['git']['dirty'])}</dd><dt>当前版本</dt><dd><code>{_escape(snapshot['git']['head'])}</code></dd></dl><h2>内部状态</h2><dl class="facts"><dt>校验结论</dt><dd>{_badge(snapshot['validationStatus'])}</dd><dt>记录状态</dt><dd>{_escape(declared['phase'])} / {_escape(declared['health'])} / {_escape(declared['claimLevel'])}</dd><dt>重新计算</dt><dd>{_escape(state['phase'])} / {_escape(state['health'])} / {_escape(state['claimLevel'])}</dd><dt>不一致</dt><dd>{_escape(drift['detected'])} · {_escape(', '.join(drift['fields']))}</dd><dt>投影来源</dt><dd>{_escape(snapshot['source'])}</dd><dt>快照校验值</dt><dd><code>{_escape(snapshot['snapshotSha256'])}</code></dd></dl></details>
        <details class="panel"><summary>查看审核发现的技术详情</summary>{finding_markup}</details>
        <section class="panel"><h2>当前为何停下</h2><p>{_escape(plain['whatStillDoesNotWork'])}</p><details style="margin-top:12px"><summary>查看内部原因与编号</summary><dl class="facts"><dt>停止原因</dt><dd>{_escape(snapshot['stopReason'])}</dd></dl><div style="margin-top:12px">{_list(snapshot['blockers'], empty='当前快照未观察到阻断项')}</div></details></section>
      </aside>
    </div>
    <section class="panel"><span class="eyebrow">下一步建议</span><h2>你可以怎样继续</h2><div class="actions">{action_markup}</div></section>
    <footer>生成时间：{_escape(snapshot['generatedAt'])} · 本页面只保存在本机，不会改变项目。</footer>
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
    scorecard = snapshot["scorecard"]
    plain = snapshot["plainLanguage"]
    domain_lines = []
    for row in scorecard["domains"]:
        ratio_text = f"{row['ratio']:.1f}%" if row["ratio"] is not None else "尚未建立基线"
        domain_lines.append(
            f"- {row['category']}：{row['passed']} / {row['total']}（{ratio_text}），权重 {row['weight']}%"
        )
    overall_text = (
        f"{scorecard['overall']:.1f}%"
        if scorecard["overall"] is not None
        else "尚未建立基线"
    )
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
        f"- 本机行动节点：{snapshot['progress']['nodeCounts']['completed']} / {snapshot['progress']['nodeCounts']['total']}；记录修订：{_markdown(snapshot['progress']['revision'])}",
        "",
        "## 四维交付准备度",
        "",
        *domain_lines,
        f"- 综合：{overall_text}。这是交付准备度，不是剩余工时预测。",
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
        "",
        "## 下一步选择",
        "",
        *[
            f"- **{_markdown(item.get('type'))}**：{_markdown(item.get('statement'))}。{_markdown(item.get('impact'))} 风险：{_markdown(item.get('risk'))}；人工投入：{_markdown(item.get('humanEffort'))}"
            for item in snapshot["nextActions"]
            if isinstance(item, dict)
        ],
        "",
        "## 给没有开发背景的人看的说明",
        "",
        f"- 这个项目是做什么的：{_markdown(plain['projectPurpose'])}",
        f"- 这次做了什么：{_markdown(plain['whatWasDone'])}",
        f"- 现在能用什么：{_markdown(plain['whatWorksNow'])}",
        f"- 还有什么不能用：{_markdown(plain['whatStillDoesNotWork'])}",
        f"- 对你的影响：{_markdown(plain['userImpact'])}",
        f"- 能否继续：{_markdown(plain['canContinue'])}",
        f"- 能否交付：{_markdown(plain['canRelease'])}",
    ]
    return "\n".join(lines)


def _generate_dashboard_locked(
    project: Path,
    output_dir: Path | None,
    *,
    progress_ledger: Path | None = None,
) -> dict[str, Any]:
    """Generate a non-authoritative, offline human-review projection.

    Validation runs through the controller's explicit read-only projection. The mutable control
    plane is fingerprinted before and after projection so accidental writes fail closed.
    """

    root, has_git = _project_root(project)
    git_fingerprint_before = _git_worktree_fingerprint(root) if has_git else None
    control_fingerprint_before = _control_fingerprint(root / ".vibe-control")
    snapshot = _build_snapshot(root, progress_ledger)
    control_fingerprint_after = _control_fingerprint(root / ".vibe-control")
    git_fingerprint_after = _git_worktree_fingerprint(root) if has_git else None
    if (
        control_fingerprint_after != control_fingerprint_before
        or git_fingerprint_after != git_fingerprint_before
    ):
        raise ControlError(
            "HC-DASHBOARD-READONLY-DRIFT",
            "dashboard projection observed project or control state changing while it ran",
            status="FAIL",
            details={
                "controlBefore": control_fingerprint_before,
                "controlAfter": control_fingerprint_after,
                "gitBefore": git_fingerprint_before,
                "gitAfter": git_fingerprint_after,
            },
        )
    if output_dir is None:
        destination = _default_output(
            root,
            {
                **snapshot["declaredState"],
                "taskId": snapshot["progress"].get("taskId")
                or snapshot["declaredState"].get("taskId"),
            },
        )
    else:
        destination = output_dir.expanduser().resolve()
        if _inside(destination, root):
            raise ControlError(
                "HC-DASHBOARD-OUTPUT-SCOPE",
                "dashboard output must remain outside the project Git root",
                status="BLOCKED",
                details={"project": str(root), "output": str(destination)},
            )

    with _exclusive_lock(destination_lock_path(root, destination)):
        existing_status = destination / "status.json"
        managed_names = {"status.json", "index.html", "summary.md"}
        if destination.exists() and any((destination / name).exists() for name in managed_names):
            try:
                existing = json.loads(existing_status.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ControlError(
                    "HC-DASHBOARD-DESTINATION-OWNERSHIP",
                    "dashboard destination contains an unreadable prior snapshot",
                    status="BLOCKED",
                ) from exc
            existing_project = existing.get("project") if isinstance(existing, dict) else None
            existing_root = existing_project.get("root") if isinstance(existing_project, dict) else None
            if not isinstance(existing_root, str) or os.path.normcase(str(Path(existing_root).resolve())) != os.path.normcase(str(root.resolve())):
                raise ControlError(
                    "HC-DASHBOARD-DESTINATION-OWNERSHIP",
                    "dashboard destination is already bound to a different project",
                    status="BLOCKED",
                )
        destination.mkdir(parents=True, exist_ok=True)
        status_path = destination / "status.json"
        html_path = destination / "index.html"
        summary_path = destination / "summary.md"
        write_json_atomic(status_path, snapshot)
        _atomic_text(html_path, _render_html(snapshot))
        _atomic_text(summary_path, _render_summary(snapshot))
        status_value = json.loads(status_path.read_text(encoding="utf-8-sig"))
        html_value = html_path.read_text(encoding="utf-8")
        summary_value = summary_path.read_text(encoding="utf-8")
        snapshot_id = snapshot["snapshotSha256"]
        if (
            status_value.get("snapshotSha256") != snapshot_id
            or snapshot_id not in html_value
            or snapshot_id not in summary_value
        ):
            raise ControlError(
                "HC-DASHBOARD-SNAPSHOT-CLOSURE",
                "dashboard files do not describe the same snapshot",
                status="FAIL",
            )
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
        plain_language=snapshot["plainLanguage"],
    )


def generate_dashboard(
    project: Path,
    output_dir: Path | None,
    *,
    progress_ledger: Path | None = None,
    project_lock_held: bool = False,
) -> dict[str, Any]:
    """Serialize one same-source Dashboard snapshot per project."""

    if project_lock_held:
        return _generate_dashboard_locked(
            project, output_dir, progress_ledger=progress_ledger
        )
    with _exclusive_lock(project_lock_path(project.expanduser().resolve())):
        return _generate_dashboard_locked(
            project, output_dir, progress_ledger=progress_ledger
        )
