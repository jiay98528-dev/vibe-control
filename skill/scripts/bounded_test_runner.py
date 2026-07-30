#!/usr/bin/env python3
"""Process-isolated, bounded and observable runner shared by deep regressions."""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable


_OUTPUT_LOCK = threading.Lock()
SUITE_CLEANUP_BUDGET_SECONDS = 30


def suite_execution_budget(suite_timeout: int) -> float:
    """Reserve a fixed cleanup window inside normal suite deadlines.

    Very small synthetic deadlines still receive the same explicit cleanup
    allowance, so their total wall-clock contract is timeout + cleanup budget.
    """
    return max(0.1, float(suite_timeout - SUITE_CLEANUP_BUDGET_SECONDS))


def emit_progress(event: dict) -> None:
    with _OUTPUT_LOCK:
        print(json.dumps(event, ensure_ascii=False), file=sys.stderr, flush=True)


def terminate_process_tree(process: subprocess.Popen[str], *, cleanup_timeout: float = 5.0) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=cleanup_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.communicate(timeout=cleanup_timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.communicate(timeout=cleanup_timeout)
        except subprocess.TimeoutExpired:
            pass


def parse_worker_output(
    case_id: str,
    stdout: str,
    stderr: str,
    returncode: int,
    *,
    identity_field: str,
    protocol_id: str,
) -> dict:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        return {
            identity_field: case_id,
            "status": "FAIL",
            "checkId": protocol_id,
            "error": f"worker emitted {len(lines)} non-empty stdout lines",
            "workerExitCode": returncode,
            "workerStderr": stderr[-2000:],
        }
    try:
        result = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        return {
            identity_field: case_id,
            "status": "FAIL",
            "checkId": protocol_id,
            "error": f"worker output is not JSON: {exc}",
            "workerExitCode": returncode,
            "workerStderr": stderr[-2000:],
        }
    if not isinstance(result, dict) or result.get(identity_field) != case_id or result.get("status") not in {"PASS", "FAIL"}:
        return {
            identity_field: case_id,
            "status": "FAIL",
            "checkId": protocol_id,
            "error": "worker result identity or status is invalid",
            "workerExitCode": returncode,
        }
    expected_exit = 0 if result["status"] == "PASS" else 1
    if returncode != expected_exit:
        return {
            identity_field: case_id,
            "status": "FAIL",
            "checkId": protocol_id,
            "error": f"worker exit {returncode} contradicts status {result['status']}",
            "workerExitCode": returncode,
        }
    result["workerExitCode"] = returncode
    return result


def run_supervised_command(
    case_id: str,
    temp_root: Path,
    command: list[str],
    timeout_seconds: int,
    *,
    identity_field: str = "case",
    protocol_id: str = "BOUNDED-CASE-PROTOCOL",
    timeout_id: str = "BOUNDED-CASE-TIMEOUT",
    active: dict[str, subprocess.Popen[str]] | None = None,
    active_lock: threading.Lock | None = None,
    cancelled: threading.Event | None = None,
    env_overrides: dict[str, str] | None = None,
) -> dict:
    case_temp = temp_root / case_id
    case_temp.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if env_overrides:
        reserved = {"TEMP", "TMP", "TMPDIR", "PYTHONDONTWRITEBYTECODE"}
        overlap = reserved & {str(key).upper() for key in env_overrides}
        if overlap:
            raise ValueError(f"env_overrides cannot replace runner isolation variables: {sorted(overlap)}")
        env.update({str(key): str(value) for key, value in env_overrides.items()})
    env.update({"TEMP": str(case_temp), "TMP": str(case_temp), "TMPDIR": str(case_temp), "PYTHONDONTWRITEBYTECODE": "1"})
    kwargs: dict = {
        "cwd": str(Path(__file__).resolve().parent),
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    if active is not None and active_lock is not None:
        with active_lock:
            active[case_id] = process
            cancellation_observed = cancelled is not None and cancelled.is_set()
        if cancellation_observed:
            terminate_process_tree(process)
            with active_lock:
                active.pop(case_id, None)
            return {identity_field: case_id, "status": "TIMEOUT", "checkId": timeout_id, "timeoutSeconds": timeout_seconds}
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        terminate_process_tree(process)
        return {identity_field: case_id, "status": "TIMEOUT", "checkId": timeout_id, "timeoutSeconds": timeout_seconds}
    finally:
        if active is not None and active_lock is not None:
            with active_lock:
                active.pop(case_id, None)
    return parse_worker_output(case_id, stdout, stderr, process.returncode, identity_field=identity_field, protocol_id=protocol_id)


def counters(results: list[dict]) -> dict[str, int]:
    passed = sum(item.get("status") == "PASS" for item in results)
    failed = sum(item.get("status") == "FAIL" for item in results)
    timed_out = sum(item.get("status") == "TIMEOUT" for item in results)
    skipped = sum(item.get("status") == "SKIPPED" for item in results)
    return {"total": len(results), "passed": passed, "failed": failed, "timedOut": timed_out, "skipped": skipped}


def run_suite(
    case_ids: list[str],
    *,
    command_for: Callable[[str], list[str]],
    temp_root: Path,
    jobs: int,
    case_timeout: int,
    suite_timeout: int,
    identity_field: str,
    protocol_id: str,
    timeout_id: str,
    suite_timeout_id: str,
    env_overrides: dict[str, str] | None = None,
) -> tuple[list[dict], float]:
    started = time.monotonic()
    execution_budget = suite_execution_budget(suite_timeout)
    active: dict[str, subprocess.Popen[str]] = {}
    active_lock = threading.Lock()
    cancelled = threading.Event()
    by_id: dict[str, dict] = {}
    results_lock = threading.Lock()
    work: queue.Queue[str] = queue.Queue()
    for case_id in case_ids:
        work.put(case_id)

    def execute(case_id: str) -> dict:
        emit_progress({"event": "case-start", identity_field: case_id, "total": len(case_ids)})
        if cancelled.is_set():
            result = {identity_field: case_id, "status": "TIMEOUT", "checkId": suite_timeout_id, "timeoutSeconds": suite_timeout}
            emit_progress({"event": "case-complete", **result})
            return result
        result = run_supervised_command(
            case_id,
            temp_root,
            command_for(case_id),
            case_timeout,
            identity_field=identity_field,
            protocol_id=protocol_id,
            timeout_id=timeout_id,
            active=active,
            active_lock=active_lock,
            cancelled=cancelled,
            env_overrides=env_overrides,
        )
        emit_progress({"event": "case-complete", **result})
        return result

    def worker() -> None:
        while not cancelled.is_set():
            try:
                case_id = work.get_nowait()
            except queue.Empty:
                return
            try:
                result = execute(case_id)
            except Exception as exc:
                result = {identity_field: case_id, "status": "FAIL", "checkId": protocol_id, "errorType": type(exc).__name__, "error": str(exc)}
            with results_lock:
                by_id.setdefault(case_id, result)
            work.task_done()

    workers = [
        threading.Thread(target=worker, name=f"bounded-suite-{index + 1}", daemon=True)
        for index in range(max(1, min(jobs, len(case_ids))))
    ]
    for thread in workers:
        thread.start()
    last_heartbeat = started
    while True:
        with results_lock:
            completed = len(by_id)
        if completed == len(case_ids):
            break
        remaining = execution_budget - (time.monotonic() - started)
        if remaining <= 0:
            cancelled.set()
            with results_lock:
                incomplete_at_timeout = [case_id for case_id in case_ids if case_id not in by_id]
            with active_lock:
                processes = list(active.values())
            cleanup_threads = [threading.Thread(target=terminate_process_tree, args=(process,), daemon=True) for process in processes]
            for thread in cleanup_threads:
                thread.start()
            cleanup_deadline = time.monotonic() + 8.0
            for thread in cleanup_threads:
                thread.join(timeout=max(0.0, cleanup_deadline - time.monotonic()))
            with results_lock:
                for case_id in incomplete_at_timeout:
                    by_id[case_id] = {identity_field: case_id, "status": "TIMEOUT", "checkId": suite_timeout_id, "timeoutSeconds": suite_timeout}
            emit_progress({
                "event": "suite-timeout", "remainingCases": sorted(incomplete_at_timeout),
                "timeoutSeconds": suite_timeout, "cleanupBudgetSeconds": SUITE_CLEANUP_BUDGET_SECONDS,
            })
            break
        now = time.monotonic()
        if now - last_heartbeat >= 10:
            emit_progress({"event": "heartbeat", "completed": completed, "total": len(case_ids), "elapsedSeconds": round(now - started, 3)})
            last_heartbeat = now
        time.sleep(min(0.1, remaining))
    join_deadline = time.monotonic() + 8.0
    for thread in workers:
        thread.join(timeout=max(0.0, join_deadline - time.monotonic()))
    results = [by_id.get(case_id, {identity_field: case_id, "status": "TIMEOUT", "checkId": suite_timeout_id}) for case_id in case_ids]
    return results, time.monotonic() - started
