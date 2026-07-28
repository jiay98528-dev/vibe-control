#!/usr/bin/env python3
"""Validate an installed vibe-control package without implying a formal seal."""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.metadata
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True


class ArgumentError(Exception):
    pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ArgumentError(message)


def check(check_id: str, status: str, message: str, **details: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"id": check_id, "status": status, "message": message}
    if details:
        value["details"] = details
    return value


def dependency_checks(root: Path) -> list[dict[str, Any]]:
    lock_path = root / "assets" / "project-control" / "runtime" / "dependency-lock.json"
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return [check("INSTALL-DEPENDENCY-LOCK", "BLOCKED", "dependency lock is unavailable or malformed", error=str(exc))]
    if not isinstance(lock, dict) or not isinstance(lock.get("packages"), dict):
        return [check("INSTALL-DEPENDENCY-LOCK", "BLOCKED", "dependency lock must contain a packages object")]
    python_ok = (3, 12) <= tuple(sys.version_info[:2]) < (3, 13)
    checks = [check(
        "INSTALL-DEPENDENCY-PYTHON",
        "PASS" if python_ok else "BLOCKED",
        "Python 3.12.x is available" if python_ok else "Python 3.12.x is required",
        actual=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    )]
    for name, expected in sorted(lock["packages"].items()):
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        ok = isinstance(expected, str) and actual == expected
        checks.append(check(
            f"INSTALL-DEPENDENCY-{name.upper().replace('-', '_')}",
            "PASS" if ok else "BLOCKED",
            "dependency version matches lock" if ok else "dependency is missing or has the wrong version",
            package=name,
            expected=expected,
            actual=actual,
        ))
    return checks


def dependency_report(checks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schemaVersion": "3.2",
        "tool": "validate_installation",
        "checkedAt": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
        "status": "BLOCKED",
        "readiness": "DEPENDENCY_BLOCKED",
        "packageMode": None,
        "formalClaimsAllowed": False,
        "maxClaimLevel": "DIAGNOSTIC",
        "checks": checks,
        "blockers": [item["id"] for item in checks if item["status"] != "PASS"],
    }


def validate(root: Path) -> dict[str, Any]:
    root = root.resolve()
    checks = dependency_checks(root)
    if any(item["status"] != "PASS" for item in checks):
        return dependency_report(checks)
    try:
        package = json.loads((root / "package-manifest.json").read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "schemaVersion": "3.2",
            "tool": "validate_installation",
            "status": "FAIL",
            "readiness": "DIAGNOSTIC",
            "packageMode": None,
            "formalClaimsAllowed": False,
            "maxClaimLevel": "DIAGNOSTIC",
            "checks": [*checks, check("INSTALL-PACKAGE-MANIFEST", "FAIL", "package manifest is unavailable or malformed", error=str(exc))],
            "blockers": ["INSTALL-PACKAGE-MANIFEST"],
        }
    runtime = root / "assets" / "project-control" / "runtime"
    sys.path.insert(0, str(runtime))
    try:
        from vibe_runtime.package_release import validate_development_package, validate_package_release
    except ImportError as exc:
        blocked = check("INSTALL-DEPENDENCY-IMPORT", "BLOCKED", "runtime dependency import failed", error=str(exc))
        return dependency_report([*checks, blocked])
    if isinstance(package, dict) and package.get("maturity") == "DEVELOPMENT_DIAGNOSTIC":
        report = validate_development_package(root)
    else:
        report = validate_package_release(root)
        report.setdefault("packageMode", "SEALED")
        report.setdefault("maxClaimLevel", "RELEASE_READY" if report.get("status") == "PASS" else "DIAGNOSTIC")
    report = {
        "schemaVersion": "3.2",
        "tool": "validate_installation",
        "checkedAt": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
        **report,
    }
    binding = report.get("binding")
    report["sourceKind"] = binding.get("sourceKind") if isinstance(binding, dict) else None
    return report


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(add_help=True)
    parser.add_argument("--skill-root", default=str(Path(__file__).resolve().parents[1]))
    try:
        args = parser.parse_args(argv)
        report = validate(Path(args.skill_root))
    except ArgumentError as exc:
        report = {
            "schemaVersion": "3.2",
            "tool": "validate_installation",
            "status": "FAIL",
            "readiness": "DIAGNOSTIC",
            "packageMode": None,
            "formalClaimsAllowed": False,
            "maxClaimLevel": "DIAGNOSTIC",
            "checks": [check("INSTALL-INVALID-ARGUMENTS", "FAIL", str(exc))],
            "blockers": ["INSTALL-INVALID-ARGUMENTS"],
        }
    except Exception as exc:
        report = {
            "schemaVersion": "3.2",
            "tool": "validate_installation",
            "status": "FAIL",
            "readiness": "DIAGNOSTIC",
            "packageMode": None,
            "formalClaimsAllowed": False,
            "maxClaimLevel": "DIAGNOSTIC",
            "checks": [check("INSTALL-INTERNAL-ERROR", "FAIL", "installation validation failed without exposing a traceback", errorType=type(exc).__name__, error=str(exc))],
            "blockers": ["INSTALL-INTERNAL-ERROR"],
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 3}.get(report.get("status"), 3)


if __name__ == "__main__":
    raise SystemExit(main())
