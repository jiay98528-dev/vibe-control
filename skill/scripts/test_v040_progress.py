#!/usr/bin/env python3
"""Focused regressions for the 0.4 local progress ledger and scorecard."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
RUNTIME = SKILL_ROOT / "assets" / "project-control" / "runtime"
sys.path.insert(0, str(RUNTIME))

from vibe_runtime.common import ControlError, canonical_bytes, sha256_bytes  # noqa: E402
import vibe_runtime.dashboard as dashboard_module  # noqa: E402
import vibe_runtime.progress as progress_module  # noqa: E402
from vibe_runtime.dashboard import _markdown, _scorecard_projection, generate_dashboard  # noqa: E402
from vibe_runtime.progress import (  # noqa: E402
    _exclusive_lock,
    progress_clear,
    progress_init,
    progress_init_for_bootstrap,
    progress_path,
    progress_stop,
    progress_update,
    project_identity,
    load_progress,
)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def plan(project_id: str = "local-project") -> dict:
    return {
        "projectId": project_id,
        "taskId": "TASK-040",
        "projectPurpose": "帮助用户安全地完成一项工作 <img src=x onerror=alert(1)>",
        "currentGoal": "建立可观察的自动协作流程",
        "nodes": [
            {"id": "N1", "title": "建立计划", "kind": "PLAN", "objectiveRefs": ["KO-001"], "checkpointRefs": ["CP-F"], "dependsOn": []},
            {"id": "N2", "title": "实现与复核", "kind": "IMPLEMENT", "objectiveRefs": ["KO-001"], "checkpointRefs": ["CP-R", "CP-A", "CP-P"], "dependsOn": ["N1"]},
        ],
        "scorecardPlan": {
            "weights": {"FUNCTIONALITY": 40, "ROBUSTNESS_SECURITY": 25, "AUDIT": 20, "PROCESS": 15},
            "items": [
                {"id": "S-F", "category": "FUNCTIONALITY", "statement": "功能", "checkpointIds": ["CP-F"], "factSources": [{"kind": "CHECKPOINT", "refs": ["CP-F"]}]},
                {"id": "S-R", "category": "ROBUSTNESS_SECURITY", "statement": "健壮", "checkpointIds": ["CP-R"], "factSources": [{"kind": "CASE", "refs": ["CASE-R"]}]},
                {"id": "S-A", "category": "AUDIT", "statement": "审计", "checkpointIds": ["CP-A"], "factSources": [{"kind": "REVIEW", "refs": ["FRESH-INDEPENDENT-REVIEW"]}]},
                {"id": "S-P", "category": "PROCESS", "statement": "流程", "checkpointIds": ["CP-P"], "factSources": [{"kind": "CORE_CONTROL", "refs": ["RULE-CORE-OBSERVABLE-CANDIDATE"]}]},
            ],
        },
    }


def event(node: str, status: str, summary: str) -> dict:
    return {"taskId": "TASK-040", "nodeId": node, "status": status, "summary": summary, "actorId": "coordinator", "sessionId": "main"}


def stop_packet() -> dict:
    return {
        "taskId": "TASK-040",
        "reason": "OWNER_REVIEW",
        "summary": "到达负责人复核点。",
        "actorId": "coordinator",
        "sessionId": "main",
        "plainLanguage": {
            "projectPurpose": "帮助用户安全地完成这项工作。",
            "whatWasDone": "计划中的实现与核对已经完成。",
            "whatWorksNow": "已通过的功能现在可以按约定使用。",
            "whatStillDoesNotWork": "仍需负责人亲自确认最终体验。",
            "userImpact": "确认前不应把它当作最终成品。",
            "canContinue": "可以进入负责人复核。",
            "canRelease": "现在还不能作为最终版本交付。",
        },
        "nextActions": [
            {"type": "RECOMMENDED", "statement": "复核当前结果", "impact": "确认功能是否符合实际需要。", "risk": "低；不改变目标。", "humanEffort": "需要查看一次结果。", "sourceRefs": ["N2"]},
            {"type": "ALTERNATIVE", "statement": "退回继续修改", "impact": "保持原目标并修正当前不足。", "risk": "低；会延后完成。", "humanEffort": "需要说明不满意的位置。", "sourceRefs": ["N2"]},
            {"type": "OPEN", "statement": "提出其他做法", "impact": "可以输入新的方向。", "risk": "取决于新方向。", "humanEffort": "需要描述新方向。", "sourceRefs": ["N2"]},
        ],
    }


def test_non_git_init_revision_dashboard_and_stop() -> None:
    with tempfile.TemporaryDirectory(prefix="vc040-progress-", ignore_cleanup_errors=True) as name:
        project = Path(name) / "project"
        project.mkdir()
        initial_inventory = list(project.iterdir())
        spec = Path(name) / "plan.json"
        write_json(spec, plan())
        result = progress_init(project, spec)
        assert result["status"] == "PASS", result
        assert list(project.iterdir()) == initial_inventory
        ledger = progress_path(project, "TASK-040")
        output = ledger.parent
        assert all((output / item).is_file() for item in ("progress-ledger.json", "status.json", "summary.md", "index.html"))
        html = (output / "index.html").read_text(encoding="utf-8")
        assert "<img src=x" not in html and "&lt;img src=x" in html
        assert "http://" not in html and "https://" not in html
        footer = html.split("<footer>", 1)[1].split("</footer>", 1)[0]
        assert "SHA-256" not in footer and "来源" not in footer and "DERIVED" not in footer
        assert 'role="status"' in html and 'aria-label="功能完成度：尚未建立计分基线"' in html
        for name_to_remove in ("status.json", "summary.md", "index.html"):
            (output / name_to_remove).unlink()
        rebuilt = generate_dashboard(project, None)
        assert rebuilt["status"] == "PASS"
        assert all((output / item).is_file() for item in ("progress-ledger.json", "status.json", "summary.md", "index.html"))
        initial_status = json.loads((output / "status.json").read_text(encoding="utf-8"))
        status_keys = list(initial_status)
        assert status_keys[-1] == "plainLanguage", status_keys
        assert initial_status["project"]["id"] == "local-project"
        assert initial_status["objectives"]["goal"] == "建立可观察的自动协作流程"
        assert initial_status["plainLanguage"]["whatWasDone"] == "已完成 0 项计划事项，共有 2 项。"
        assert all("Dashboard" not in value for value in initial_status["notProven"])

        update = Path(name) / "event.json"
        write_json(update, event("N1", "ACTIVE", "开始建立计划。"))
        progress_update(project, update, 0)
        write_json(update, event("N1", "COMPLETED", "计划已经建立。"))
        progress_update(project, update, 1)
        try:
            progress_update(project, update, 1)
        except ControlError as exc:
            assert exc.check_id == "HC-PROGRESS-REVISION"
        else:
            raise AssertionError("stale progress revision was accepted")
        write_json(update, event("N2", "ACTIVE", "开始实现。"))
        progress_update(project, update, 2)
        write_json(update, event("N2", "COMPLETED", "实现与核对完成。"))
        progress_update(project, update, 3)
        packet = Path(name) / "stop.json"
        write_json(packet, stop_packet())
        stopped = progress_stop(project, packet, 4)
        assert stopped["status"] == "BLOCKED" and stopped["plainLanguage"]["canRelease"]
        summary = (output / "summary.md").read_text(encoding="utf-8")
        assert summary.rfind("## 给没有开发背景的人看的说明") > summary.rfind("## 下一步选择")
        assert summary.rstrip().endswith("- 能否交付：现在没有足够依据把它作为最终版本交付。")

        write_json(update, event("N2", "COMPLETED", "普通更新不能越过复核点。"))
        for operation, expected in (
            (lambda: progress_update(project, update, 5), "HC-PROGRESS-STOPPED"),
            (lambda: progress_stop(project, packet, 5), "HC-PROGRESS-ALREADY-STOPPED"),
        ):
            try:
                operation()
            except ControlError as exc:
                assert exc.check_id == expected, (exc.check_id, expected)
            else:
                raise AssertionError(f"{expected} did not fail closed")
        resume = event("N2", "COMPLETED", "负责人同意继续。")
        resume["resumeAcknowledgement"] = {
            "actorId": "owner",
            "action": "CONTINUE",
            "summary": "负责人已看过当前结果并选择继续。",
            "acknowledgedAt": "2026-07-30T12:00:00Z",
            "reportRevision": stopped["data"]["reportRevision"],
            "reportSha256": stopped["data"]["reportSha256"],
        }
        write_json(update, resume)
        resumed = progress_update(project, update, 5)
        assert resumed["status"] == "PASS" and resumed["data"]["revision"] == 6
        loaded = load_progress(project, "TASK-040")
        assert loaded["report"] is None and len(loaded["reportHistory"]) == 1
        try:
            progress_update(project, update, 6)
        except ControlError as exc:
            assert exc.check_id == "HC-PROGRESS-RESUME-NOT-STOPPED"
        else:
            raise AssertionError("a stale owner continuation was replayed")

        identity = project_identity(project)["projectInstanceId"]
        cleared = progress_clear(project, "TASK-040", "current-task", identity)
        assert cleared["data"]["removed"] is True and not output.exists()
        lost_output = Path(name) / "lost-dashboard"
        generate_dashboard(project, lost_output)
        lost = json.loads((lost_output / "status.json").read_text(encoding="utf-8"))
        assert lost["progress"]["recordLost"] is True


def test_dependency_and_clear_identity_fail_closed() -> None:
    with tempfile.TemporaryDirectory(prefix="vc040-guards-", ignore_cleanup_errors=True) as name:
        project = Path(name) / "project"
        project.mkdir()
        spec = Path(name) / "plan.json"
        write_json(spec, plan())
        progress_init(project, spec)
        update = Path(name) / "event.json"
        write_json(update, event("N2", "ACTIVE", "不应越过依赖。"))
        worker_event = event("N1", "ACTIVE", "子任务不应写入。")
        worker_event["actorId"] = "implementer"
        worker_path = Path(name) / "worker-event.json"
        write_json(worker_path, worker_event)
        for operation, expected in (
            (lambda: progress_update(project, update, 0), "HC-PROGRESS-DEPENDENCY"),
            (lambda: progress_update(project, worker_path, 0), "HC-PROGRESS-WRITER"),
            (lambda: progress_clear(project, "TASK-040", "current-task", "wrong"), "HC-PROGRESS-CLEAR-CONFIRMATION"),
        ):
            try:
                operation()
            except ControlError as exc:
                assert exc.check_id == expected, (exc.check_id, expected)
            else:
                raise AssertionError(f"{expected} did not fail closed")
        shutil.rmtree(progress_path(project, "TASK-040").parent, ignore_errors=True)


def test_scorecard_is_recomputed_from_checkpoint_facts() -> None:
    contract = {"scorecardPlan": plan()["scorecardPlan"]}
    checkpoints = [
        {"id": "CP-F", "status": "PASS"},
        {"id": "CP-R", "status": "PASS"},
        {"id": "CP-A", "status": "BLOCKED"},
        {"id": "CP-P", "status": "PASS"},
    ]
    cases = [{"caseId": "CASE-R", "status": "PASS"}, {"caseId": "C2", "status": "BLOCKED"}]
    score = _scorecard_projection(contract, checkpoints, cases, None)
    assert score["baselineEstablished"] is True
    assert score["overall"] == 65.0, score
    assert next(row for row in score["domains"] if row["category"] == "AUDIT")["passed"] == 0
    assert next(row for row in score["domains"] if row["category"] == "PROCESS")["passed"] == 0
    complete = _scorecard_projection(
        contract,
        checkpoints,
        cases,
        None,
        review={"reviewId": "REVIEW-1", "result": "PASS"},
        candidate={"candidateId": "CANDIDATE-1", "commit": "a" * 40, "tree": "b" * 40},
        validation={
            "integrity": {
                "checks": [
                    {"id": "HC-CANDIDATE-IDENTITY", "status": "PASS"},
                    {"id": "HC-REVIEW-TRACKED", "status": "PASS"},
                    {"id": "HC-REVIEW-RESULT", "status": "PASS"},
                    {"id": "HC-PROJECT-REVIEW-GATE", "status": "PASS"},
                    {"id": "HC-REVIEW-ATTESTATION", "status": "PASS"},
                    {"id": "HC-REVIEW-EVIDENCE-REF-1", "status": "PASS"},
                    {"id": "HC-REVIEW-TRANSCRIPT-REVIEW-1", "status": "PASS"},
                ]
            },
            "state": {"derived": {"phase": "AUDITED"}},
        },
    )
    assert complete["overall"] == 100.0, complete
    unqualified = _scorecard_projection(
        contract,
        checkpoints,
        cases,
        None,
        review={"reviewId": "REVIEW-1", "result": "PASS"},
        candidate={"candidateId": "CANDIDATE-1", "commit": "a" * 40, "tree": "b" * 40},
        validation={
            "integrity": {
                "checks": [
                    {"id": "HC-CANDIDATE-IDENTITY", "status": "PASS"},
                    {"id": "HC-PROJECT-REVIEW-GATE", "status": "FAIL"},
                ]
            },
            "state": {"derived": {"phase": "VERIFIED"}},
        },
    )
    assert next(row for row in unqualified["domains"] if row["category"] == "AUDIT")["passed"] == 0
    assert score["evidenceCoverage"] == {"passed": 1, "total": 2, "ratio": 50.0}
    missing = _scorecard_projection(None, checkpoints, cases, None)
    assert missing["baselineEstablished"] is False and missing["overall"] is None


def test_repeated_failure_without_change_pauses() -> None:
    with tempfile.TemporaryDirectory(prefix="vc040-no-progress-", ignore_cleanup_errors=True) as name:
        project = Path(name) / "project"
        project.mkdir()
        spec = Path(name) / "plan.json"
        write_json(spec, plan())
        progress_init(project, spec)
        event_path = Path(name) / "event.json"
        write_json(event_path, event("N1", "ACTIVE", "开始第一次尝试。"))
        progress_update(project, event_path, 0)
        failure = event("N1", "FAILED", "同一个检查仍然失败。")
        failure.update({"failureFingerprint": "1" * 64, "contentIdentity": "2" * 64})
        write_json(event_path, failure)
        first = progress_update(project, event_path, 1)
        assert first["status"] == "PASS" and first["data"]["status"] == "FAILED"
        write_json(event_path, event("N1", "ACTIVE", "在原范围内再次尝试。"))
        progress_update(project, event_path, 2)
        write_json(event_path, failure)
        repeated = progress_update(project, event_path, 3)
        assert repeated["status"] == "BLOCKED"
        assert repeated["data"]["status"] == "BLOCKED"
        assert repeated["data"]["stopReason"] == "PAUSED_NO_PROGRESS"
        identity = project_identity(project)["projectInstanceId"]
        progress_clear(project, "TASK-040", "current-task", identity)


def test_task_paths_do_not_collide_or_cross_load() -> None:
    with tempfile.TemporaryDirectory(prefix="vc040-task-path-", ignore_cleanup_errors=True) as name:
        project = Path(name) / "project"
        project.mkdir()
        paths = []
        for task_id in ("TASK/A", "TASK?A"):
            value = plan()
            value["taskId"] = task_id
            spec = Path(name) / f"{len(paths)}.json"
            write_json(spec, value)
            progress_init(project, spec)
            paths.append(progress_path(project, task_id))
        assert paths[0] != paths[1] and all(path.is_file() for path in paths)
        assert load_progress(project, "TASK-MISSING") is None
        assert "[点这里](javascript:alert(1))" not in _markdown("[点这里](javascript:alert(1))")
        identity = project_identity(project)["projectInstanceId"]
        progress_clear(project, None, "project", identity)


def test_cycle_cache_scope_and_dirty_byte_drift() -> None:
    with tempfile.TemporaryDirectory(prefix="vc040-hardening-", ignore_cleanup_errors=True) as name:
        project = Path(name) / "project"
        project.mkdir()
        cyclic = plan()
        cyclic["nodes"][0]["dependsOn"] = ["N2"]
        spec = Path(name) / "cycle.json"
        write_json(spec, cyclic)
        try:
            progress_init(project, spec)
        except ControlError as exc:
            assert exc.check_id == "HC-PROGRESS-NODE-CYCLE"
        else:
            raise AssertionError("cyclic progress nodes were accepted")

        previous_local = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = str(project)
        try:
            write_json(spec, plan())
            try:
                progress_init(project, spec)
            except ControlError as exc:
                assert exc.check_id == "HC-PROGRESS-CACHE-SCOPE"
            else:
                raise AssertionError("a progress cache inside the project was accepted")
            assert not (project / "vibe-control").exists()
        finally:
            if previous_local is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = previous_local

        subprocess.run(["git", "init", "-q", str(project)], check=True)
        subprocess.run(["git", "-C", str(project), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(project), "config", "user.email", "test@example.invalid"], check=True)
        tracked = project / "tracked.txt"
        tracked.write_text("clean\n", encoding="utf-8", newline="\n")
        subprocess.run(["git", "-C", str(project), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(project), "commit", "-qm", "test: baseline"], check=True)
        tracked.write_text("dirty-before\n", encoding="utf-8", newline="\n")
        original = dashboard_module._build_snapshot

        def mutate_during_projection(root: Path, progress_ledger: Path | None = None) -> dict:
            tracked.write_text("dirty-after\n", encoding="utf-8", newline="\n")
            return original(root, progress_ledger)

        dashboard_module._build_snapshot = mutate_during_projection
        try:
            try:
                generate_dashboard(project, Path(name) / "dirty-output")
            except ControlError as exc:
                assert exc.check_id == "HC-DASHBOARD-READONLY-DRIFT"
            else:
                raise AssertionError("dirty file byte drift was not detected")
        finally:
            dashboard_module._build_snapshot = original


def test_dashboard_destination_is_project_bound() -> None:
    with tempfile.TemporaryDirectory(prefix="vc040-destination-", ignore_cleanup_errors=True) as name:
        first = Path(name) / "first"
        second = Path(name) / "second"
        destination = Path(name) / "shared-output"
        first.mkdir(); second.mkdir()
        one = generate_dashboard(first, destination)
        assert one["status"] == "PASS"
        try:
            generate_dashboard(second, destination)
        except ControlError as exc:
            assert exc.check_id == "HC-DASHBOARD-DESTINATION-OWNERSHIP"
        else:
            raise AssertionError("a second project overwrote an existing dashboard destination")


def test_bootstrap_first_view_and_tampered_report_fails_closed() -> None:
    with tempfile.TemporaryDirectory(prefix="vc040-bootstrap-view-", ignore_cleanup_errors=True) as name:
        project = Path(name) / "project"
        project.mkdir()
        bootstrap_spec = Path(name) / "bootstrap.json"
        write_json(
            bootstrap_spec,
            {
                "projectId": "plain-project",
                "firstVerticalSlice": {"outcome": "帮助用户整理本地资料"},
            },
        )
        initialized = progress_init_for_bootstrap(project, bootstrap_spec)
        assert initialized["status"] == "PASS"
        ledger_path = progress_path(project, None)
        assert ledger_path.is_file() and (ledger_path.parent / "index.html").is_file()
        changed_bootstrap = Path(name) / "changed-bootstrap.json"
        write_json(changed_bootstrap, {"projectId": "plain-project", "firstVerticalSlice": {"outcome": "帮助用户整理另一类资料"}})
        reused = progress_init_for_bootstrap(project, changed_bootstrap)
        assert reused["status"] == "PASS" and reused["data"]["revision"] == 0

        packet = stop_packet()
        packet["taskId"] = None
        packet["nextActions"] = [
            {**item, "sourceRefs": ["NODE-BOOTSTRAP"]}
            for item in packet["nextActions"]
        ]
        packet_path = Path(name) / "stop.json"
        write_json(packet_path, packet)
        stopped = progress_stop(project, packet_path, 0)
        ledger = json.loads(ledger_path.read_text(encoding="utf-8-sig"))
        ledger["report"]["plainLanguage"]["whatWasDone"] = "PASS"
        bound = dict(ledger["report"])
        bound.pop("reportSha256")
        ledger["report"]["reportSha256"] = sha256_bytes(canonical_bytes(bound))
        write_json(ledger_path, ledger)
        output = Path(name) / "tampered-view"
        projected = generate_dashboard(project, output)
        assert projected["status"] == "PASS"
        status = json.loads((output / "status.json").read_text(encoding="utf-8-sig"))
        assert status["progress"]["recordLost"] is True
        assert "HC-PROGRESS-LEDGER-CORRUPT" in status["blockers"]
        assert stopped["data"]["reportSha256"] != ledger["report"]["reportSha256"]
        identity = project_identity(project)["projectInstanceId"]
        progress_clear(project, None, "project", identity)


def test_os_lock_cannot_be_stolen_by_old_mtime() -> None:
    with tempfile.TemporaryDirectory(prefix="vc040-lock-", ignore_cleanup_errors=True) as name:
        lock = Path(name) / "progress.lock"
        ready = Path(name) / "ready"
        source = (
            "import sys,time\n"
            f"sys.path.insert(0, {str(RUNTIME)!r})\n"
            "from pathlib import Path\n"
            "from vibe_runtime.progress import _exclusive_lock\n"
            f"lock=Path({str(lock)!r}); ready=Path({str(ready)!r})\n"
            "with _exclusive_lock(lock, timeout_seconds=5):\n"
            " ready.write_text('ready', encoding='utf-8')\n"
            " time.sleep(1.2)\n"
        )
        child = subprocess.Popen([sys.executable, "-c", source])
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert ready.exists(), "lock holder did not start"
            old = time.time() - 86400
            os.utime(lock, (old, old))
            try:
                with _exclusive_lock(lock, timeout_seconds=0.2):
                    raise AssertionError("an active operating-system lock was stolen")
            except ControlError as exc:
                assert exc.check_id == "HC-PROGRESS-LOCK"
        finally:
            child.wait(timeout=5)
        with _exclusive_lock(lock, timeout_seconds=0.5):
            pass


def test_failed_dashboard_render_does_not_consume_revision() -> None:
    with tempfile.TemporaryDirectory(prefix="vc040-render-rollback-", ignore_cleanup_errors=True) as name:
        project = Path(name) / "project"
        project.mkdir()
        spec = Path(name) / "plan.json"
        write_json(spec, plan())
        progress_init(project, spec)
        ledger_path = progress_path(project, "TASK-040")
        original = ledger_path.read_bytes()
        update = Path(name) / "event.json"
        write_json(update, event("N1", "ACTIVE", "开始建立计划。"))
        render = progress_module._render_after_update

        def fail_render(_project: Path, _ledger: Path) -> dict:
            raise RuntimeError("simulated dashboard cache failure")

        progress_module._render_after_update = fail_render
        try:
            try:
                progress_update(project, update, 0)
            except RuntimeError as exc:
                assert "simulated dashboard" in str(exc)
            else:
                raise AssertionError("a failed Dashboard render was reported as success")
        finally:
            progress_module._render_after_update = render
        assert ledger_path.read_bytes() == original
        loaded = load_progress(project, "TASK-040")
        assert loaded["revision"] == 0
        assert next(item for item in loaded["nodes"] if item["id"] == "N1")["status"] == "PENDING"
        completed = progress_update(project, update, 0)
        assert completed["status"] == "PASS" and completed["data"]["revision"] == 1
        identity = project_identity(project)["projectInstanceId"]
        progress_clear(project, "TASK-040", "current-task", identity)


def main() -> int:
    tests = [
        ("non-git-ledger-dashboard-stop", test_non_git_init_revision_dashboard_and_stop),
        ("dependency-and-clear-identity", test_dependency_and_clear_identity_fail_closed),
        ("scorecard-derived-facts", test_scorecard_is_recomputed_from_checkpoint_facts),
        ("repeated-failure-no-progress", test_repeated_failure_without_change_pauses),
        ("task-path-identity-and-markdown", test_task_paths_do_not_collide_or_cross_load),
        ("cycle-cache-and-dirty-byte-drift", test_cycle_cache_scope_and_dirty_byte_drift),
        ("dashboard-destination-project-binding", test_dashboard_destination_is_project_bound),
        ("bootstrap-first-view-and-tamper", test_bootstrap_first_view_and_tampered_report_fails_closed),
        ("os-lock-no-mtime-steal", test_os_lock_cannot_be_stolen_by_old_mtime),
        ("dashboard-render-revision-rollback", test_failed_dashboard_render_does_not_consume_revision),
    ]
    results = []
    for case_id, test in tests:
        started = time.monotonic()
        try:
            test()
            results.append({"case": case_id, "status": "PASS", "durationSeconds": round(time.monotonic() - started, 3)})
        except Exception as exc:
            results.append({"case": case_id, "status": "FAIL", "durationSeconds": round(time.monotonic() - started, 3), "errorType": type(exc).__name__, "error": str(exc)})
    passed = sum(item["status"] == "PASS" for item in results)
    report = {
        "test": "vibe-control-0.4.0-progress",
        "status": "PASS" if passed == len(results) else "FAIL",
        "counters": {"total": len(results), "passed": passed, "failed": len(results) - passed, "timedOut": 0, "skipped": 0},
        "cases": results,
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
