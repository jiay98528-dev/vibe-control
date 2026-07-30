from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .common import (
    ControlError,
    canonical_bytes,
    check,
    envelope,
    git,
    now_iso,
    sha256_bytes,
    write_json_atomic,
)
from .schema import validate_object


PROGRESS_SCHEMA_VERSION = "1.0"
SOURCE = "LOCAL_TEMPORARY_NON_AUTHORITATIVE"
NODE_STATES = {"PENDING", "ACTIVE", "COMPLETED", "BLOCKED", "FAILED", "SUPERSEDED"}
ACTION_TYPES = {"RECOMMENDED", "ALTERNATIVE", "OPEN"}
DOMAIN_WEIGHTS = {
    "FUNCTIONALITY": 40,
    "ROBUSTNESS_SECURITY": 25,
    "AUDIT": 20,
    "PROCESS": 15,
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PLAIN_FORBIDDEN = re.compile(
    r"\b(?:schema|claim|commit|tree|hash|case|check\s*id|pass|blocked|fail|diagnostic|git|evidence|transcript|artifact|audit|phase|health)\b|CTRL-|HC-|VC-|哈希|控制面|声明等级|候选提交|目录树|门禁|审计|运行时|工作树|执行器|证据链|架构",
    re.IGNORECASE,
)
_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")
_TRANSITIONS = {
    "PENDING": {"ACTIVE", "BLOCKED", "FAILED", "SUPERSEDED"},
    "ACTIVE": {"COMPLETED", "BLOCKED", "FAILED", "SUPERSEDED"},
    "BLOCKED": {"ACTIVE", "SUPERSEDED"},
    "FAILED": {"ACTIVE", "SUPERSEDED"},
    "COMPLETED": {"SUPERSEDED"},
    "SUPERSEDED": set(),
}


def _cache_root() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return (base / "vibe-control" / "workspaces").absolute()


def _is_linklike(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True


def _validated_cache(project: Path) -> Path:
    raw = _cache_root()
    cursor = Path(raw.anchor)
    for part in raw.parts[1:]:
        cursor /= part
        if cursor.exists() and _is_linklike(cursor):
            raise ControlError("HC-PROGRESS-CACHE-SAFETY", "the configured local cache contains a link or junction", status="BLOCKED")
    cache = raw.resolve()
    project_root = Path(project_identity(project)["root"]).resolve()
    try:
        cache.relative_to(project_root)
    except ValueError:
        pass
    else:
        raise ControlError(
            "HC-PROGRESS-CACHE-SCOPE",
            "the configured local cache resolves inside the project; no progress files were written",
            status="BLOCKED",
            details={"project": str(project_root), "cache": str(cache)},
        )
    return cache


def _cache_child(project: Path, *parts: str) -> Path:
    cache = _validated_cache(project)
    cursor = cache
    for part in parts:
        cursor /= part
        if cursor.exists() and _is_linklike(cursor):
            raise ControlError("HC-PROGRESS-CACHE-SAFETY", "a local progress path contains a link or junction", status="BLOCKED")
    candidate = cursor.resolve()
    try:
        candidate.relative_to(cache)
    except ValueError as exc:
        raise ControlError("HC-PROGRESS-CACHE-SCOPE", "local progress path escaped the validated cache") from exc
    return candidate


def _safe_component(value: Any, fallback: str) -> str:
    result = _SAFE_COMPONENT.sub("-", str(value or "")).strip("-._")
    return result[:72] or fallback


def project_identity(project: Path) -> dict[str, Any]:
    requested = project.expanduser().resolve()
    root_text = git(requested, "rev-parse", "--show-toplevel", required=False)
    git_root = Path(root_text).resolve() if root_text else None
    common_text = git(requested, "rev-parse", "--git-common-dir", required=False) if git_root else ""
    common_dir = None
    if common_text and git_root:
        candidate = Path(common_text)
        common_dir = (candidate if candidate.is_absolute() else git_root / candidate).resolve()
    canonical_root = str(git_root or requested)
    identity_input = {
        "root": os.path.normcase(canonical_root),
        "gitCommonDir": os.path.normcase(str(common_dir)) if common_dir else None,
    }
    digest = sha256_bytes(canonical_bytes(identity_input))
    label = _safe_component((git_root or requested).name, "project")
    return {
        "projectInstanceId": f"{label}-{digest[:12]}",
        "root": canonical_root,
        "gitRoot": str(git_root) if git_root else None,
        "gitCommonDir": str(common_dir) if common_dir else None,
        "identitySha256": digest,
    }


def project_progress_root(project: Path) -> Path:
    identity = project_identity(project)
    return _cache_child(project, identity["projectInstanceId"])


def project_lock_path(project: Path) -> Path:
    identity = project_identity(project)
    return _cache_child(project, ".locks", f"{identity['projectInstanceId']}.lock")


def destination_lock_path(project: Path, destination: Path) -> Path:
    normalized = os.path.normcase(str(destination.expanduser().resolve()))
    digest = sha256_bytes(normalized.encode("utf-8"))
    return _cache_child(project, ".output-locks", f"{digest}.lock")


def progress_path(project: Path, task_id: str | None) -> Path:
    if task_id is None:
        task_key = "no-active-task"
    else:
        task_digest = sha256_bytes(str(task_id).encode("utf-8"))[:10]
        task_key = f"{_safe_component(task_id, 'task')}-{task_digest}"
    identity = project_identity(project)
    return _cache_child(project, identity["projectInstanceId"], task_key, "progress-ledger.json")


def _load_json(path: Path, check_id: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ControlError(check_id, f"local progress record is missing: {path}", status="BLOCKED") from exc
    except json.JSONDecodeError as exc:
        raise ControlError(check_id, f"local progress record is malformed at line {exc.lineno}") from exc
    if not isinstance(value, dict):
        raise ControlError(check_id, "local progress record must be an object")
    return value


def _validate_ledger(project: Path, ledger: dict[str, Any], task_id: str | None) -> None:
    validate_object("task-progress", ledger)
    current_identity = project_identity(project)
    recorded_project = ledger.get("project")
    if (
        not isinstance(recorded_project, dict)
        or recorded_project.get("projectInstanceId") != current_identity["projectInstanceId"]
        or recorded_project.get("identitySha256") != current_identity["identitySha256"]
    ):
        raise ControlError("HC-PROGRESS-PROJECT-IDENTITY", "local progress record belongs to a different project", status="INVALIDATED")
    binding = ledger.get("taskBinding")
    recorded_task = binding.get("taskId") if isinstance(binding, dict) else None
    if task_id is not None and recorded_task != task_id:
        raise ControlError("HC-PROGRESS-TASK-IDENTITY", "local progress record belongs to a different task", status="INVALIDATED")
    for field in ("projectPurpose", "currentGoal"):
        value = ledger.get(field)
        if not isinstance(value, str) or not value.strip() or _PLAIN_FORBIDDEN.search(value):
            raise ControlError(
                "HC-PLAIN-LANGUAGE-JARGON",
                f"local progress orientation contains invalid or internal text: {field}",
                status="INVALIDATED",
            )
    packets: list[dict[str, Any]] = []
    if isinstance(ledger.get("report"), dict):
        packets.append(ledger["report"])
    for historical in ledger.get("reportHistory", []):
        if isinstance(historical, dict) and isinstance(historical.get("report"), dict):
            packets.append(historical["report"])
    for packet in packets:
        expected_report_sha = packet.get("reportSha256")
        bound_packet = dict(packet)
        bound_packet.pop("reportSha256", None)
        if (
            not isinstance(expected_report_sha, str)
            or expected_report_sha != sha256_bytes(canonical_bytes(bound_packet))
        ):
            raise ControlError(
                "HC-PROGRESS-REPORT-BINDING",
                "stored review report identity does not match its content",
                status="INVALIDATED",
            )
        _validate_plain_language(packet.get("plainLanguage"))
        actions = _validate_next_actions(packet.get("nextActions"))
        allowed_refs = _allowed_action_refs(ledger, str(packet.get("reason") or "OWNER_REVIEW"))
        unknown_refs = sorted(
            {ref for action in actions for ref in action["sourceRefs"] if ref not in allowed_refs}
        )
        if unknown_refs:
            raise ControlError(
                "HC-PROGRESS-NEXT-ACTION-CLOSURE",
                f"stored next actions reference unknown current facts: {unknown_refs}",
                status="INVALIDATED",
            )


def _load_ledger(project: Path, path: Path, task_id: str | None) -> dict[str, Any]:
    ledger = _load_json(path, "HC-PROGRESS-LEDGER")
    _validate_ledger(project, ledger, task_id)
    return ledger


def _read_spec(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.expanduser().resolve().read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ControlError("HC-PROGRESS-SPEC", "progress spec is missing", status="BLOCKED") from exc
    except json.JSONDecodeError as exc:
        raise ControlError("HC-PROGRESS-SPEC", f"progress spec is malformed at line {exc.lineno}") from exc
    if not isinstance(value, dict):
        raise ControlError("HC-PROGRESS-SPEC", "progress spec must be an object")
    return value


def _task_id_from_spec(spec: dict[str, Any]) -> str | None:
    value = spec.get("taskId")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ControlError("HC-PROGRESS-TASK", "taskId must be a non-empty string or null")
    return value.strip()


def _validate_next_actions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != 3:
        raise ControlError("HC-PROGRESS-NEXT-ACTIONS", "exactly one recommended, one alternative, and one open choice are required")
    result: list[dict[str, Any]] = []
    types: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            raise ControlError("HC-PROGRESS-NEXT-ACTIONS", "each next action must be an object")
        action_type = item.get("type")
        if action_type not in ACTION_TYPES:
            raise ControlError("HC-PROGRESS-NEXT-ACTIONS", f"unknown next action type: {action_type}")
        statement = item.get("statement")
        impact = item.get("impact")
        risk = item.get("risk")
        human_effort = item.get("humanEffort")
        if not all(isinstance(value, str) and value.strip() for value in (statement, impact, risk, human_effort)):
            raise ControlError("HC-PROGRESS-NEXT-ACTIONS", "next actions require plain statement, impact, risk, and humanEffort")
        if any(_PLAIN_FORBIDDEN.search(value) for value in (statement, impact, risk, human_effort)):
            raise ControlError("HC-PLAIN-LANGUAGE-JARGON", "next action text contains internal terms")
        refs = item.get("sourceRefs", [])
        if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
            raise ControlError("HC-PROGRESS-NEXT-ACTIONS", "each next action must bind a current goal, node, checkpoint, or blocker")
        result.append({"type": action_type, "statement": statement.strip(), "impact": impact.strip(), "risk": risk.strip(), "humanEffort": human_effort.strip(), "sourceRefs": refs})
        types.append(action_type)
    if any(types.count(name) != 1 for name in ACTION_TYPES):
        raise ControlError("HC-PROGRESS-NEXT-ACTIONS", "next actions require exactly one RECOMMENDED, one ALTERNATIVE, and one OPEN option")
    return result


def _validate_plain_language(value: Any) -> dict[str, str]:
    required = (
        "projectPurpose", "whatWasDone", "whatWorksNow", "whatStillDoesNotWork",
        "userImpact", "canContinue", "canRelease",
    )
    if not isinstance(value, dict):
        raise ControlError("HC-PLAIN-LANGUAGE-REPORT", "plainLanguage must be an object")
    result: dict[str, str] = {}
    for name in required:
        text = value.get(name)
        if not isinstance(text, str) or not text.strip():
            raise ControlError("HC-PLAIN-LANGUAGE-REPORT", f"plainLanguage.{name} is required")
        result[name] = text.strip()
    leaked = [name for name, text in result.items() if _PLAIN_FORBIDDEN.search(text)]
    if leaked:
        raise ControlError("HC-PLAIN-LANGUAGE-JARGON", f"plain-language fields contain internal terms: {leaked}")
    return result


def _allowed_action_refs(ledger: dict[str, Any], reason: str) -> set[str]:
    allowed_refs = {"CURRENT-GOAL", reason}
    for node in ledger.get("nodes", []):
        if not isinstance(node, dict):
            continue
        allowed_refs.add(str(node.get("id")))
        allowed_refs.update(str(value) for value in node.get("objectiveRefs", []))
        allowed_refs.update(str(value) for value in node.get("checkpointRefs", []))
    return allowed_refs


def _validate_nodes(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ControlError("HC-PROGRESS-NODES", "progress plan requires at least one node")
    seen: set[str] = set()
    nodes: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ControlError("HC-PROGRESS-NODES", "each progress node must be an object")
        node_id = raw.get("id")
        title = raw.get("title")
        if not isinstance(node_id, str) or not node_id or node_id in seen:
            raise ControlError("HC-PROGRESS-NODES", f"node id is missing or duplicated: {node_id}")
        if not isinstance(title, str) or not title.strip():
            raise ControlError("HC-PROGRESS-NODES", f"node title is required: {node_id}")
        state = raw.get("status", "PENDING")
        if state not in NODE_STATES:
            raise ControlError("HC-PROGRESS-NODES", f"invalid node status: {state}")
        dependencies = raw.get("dependsOn", [])
        if not isinstance(dependencies, list) or any(not isinstance(item, str) for item in dependencies):
            raise ControlError("HC-PROGRESS-NODES", f"dependsOn must be a string array: {node_id}")
        seen.add(node_id)
        nodes.append({
            "id": node_id,
            "title": title.strip(),
            "kind": str(raw.get("kind") or "IMPLEMENT"),
            "objectiveRefs": [str(item) for item in raw.get("objectiveRefs", [])],
            "checkpointRefs": [str(item) for item in raw.get("checkpointRefs", [])],
            "dependsOn": dependencies,
            "status": state,
            "summary": str(raw.get("summary") or ""),
            "startedAt": raw.get("startedAt"),
            "completedAt": raw.get("completedAt"),
            "updatedAt": now_iso(),
        })
    unknown = sorted({dep for node in nodes for dep in node["dependsOn"] if dep not in seen})
    if unknown:
        raise ControlError("HC-PROGRESS-NODE-CLOSURE", f"progress dependencies reference unknown nodes: {unknown}")
    remaining = {node["id"]: set(node["dependsOn"]) for node in nodes}
    while remaining:
        ready = {node_id for node_id, dependencies in remaining.items() if not dependencies}
        if not ready:
            raise ControlError("HC-PROGRESS-NODE-CYCLE", f"progress dependencies contain a cycle: {sorted(remaining)}")
        for node_id in ready:
            remaining.pop(node_id)
        for dependencies in remaining.values():
            dependencies.difference_update(ready)
    return nodes


def _validate_scorecard_plan(value: Any, known_checkpoints: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ControlError("HC-SCORECARD-PLAN", "scorecardPlan must be an object")
    if value.get("weights") != DOMAIN_WEIGHTS:
        raise ControlError("HC-SCORECARD-WEIGHTS", "scorecard weights must be 40/25/20/15")
    items = value.get("items")
    if not isinstance(items, list):
        raise ControlError("HC-SCORECARD-PLAN", "scorecard items must be an array")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    domains: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            raise ControlError("HC-SCORECARD-PLAN", "scorecard item must be an object")
        item_id = raw.get("id")
        domain = raw.get("category") or raw.get("domain")
        if not isinstance(item_id, str) or not item_id or item_id in seen or domain not in DOMAIN_WEIGHTS:
            raise ControlError("HC-SCORECARD-PLAN", f"invalid or duplicate scorecard item: {item_id}")
        statement = raw.get("statement")
        refs = raw.get("checkpointIds", [])
        sources = raw.get("factSources", [])
        if (
            not isinstance(statement, str)
            or not statement.strip()
            or not isinstance(refs, list)
            or not refs
            or not isinstance(sources, list)
            or not sources
        ):
            raise ControlError("HC-SCORECARD-PLAN", f"scorecard item needs statement, checkpointIds and factSources: {item_id}")
        checkpoint_refs = [str(item) for item in refs]
        unknown_checkpoints = sorted(set(checkpoint_refs) - known_checkpoints)
        if unknown_checkpoints:
            raise ControlError("HC-SCORECARD-PLAN", f"scorecard item references unknown checkpoints: {unknown_checkpoints}")
        normalized_sources: list[dict[str, Any]] = []
        source_kinds: set[str] = set()
        for source in sources:
            if not isinstance(source, dict):
                raise ControlError("HC-SCORECARD-FACT-SOURCE", f"scorecard fact source must be an object: {item_id}")
            kind = source.get("kind")
            source_refs = source.get("refs")
            if kind not in {"CHECKPOINT", "CASE", "EVIDENCE", "REVIEW", "CORE_CONTROL"} or not isinstance(source_refs, list) or not source_refs or any(not isinstance(ref, str) or not ref for ref in source_refs):
                raise ControlError("HC-SCORECARD-FACT-SOURCE", f"scorecard fact source is invalid: {item_id}")
            if kind == "CHECKPOINT" and not set(source_refs).issubset(known_checkpoints):
                raise ControlError("HC-SCORECARD-FACT-SOURCE", f"scorecard facts reference unknown checkpoints: {item_id}")
            if kind == "CORE_CONTROL" and not set(source_refs).issubset({"RULE-CORE-OBSERVABLE-CANDIDATE", "RULE-CORE-FAILURE-CONSERVATION"}):
                raise ControlError("HC-SCORECARD-FACT-SOURCE", f"scorecard facts reference unknown core controls: {item_id}")
            normalized_sources.append({"kind": kind, "refs": [str(ref) for ref in source_refs]})
            source_kinds.add(str(kind))
        eligible_kinds = {
            "FUNCTIONALITY": {"CHECKPOINT", "CASE", "EVIDENCE"},
            "ROBUSTNESS_SECURITY": {"CASE", "EVIDENCE", "CORE_CONTROL"},
            "AUDIT": {"REVIEW"},
            "PROCESS": {"CORE_CONTROL"},
        }[str(domain)]
        if not source_kinds & eligible_kinds:
            raise ControlError("HC-SCORECARD-FACT-SOURCE", f"scorecard category lacks an eligible fact source: {item_id}")
        normalized.append({"id": item_id, "category": domain, "statement": statement.strip(), "checkpointIds": checkpoint_refs, "factSources": normalized_sources})
        seen.add(item_id); domains.add(domain)
    missing = sorted(set(DOMAIN_WEIGHTS) - domains)
    if missing:
        raise ControlError("HC-SCORECARD-DOMAIN-COVERAGE", f"scorecard plan is missing domains: {missing}")
    return {"weights": dict(DOMAIN_WEIGHTS), "items": normalized}


@contextmanager
def _exclusive_lock(path: Path, timeout_seconds: float = 5.0) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    handle = path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    acquired = False
    while not acquired:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            if time.monotonic() >= deadline:
                handle.close()
                raise ControlError("HC-PROGRESS-LOCK", "another context is updating the local progress record", status="BLOCKED")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _render_after_update(project: Path, ledger_path: Path) -> dict[str, Any]:
    from .dashboard import generate_dashboard
    return generate_dashboard(
        project,
        ledger_path.parent,
        progress_ledger=ledger_path,
        project_lock_held=True,
    )


def _persist_ledger_and_render(
    project: Path,
    ledger_path: Path,
    ledger: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    """Keep a failed cache render from consuming a progress revision.

    The ledger is the durable local record and the Dashboard is a rebuildable
    view.  A caller must nevertheless be able to trust a failed command: when
    rendering cannot close, restore the prior revision (or remove a brand-new
    task record) before surfacing the error.
    """

    write_json_atomic(ledger_path, ledger)
    try:
        return _render_after_update(project, ledger_path)
    except Exception:
        if previous is None:
            shutil.rmtree(ledger_path.parent, ignore_errors=True)
        else:
            write_json_atomic(ledger_path, previous)
            try:
                _render_after_update(project, ledger_path)
            except Exception:
                # The view is non-authoritative and may be rebuilt later.  The
                # important guarantee here is that the failed revision did not
                # replace the durable local record.
                pass
        raise


def progress_init(project: Path, spec_path: Path) -> dict[str, Any]:
    project = project.expanduser().resolve()
    if not project.is_dir():
        raise ControlError("HC-PROGRESS-PROJECT", "project directory does not exist", status="BLOCKED")
    spec = _read_spec(spec_path)
    validate_object("progress-plan", spec)
    task_id = _task_id_from_spec(spec)
    identity = project_identity(project)
    nodes = _validate_nodes(spec.get("nodes"))
    known_checkpoints = {
        ref
        for node in nodes
        for ref in node.get("checkpointRefs", [])
        if isinstance(ref, str) and ref
    }
    scorecard = _validate_scorecard_plan(spec.get("scorecardPlan"), known_checkpoints)
    purpose = spec.get("projectPurpose")
    goal = spec.get("currentGoal")
    if not isinstance(purpose, str) or not purpose.strip() or not isinstance(goal, str) or not goal.strip():
        raise ControlError("HC-PROGRESS-ORIENTATION", "projectPurpose and currentGoal are required")
    leaked_orientation = [name for name, text in (("projectPurpose", purpose), ("currentGoal", goal)) if _PLAIN_FORBIDDEN.search(text)]
    if leaked_orientation:
        raise ControlError("HC-PLAIN-LANGUAGE-JARGON", f"progress orientation contains internal terms: {leaked_orientation}")
    plan = {
        "projectId": str(spec.get("projectId") or identity["projectInstanceId"]),
        "taskId": task_id,
        "projectPurpose": purpose.strip(),
        "currentGoal": goal.strip(),
        "nodes": nodes,
        "scorecardPlan": scorecard,
    }
    plan_sha = sha256_bytes(canonical_bytes(plan))
    path = progress_path(project, task_id)
    with _exclusive_lock(project_lock_path(project)):
        created_new = False
        if path.exists():
            current = _load_ledger(project, path, task_id)
            if current.get("planSha256") != plan_sha:
                raise ControlError("HC-PROGRESS-PLAN-DRIFT", "a different progress plan already exists for this task", status="BLOCKED")
            ledger = current
        else:
            created = now_iso()
            ledger = {
                "schemaVersion": PROGRESS_SCHEMA_VERSION,
                "source": SOURCE,
                "temporary": True,
                "manualClearOnly": True,
                "project": {**identity, "projectId": str(spec.get("projectId") or Path(identity["root"]).name)},
                "taskBinding": {"taskId": task_id},
                "revision": 0,
                "planSha256": plan_sha,
                "projectPurpose": plan["projectPurpose"],
                "currentGoal": plan["currentGoal"],
                "nodes": nodes,
                "scorecardPlan": scorecard,
                "events": [],
                "report": None,
                "reportHistory": [],
                "createdAt": created,
                "updatedAt": created,
            }
            _validate_ledger(project, ledger, task_id)
            created_new = True
        dashboard = (
            _persist_ledger_and_render(project, path, ledger, None)
            if created_new
            else _render_after_update(project, path)
        )
    return envelope(
        status="PASS",
        checks=[check("HC-PROGRESS-INITIALIZED", "PASS", "local temporary progress record is initialized")],
        formal={"eligible": False, "maxClaimLevel": "DIAGNOSTIC", "blockers": ["HC-PROGRESS-NON-AUTHORITATIVE"]},
        data={"projectInstanceId": identity["projectInstanceId"], "taskId": task_id, "revision": ledger["revision"], "ledger": str(path), "dashboard": dashboard.get("data")},
    )


def progress_init_for_bootstrap(project: Path, bootstrap_spec_path: Path) -> dict[str, Any]:
    """Create the zero-context local view before bootstrap touches the project."""

    project = project.expanduser().resolve()
    existing_path = progress_path(project, None)
    if existing_path.is_file():
        with _exclusive_lock(project_lock_path(project)):
            ledger = _load_ledger(project, existing_path, None)
            dashboard = _render_after_update(project, existing_path)
        return envelope(
            status="PASS",
            checks=[check("HC-PROGRESS-INITIALIZED", "PASS", "the existing pre-task local progress record remains active")],
            formal={"eligible": False, "maxClaimLevel": "DIAGNOSTIC", "blockers": ["HC-PROGRESS-NON-AUTHORITATIVE"]},
            data={
                "projectInstanceId": ledger["project"]["projectInstanceId"],
                "taskId": None,
                "revision": ledger["revision"],
                "ledger": str(existing_path),
                "dashboard": dashboard.get("data"),
            },
        )
    raw = _read_spec(bootstrap_spec_path)
    project_id = str(raw.get("projectId") or project.expanduser().resolve().name)
    slice_value = raw.get("firstVerticalSlice")
    outcome = slice_value.get("outcome") if isinstance(slice_value, dict) else None
    purpose = str(outcome or f"帮助用户使用 {project_id} 完成当前项目目标。")
    if _PLAIN_FORBIDDEN.search(purpose):
        purpose = "帮助用户完成当前项目所面向的实际工作。"
    plan = {
        "projectId": project_id,
        "taskId": None,
        "projectPurpose": purpose,
        "currentGoal": purpose,
        "nodes": [
            {
                "id": "NODE-BOOTSTRAP",
                "title": "建立项目计划和本地进度页面",
                "kind": "PLAN",
                "objectiveRefs": [],
                "checkpointRefs": ["CP-BOOTSTRAP-FUNCTION", "CP-BOOTSTRAP-ROBUSTNESS", "CP-BOOTSTRAP-AUDIT", "CP-BOOTSTRAP-PROCESS"],
                "dependsOn": [],
            }
        ],
        "scorecardPlan": {
            "weights": dict(DOMAIN_WEIGHTS),
            "items": [
                {"id": "SCORE-BOOTSTRAP-FUNCTION", "category": "FUNCTIONALITY", "statement": "项目的首个可见结果达到约定", "checkpointIds": ["CP-BOOTSTRAP-FUNCTION"], "factSources": [{"kind": "CHECKPOINT", "refs": ["CP-BOOTSTRAP-FUNCTION"]}]},
                {"id": "SCORE-BOOTSTRAP-ROBUSTNESS", "category": "ROBUSTNESS_SECURITY", "statement": "首个结果的故障与安全边界得到核对", "checkpointIds": ["CP-BOOTSTRAP-ROBUSTNESS"], "factSources": [{"kind": "CASE", "refs": ["CASE-BOOTSTRAP-ROBUSTNESS"]}]},
                {"id": "SCORE-BOOTSTRAP-AUDIT", "category": "AUDIT", "statement": "要求的结果得到独立核对", "checkpointIds": ["CP-BOOTSTRAP-AUDIT"], "factSources": [{"kind": "REVIEW", "refs": ["FRESH-INDEPENDENT-REVIEW"]}]},
                {"id": "SCORE-BOOTSTRAP-PROCESS", "category": "PROCESS", "statement": "最低范围与事实边界得到遵守", "checkpointIds": ["CP-BOOTSTRAP-PROCESS"], "factSources": [{"kind": "CORE_CONTROL", "refs": ["RULE-CORE-OBSERVABLE-CANDIDATE"]}]},
            ],
        },
    }
    with tempfile.TemporaryDirectory(prefix="vibe-control-bootstrap-progress-", ignore_cleanup_errors=True) as temporary:
        path = Path(temporary) / "progress-plan.json"
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        return progress_init(project, path)


def progress_update(project: Path, spec_path: Path, expected_revision: int) -> dict[str, Any]:
    project = project.expanduser().resolve()
    spec = _read_spec(spec_path)
    validate_object("progress-event", spec)
    task_id = _task_id_from_spec(spec)
    path = progress_path(project, task_id)
    with _exclusive_lock(project_lock_path(project)):
        ledger = _load_ledger(project, path, task_id)
        previous_ledger = copy.deepcopy(ledger)
        if ledger.get("revision") != expected_revision:
            raise ControlError("HC-PROGRESS-REVISION", "local progress revision changed; reload before updating", status="BLOCKED", details={"expected": expected_revision, "actual": ledger.get("revision")})
        node_id = spec.get("nodeId")
        target = spec.get("status")
        summary = spec.get("summary")
        actor_id = spec.get("actorId")
        session_id = spec.get("sessionId")
        if target not in NODE_STATES or not all(isinstance(value, str) and value.strip() for value in (node_id, summary, actor_id, session_id)):
            raise ControlError("HC-PROGRESS-EVENT", "nodeId, status, summary, actorId and sessionId are required")
        if actor_id.strip() != "coordinator":
            raise ControlError("HC-PROGRESS-WRITER", "only the coordinator role may update the local progress record", status="BLOCKED")
        node = next((item for item in ledger.get("nodes", []) if item.get("id") == node_id), None)
        if not isinstance(node, dict):
            raise ControlError("HC-PROGRESS-NODE", f"unknown progress node: {node_id}")
        previous = node.get("status")
        if isinstance(ledger.get("report"), dict):
            acknowledgement = spec.get("resumeAcknowledgement")
            if not isinstance(acknowledgement, dict):
                raise ControlError(
                    "HC-PROGRESS-STOPPED",
                    "progress is stopped for owner review; record an explicit owner continuation before updating nodes",
                    status="BLOCKED",
                )
            if (
                acknowledgement.get("actorId") != "owner"
                or acknowledgement.get("action") != "CONTINUE"
                or not isinstance(acknowledgement.get("summary"), str)
                or not acknowledgement["summary"].strip()
                or not isinstance(acknowledgement.get("acknowledgedAt"), str)
                or not acknowledgement["acknowledgedAt"].strip()
                or acknowledgement.get("reportRevision") != ledger["report"].get("reportRevision")
                or acknowledgement.get("reportSha256") != ledger["report"].get("reportSha256")
            ):
                raise ControlError(
                    "HC-PROGRESS-RESUME-ACKNOWLEDGEMENT",
                    "owner continuation acknowledgement is incomplete",
                    status="BLOCKED",
                )
            if target != previous:
                raise ControlError(
                    "HC-PROGRESS-RESUME-ATOMICITY",
                    "resuming only clears the review stop; change the node in a later revision",
                    status="BLOCKED",
                )
            timestamp = now_iso()
            report = ledger["report"]
            ledger.setdefault("reportHistory", []).append(
                {
                    "report": report,
                    "resolution": {
                        "actorId": "owner",
                        "action": "CONTINUE",
                        "summary": acknowledgement["summary"].strip(),
                        "acknowledgedAt": acknowledgement["acknowledgedAt"].strip(),
                        "reportRevision": acknowledgement["reportRevision"],
                        "reportSha256": acknowledgement["reportSha256"],
                        "recordedAt": timestamp,
                    },
                }
            )
            ledger["report"] = None
            ledger["revision"] = expected_revision + 1
            ledger["updatedAt"] = timestamp
            ledger.setdefault("events", []).append(
                {
                    "revision": ledger["revision"],
                    "nodeId": None,
                    "from": "STOPPED",
                    "to": "RESUMED",
                    "actorId": "coordinator",
                    "sessionId": session_id.strip(),
                    "at": timestamp,
                    "summary": acknowledgement["summary"].strip(),
                    "failureFingerprint": None,
                    "contentIdentity": None,
                    "stopReason": "OWNER_REVIEW_RESOLVED",
                }
            )
            _validate_ledger(project, ledger, task_id)
            dashboard = _persist_ledger_and_render(
                project, path, ledger, previous_ledger
            )
            return envelope(
                status="PASS",
                checks=[check("HC-PROGRESS-RESUMED", "PASS", "owner review stop was archived before automation resumed")],
                formal={"eligible": False, "maxClaimLevel": "DIAGNOSTIC", "blockers": ["HC-PROGRESS-NON-AUTHORITATIVE"]},
                data={
                    "taskId": task_id,
                    "nodeId": node_id,
                    "status": previous,
                    "revision": ledger["revision"],
                    "ledger": str(path),
                    "dashboard": dashboard.get("data"),
                },
            )
        if spec.get("resumeAcknowledgement") is not None:
            raise ControlError(
                "HC-PROGRESS-RESUME-NOT-STOPPED",
                "owner continuation cannot be recorded because no review stop is active",
                status="BLOCKED",
            )
        failure_fingerprint = spec.get("failureFingerprint")
        content_identity = spec.get("contentIdentity")
        if target == "FAILED" and (
            not isinstance(failure_fingerprint, str)
            or not _SHA256.fullmatch(failure_fingerprint)
            or not isinstance(content_identity, str)
            or not _SHA256.fullmatch(content_identity)
        ):
            raise ControlError("HC-PROGRESS-FAILURE-IDENTITY", "FAILED progress requires failureFingerprint and contentIdentity SHA-256 values")
        repeated_without_change = target == "FAILED" and any(
            isinstance(item, dict)
            and item.get("nodeId") == node_id
            and item.get("to") == "FAILED"
            and item.get("failureFingerprint") == failure_fingerprint
            and item.get("contentIdentity") == content_identity
            for item in ledger.get("events", [])
        )
        effective_target = "BLOCKED" if repeated_without_change else target
        if target == previous:
            raise ControlError("HC-PROGRESS-TRANSITION", "a progress update must change node state")
        if effective_target not in _TRANSITIONS.get(str(previous), set()):
            raise ControlError("HC-PROGRESS-TRANSITION", f"invalid progress transition: {previous} -> {effective_target}")
        if effective_target in {"ACTIVE", "COMPLETED"}:
            incomplete = [dep for dep in node.get("dependsOn", []) if next((item for item in ledger["nodes"] if item.get("id") == dep), {}).get("status") != "COMPLETED"]
            if incomplete:
                raise ControlError("HC-PROGRESS-DEPENDENCY", f"progress dependencies are incomplete: {incomplete}", status="BLOCKED")
        timestamp = now_iso()
        node["status"] = effective_target
        node["summary"] = summary.strip()
        node["updatedAt"] = timestamp
        if effective_target == "ACTIVE" and not node.get("startedAt"):
            node["startedAt"] = timestamp
        if effective_target == "COMPLETED":
            node["completedAt"] = timestamp
        ledger["revision"] = expected_revision + 1
        ledger["updatedAt"] = timestamp
        ledger.setdefault("events", []).append({
            "revision": ledger["revision"], "nodeId": node_id, "from": previous, "to": effective_target,
            "actorId": actor_id.strip(), "sessionId": session_id.strip(), "at": timestamp,
            "summary": summary.strip(), "failureFingerprint": failure_fingerprint,
            "contentIdentity": content_identity,
            "stopReason": "PAUSED_NO_PROGRESS" if repeated_without_change else None,
        })
        _validate_ledger(project, ledger, task_id)
        dashboard = _persist_ledger_and_render(
            project, path, ledger, previous_ledger
        )
    report_status = "BLOCKED" if repeated_without_change else "PASS"
    check_id = "HC-AUTOMATION-PAUSED-NO-PROGRESS" if repeated_without_change else "HC-PROGRESS-REVISION"
    return envelope(
        status=report_status,
        checks=[check(check_id, report_status, "repeated failure without content change requires a review" if repeated_without_change else "local progress revision advanced atomically")],
        formal={"eligible": False, "maxClaimLevel": "DIAGNOSTIC", "blockers": [check_id if repeated_without_change else "HC-PROGRESS-NON-AUTHORITATIVE"]},
        data={"taskId": task_id, "nodeId": node_id, "status": effective_target, "stopReason": "PAUSED_NO_PROGRESS" if repeated_without_change else None, "revision": ledger["revision"], "ledger": str(path), "dashboard": dashboard.get("data")},
    )


def progress_stop(project: Path, spec_path: Path, expected_revision: int) -> dict[str, Any]:
    project = project.expanduser().resolve()
    spec = _read_spec(spec_path)
    validate_object("progress-report-packet", spec)
    task_id = _task_id_from_spec(spec)
    path = progress_path(project, task_id)
    plain = _validate_plain_language(spec.get("plainLanguage"))
    plain["canContinue"] = "需要完成当前人工复核后再决定是否继续。"
    plain["canRelease"] = "这份本机进度记录不能证明项目可以作为最终版本交付。"
    actions = _validate_next_actions(spec.get("nextActions"))
    if spec.get("actorId") != "coordinator":
        raise ControlError("HC-PROGRESS-WRITER", "only the coordinator role may stop the local progress record", status="BLOCKED")
    with _exclusive_lock(project_lock_path(project)):
        ledger = _load_ledger(project, path, task_id)
        previous_ledger = copy.deepcopy(ledger)
        if ledger.get("revision") != expected_revision:
            raise ControlError("HC-PROGRESS-REVISION", "local progress revision changed; reload before stopping", status="BLOCKED")
        if isinstance(ledger.get("report"), dict):
            raise ControlError(
                "HC-PROGRESS-ALREADY-STOPPED",
                "an owner review report is already active; it must be resolved before another stop",
                status="BLOCKED",
            )
        allowed_refs = _allowed_action_refs(ledger, str(spec.get("reason") or "OWNER_REVIEW"))
        unknown_refs = sorted(
            {
                ref
                for action in actions
                for ref in action["sourceRefs"]
                if ref not in allowed_refs
            }
        )
        if unknown_refs:
            raise ControlError("HC-PROGRESS-NEXT-ACTION-CLOSURE", f"next actions reference unknown current facts: {unknown_refs}")
        timestamp = now_iso()
        report_revision = expected_revision + 1
        report = {
            "plainLanguage": plain,
            "nextActions": actions,
            "stoppedAt": timestamp,
            "reason": str(spec.get("reason") or "OWNER_REVIEW"),
            "reportRevision": report_revision,
        }
        report["reportSha256"] = sha256_bytes(canonical_bytes(report))
        ledger["revision"] = report_revision
        ledger["updatedAt"] = timestamp
        ledger["report"] = report
        ledger.setdefault("events", []).append({
            "revision": ledger["revision"], "nodeId": None, "from": None, "to": "STOPPED",
            "actorId": str(spec.get("actorId") or "coordinator"), "sessionId": str(spec.get("sessionId") or "current"),
            "at": timestamp, "summary": str(spec.get("summary") or "已到达人工复核点。"),
        })
        _validate_ledger(project, ledger, task_id)
        dashboard = _persist_ledger_and_render(
            project, path, ledger, previous_ledger
        )
    return envelope(
        status="BLOCKED",
        checks=[check("HC-PROGRESS-OWNER-REVIEW", "BLOCKED", "automation reached a human review point")],
        formal={"eligible": False, "maxClaimLevel": "DIAGNOSTIC", "blockers": ["HC-PROGRESS-OWNER-REVIEW"]},
        data={
            "taskId": task_id,
            "revision": ledger["revision"],
            "reportRevision": report["reportRevision"],
            "reportSha256": report["reportSha256"],
            "ledger": str(path),
            "dashboard": dashboard.get("data"),
            "nextActions": actions,
        },
        plain_language=plain,
    )


def progress_clear(project: Path, task_id: str | None, scope: str, confirmation: str) -> dict[str, Any]:
    identity = project_identity(project.expanduser().resolve())
    if scope not in {"current-task", "project"}:
        raise ControlError("HC-PROGRESS-CLEAR-SCOPE", f"unknown progress clear scope: {scope}")
    if confirmation != identity["projectInstanceId"]:
        raise ControlError("HC-PROGRESS-CLEAR-CONFIRMATION", "project identity confirmation does not match", status="BLOCKED")
    if scope == "current-task" and task_id is None:
        current = load_progress(project)
        binding = current.get("taskBinding") if isinstance(current, dict) else None
        task_id = binding.get("taskId") if isinstance(binding, dict) else None
    project_root = project_progress_root(project)
    target = project_root if scope == "project" else progress_path(project, task_id).parent
    with _exclusive_lock(project_lock_path(project)):
        try:
            target.resolve().relative_to(_validated_cache(project))
        except ValueError as exc:
            raise ControlError("HC-PROGRESS-CLEAR-SCOPE", "progress clear target escaped the local cache") from exc
        existed = target.exists()
        if existed:
            shutil.rmtree(target)
    return envelope(
        status="PASS",
        checks=[check("HC-PROGRESS-CLEARED", "PASS", "local temporary progress history was removed on explicit request")],
        formal={"eligible": False, "maxClaimLevel": "DIAGNOSTIC", "blockers": []},
        data={"projectInstanceId": identity["projectInstanceId"], "scope": scope, "taskId": task_id, "removed": existed, "path": str(target)},
    )


def load_progress(project: Path, task_id: str | None = None, explicit: Path | None = None) -> dict[str, Any] | None:
    if explicit is not None:
        path = explicit.expanduser().resolve()
        return _load_ledger(project, path, task_id) if path.is_file() else None
    root = project_progress_root(project)
    preferred = progress_path(project, task_id)
    if preferred.is_file():
        return _load_ledger(project, preferred, task_id)
    if task_id is not None:
        return None
    candidates = sorted(root.glob("*/progress-ledger.json"), key=lambda item: item.stat().st_mtime, reverse=True) if root.is_dir() else []
    return _load_ledger(project, candidates[0], None) if candidates else None
