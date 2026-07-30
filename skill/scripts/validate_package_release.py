#!/usr/bin/env python3
"""Validate package-level independent-audit closure for the exact Git candidate."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from public_report import JsonArgumentError, JsonArgumentParser, emit, finalize


def with_plain_language(report: dict) -> dict:
    status = str(report.get("status") or "FAIL")
    return finalize(report, {
        "projectPurpose": "确认这份项目协作工具是否已经完成最终交付前的独立核对。",
        "whatWasDone": "已核对工具内容、外部检查记录和最终版本之间是否完全对应。",
        "whatWorksNow": "核对成功时，这份工具可以按已检查的内容交付使用。" if status == "PASS" else "当前只能继续作为开发中的工具使用。",
        "whatStillDoesNotWork": "仍缺少最终核对或内容之间存在不一致。" if status != "PASS" else "这项结果不代表采用该工具的其他项目已经完成。",
        "userImpact": "问题未闭合前，不应把这份工具称为最终版本。" if status != "PASS" else "这份工具本身已完成核对，但各项目仍需各自验收。",
        "canContinue": "可以继续修复和核对。" if status != "PASS" else "可以进入负责人决定是否交付。",
        "canRelease": "现在还不能作为最终版本交付。" if status != "PASS" else "可以由负责人决定是否交付这份工具。",
    })


def main(argv: list[str] | None = None) -> int:
    try:
        parser = JsonArgumentParser()
        parser.add_argument("--skill-root", default=str(Path(__file__).resolve().parents[1]))
        args = parser.parse_args(argv)
        root = Path(args.skill_root).resolve()
        runtime = root / "assets" / "project-control" / "runtime"
        sys.path.insert(0, str(runtime))
        try:
            from vibe_runtime.package_release import validate_package_release
        except (ImportError, ModuleNotFoundError) as exc:
            report = {
                "schemaVersion": "4.0",
                "status": "BLOCKED",
                "readiness": "DEPENDENCY_BLOCKED",
                "formalClaimsAllowed": False,
                "checks": [{
                    "id": "PKG-AUDIT-DEPENDENCY",
                    "status": "BLOCKED",
                    "message": "required package validation dependency is unavailable",
                    "details": {"errorType": type(exc).__name__, "error": str(exc)},
                }],
                "blockers": ["PKG-AUDIT-DEPENDENCY"],
            }
        else:
            report = validate_package_release(root)
        report = with_plain_language(report)
        code = {"PASS": 0, "BLOCKED": 2, "FAIL": 3}.get(report.get("status"), 3)
    except JsonArgumentError as exc:
        report = with_plain_language({
            "schemaVersion": "4.0",
            "status": "FAIL",
            "readiness": "DIAGNOSTIC",
            "formalClaimsAllowed": False,
            "checks": [{"id": "PKG-AUDIT-INVALID-ARGUMENTS", "status": "FAIL", "message": str(exc)}],
            "blockers": ["PKG-AUDIT-INVALID-ARGUMENTS"],
        })
        code = 3
    except Exception as exc:
        report = with_plain_language({
            "schemaVersion": "4.0",
            "status": "FAIL",
            "readiness": "DIAGNOSTIC",
            "formalClaimsAllowed": False,
            "checks": [{
                "id": "PKG-AUDIT-INTERNAL-ERROR",
                "status": "FAIL",
                "message": "package validation failed without exposing a traceback",
                "details": {"errorType": type(exc).__name__, "error": str(exc)},
            }],
            "blockers": ["PKG-AUDIT-INTERNAL-ERROR"],
        })
        code = 3
    emit(report)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
