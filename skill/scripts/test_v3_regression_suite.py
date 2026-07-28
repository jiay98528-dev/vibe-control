#!/usr/bin/env python3
"""Run every inherited 0.2.2 assurance suite against the 0.3.5 package tree."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUITE_TIMEOUT_SECONDS = int(os.environ.get("VIBE_CONTROL_INHERITED_SUITE_TIMEOUT", "1200"))
HEARTBEAT_SECONDS = int(os.environ.get("VIBE_CONTROL_SUITE_HEARTBEAT", "30"))
SCRIPTS = (
    "test_fixtures.py",
    "test_v2_security.py",
    "test_assurance_regressions.py",
    "test_assurance_harness.py",
    "test_formal_activation.py",
    "test_package_release_audit.py",
    "test_assurance_matrix_fail_closed.py",
    "test_crlf_checkout.py",
    "test_template_interfaces.py",
    "validate_schema_mirror.py",
)


def test_inventory_is_complete() -> None:
    missing = [name for name in SCRIPTS if not (ROOT / "scripts" / name).is_file()]
    assert not missing, f"inherited assurance scripts are missing: {missing}"


def test_forward_projects_are_not_modified() -> None:
    runtime = ROOT / "assets" / "project-control" / "runtime"
    sys.path.insert(0, str(runtime))
    from vibe_runtime.project_rules import compile_positioning
    with tempfile.TemporaryDirectory(prefix="vibe-control-forward-source-") as source_name, tempfile.TemporaryDirectory(prefix="vibe-control-forward-clone-") as clone_parent:
        source = Path(source_name); (source / "project.godot").write_text("[application]\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(source), "init"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(source), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.name", "Fixture"], check=True)
        subprocess.run(["git", "-C", str(source), "add", "project.godot"], check=True)
        subprocess.run(["git", "-C", str(source), "commit", "-m", "fixture"], check=True, capture_output=True)
        before_head = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        before_status = subprocess.run(["git", "-C", str(source), "status", "--porcelain"], check=True, capture_output=True, text=True).stdout
        clone = Path(clone_parent) / "candidate"
        subprocess.run(["git", "clone", "--no-local", str(source), str(clone)], check=True, capture_output=True)
        compile_positioning({"primaryExperience": "GAMEPLAY", "capabilityDomains": ["REALTIME_ENGINE"]}, clone, runtime)
        after_head = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        after_status = subprocess.run(["git", "-C", str(source), "status", "--porcelain"], check=True, capture_output=True, text=True).stdout
        assert (before_head, before_status) == (after_head, after_status)


def run_inherited_suite() -> list[dict]:
    results = []
    for name in SCRIPTS:
        command = [sys.executable, str(ROOT / "scripts" / name)]
        if name == "validate_schema_mirror.py":
            command += ["--skill-root", str(ROOT)]
        started = time.monotonic()
        print(json.dumps({"event": "suite-case-start", "script": name}, ensure_ascii=False), file=sys.stderr, flush=True)
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stdout_file, tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stderr_file:
            process = subprocess.Popen(command, cwd=ROOT, stdout=stdout_file, stderr=stderr_file, text=True)
            next_heartbeat = started + HEARTBEAT_SECONDS
            timed_out = False
            while process.poll() is None:
                now = time.monotonic()
                if now - started >= SUITE_TIMEOUT_SECONDS:
                    timed_out = True
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                    else:
                        process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    break
                if now >= next_heartbeat:
                    print(json.dumps({
                        "event": "suite-case-heartbeat",
                        "script": name,
                        "elapsedSeconds": int(now - started),
                    }, ensure_ascii=False), file=sys.stderr, flush=True)
                    next_heartbeat = now + HEARTBEAT_SECONDS
                time.sleep(1)
            stdout_file.seek(0); stdout = stdout_file.read()
            stderr_file.seek(0); stderr = stderr_file.read()
        elapsed = round(time.monotonic() - started, 3)
        if timed_out:
            item = {"id": "V3-INHERITED-SUITE-TIMEOUT", "script": name, "status": "FAIL", "error": f"timeout after {SUITE_TIMEOUT_SECONDS}s", "elapsedSeconds": elapsed}
        else:
            item = {"script": name, "status": "PASS" if process.returncode == 0 else "FAIL", "exitCode": process.returncode, "elapsedSeconds": elapsed}
            if process.returncode:
                item["stdoutTail"] = stdout[-4000:]
                item["stderrTail"] = stderr[-2000:]
        results.append(item)
        print(json.dumps({"event": "suite-case-end", **item}, ensure_ascii=False), file=sys.stderr, flush=True)
    return results


def main() -> int:
    try:
        test_inventory_is_complete()
        test_forward_projects_are_not_modified()
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "suite": "v3-inherited-regressions", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    results = run_inherited_suite()
    ok = all(item["status"] == "PASS" for item in results)
    print(json.dumps({
        "status": "PASS" if ok else "FAIL",
        "suite": "v3-inherited-regressions",
        "inheritedFrom": "0.2.2",
        "suiteTimeoutSeconds": SUITE_TIMEOUT_SECONDS,
        "total": len(results),
        "passed": sum(item["status"] == "PASS" for item in results),
        "failed": sum(item["status"] != "PASS" for item in results),
        "tests": results,
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
