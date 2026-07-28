#!/usr/bin/env python3
"""Process-isolated, bounded and observable runner shared by deep regressions."""

from __future__ import annotations

import concurrent.futures
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable


_OUTPUT_LOCK = threading.Lock()


def emit_progress(event: dict) -> None:
    with _OUTPUT_LOCK:
        print(json.dumps(event, ensure_ascii=False), file=sys.stderr, flush=True)


def terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()


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
) -> dict:
    case_temp = temp_root / case_id
    case_temp.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
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
) -> tuple[list[dict], float]:
    started = time.monotonic()
    active: dict[str, subprocess.Popen[str]] = {}
    active_lock = threading.Lock()
    by_id: dict[str, dict] = {}

    def execute(case_id: str) -> dict:
        emit_progress({"event": "case-start", identity_field: case_id, "total": len(case_ids)})
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
        )
        emit_progress({"event": "case-complete", **result})
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(jobs, len(case_ids)))) as pool:
        futures = {pool.submit(execute, case_id): case_id for case_id in case_ids}
        pending = set(futures)
        last_heartbeat = started
        while pending:
            remaining = suite_timeout - (time.monotonic() - started)
            if remaining <= 0:
                with active_lock:
                    processes = list(active.values())
                for process in processes:
                    terminate_process_tree(process)
                for future in pending:
                    future.cancel()
                for future in pending:
                    case_id = futures[future]
                    by_id.setdefault(case_id, {identity_field: case_id, "status": "TIMEOUT", "checkId": suite_timeout_id, "timeoutSeconds": suite_timeout})
                emit_progress({"event": "suite-timeout", "remainingCases": sorted(futures[item] for item in pending), "timeoutSeconds": suite_timeout})
                break
            done, pending = concurrent.futures.wait(pending, timeout=min(1.0, remaining), return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                case_id = futures[future]
                try:
                    by_id[case_id] = future.result()
                except Exception as exc:
                    by_id[case_id] = {identity_field: case_id, "status": "FAIL", "checkId": protocol_id, "errorType": type(exc).__name__, "error": str(exc)}
            now = time.monotonic()
            if pending and now - last_heartbeat >= 10:
                emit_progress({"event": "heartbeat", "completed": len(by_id), "total": len(case_ids), "elapsedSeconds": round(now - started, 3)})
                last_heartbeat = now
    results = [by_id.get(case_id, {identity_field: case_id, "status": "TIMEOUT", "checkId": suite_timeout_id}) for case_id in case_ids]
    return results, time.monotonic() - started
