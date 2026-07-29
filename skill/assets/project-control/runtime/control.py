#!/usr/bin/env python3
"""Pinned entry shim for vibe-control 0.3.7 development runtime."""

import datetime as dt
import importlib.metadata
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from vibe_runtime import VERSION as RUNTIME_VERSION


def _dependency_report(checks: list[dict], message: str) -> dict:
    return {
        "schemaVersion": "3.2",
        "runtimeVersion": RUNTIME_VERSION,
        "status": "BLOCKED",
        "checkedAt": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
        "integrity": {
            "status": "BLOCKED",
            "checks": checks,
            "counts": {
                name: sum(item.get("status") == name for item in checks)
                for name in ("PASS", "BLOCKED", "FAIL", "INVALIDATED")
            },
        },
        "formal": {
            "eligible": False,
            "maxClaimLevel": "DIAGNOSTIC",
            "blockers": [item["id"] for item in checks if item.get("status") != "PASS"],
        },
        "state": None,
        "error": {"id": "DEPENDENCY_BLOCKED", "message": message, "details": checks},
    }


def _dependency_preflight() -> list[dict]:
    lock_path = Path(__file__).resolve().parent / "dependency-lock.json"
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return [{"id": "HC-DEPENDENCY-LOCK", "status": "BLOCKED", "message": f"dependency lock is unavailable or malformed: {exc}"}]
    if not isinstance(lock, dict):
        return [{"id": "HC-DEPENDENCY-LOCK", "status": "BLOCKED", "message": "dependency lock must be an object"}]
    checks = []
    python_ok = (3, 12) <= tuple(sys.version_info[:2]) < (3, 13)
    checks.append({"id": "HC-DEPENDENCY-PYTHON", "status": "PASS" if python_ok else "BLOCKED", "message": "Python version is locked" if python_ok else "Python 3.12.x is required"})
    packages = lock.get("packages")
    if not isinstance(packages, dict):
        checks.append({"id": "HC-DEPENDENCY-LOCK", "status": "BLOCKED", "message": "dependency lock packages must be an object"})
        return checks
    for name, expected in packages.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        ok = isinstance(expected, str) and actual == expected
        checks.append({
            "id": f"HC-DEPENDENCY-{name.upper().replace('-', '_')}",
            "status": "PASS" if ok else "BLOCKED",
            "message": "dependency version matches lock" if ok else "dependency missing or version mismatch",
            "details": {"package": name, "expected": expected, "actual": actual},
        })
    return checks


def _main() -> int:
    checks = _dependency_preflight()
    if any(item["status"] != "PASS" for item in checks):
        print(json.dumps(_dependency_report(checks, "runtime dependencies do not match dependency-lock.json"), ensure_ascii=False, indent=2))
        return 2
    try:
        from vibe_runtime.cli import main
    except ImportError as exc:
        checks = [{"id": "HC-DEPENDENCY-IMPORT", "status": "BLOCKED", "message": f"runtime dependency import failed: {exc}"}]
        print(json.dumps(_dependency_report(checks, "runtime dependency import failed"), ensure_ascii=False, indent=2))
        return 2
    return main()


if __name__ == "__main__":
    raise SystemExit(_main())
