#!/usr/bin/env python3
"""Read-only Windows audit checkout path-budget check."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from public_report import JsonArgumentError, JsonArgumentParser, emit, finalize


CHECK_ID = "VC-AUDIT-PATH-BUDGET"


def plain_language(status: str) -> dict[str, str]:
    ok = status == "PASS"
    return {
        "projectPurpose": "确认最终核对所用的本机目录能够完整放下项目文件。",
        "whatWasDone": "已计算项目文件放入计划目录后需要的最长路径。",
        "whatWorksNow": "当前目录长度可以继续使用。" if ok else "当前计划目录还不能可靠使用。",
        "whatStillDoesNotWork": "这项检查不代表项目功能或最终版本已经完成。" if ok else "目录过长或输入有误，复制项目时可能失败。",
        "userImpact": "可以继续准备独立核对，但仍需实际运行项目检查。" if ok else "如果直接继续，项目文件可能无法完整复制，后续结果也不可信。",
        "canContinue": "可以按当前目录继续准备。" if ok else "需要改用更短的目录或修正输入后再继续。",
        "canRelease": "这项结果不能单独证明项目可以作为最终版本交付。",
    }


def git(source: Path, *args: str) -> tuple[int, str, str]:
    result = subprocess.run(["git", "-C", str(source), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def report(source: Path, candidate: str, audit_root: Path, limit: int) -> dict:
    source = source.resolve(); audit_root = audit_root.resolve()
    code, candidate_commit, error = git(source, "rev-parse", "--verify", f"{candidate}^{{commit}}")
    if code:
        return finalize({"status": "BLOCKED", "checkId": CHECK_ID, "message": "candidate cannot be resolved", "details": {"candidate": candidate, "error": error}}, plain_language("BLOCKED"))
    code, listing, error = git(source, "ls-tree", "-r", "--name-only", candidate_commit)
    if code:
        return finalize({"status": "BLOCKED", "checkId": CHECK_ID, "message": "candidate tree cannot be enumerated", "details": {"candidateCommit": candidate_commit, "error": error}}, plain_language("BLOCKED"))
    paths = [value for value in listing.splitlines() if value]
    longest = max(paths, key=len, default="")
    projected = len(str(audit_root)) + (1 if longest else 0) + len(longest)
    _, longpaths, _ = git(source, "config", "--bool", "--get", "core.longpaths")
    ok = projected <= limit
    return finalize({
        "status": "PASS" if ok else "BLOCKED",
        "checkId": CHECK_ID,
        "message": "planned audit root is inside the path budget" if ok else "planned audit checkout exceeds the conservative Windows path budget",
        "details": {
            "source": str(source), "candidate": candidate, "candidateCommit": candidate_commit,
            "plannedAuditRoot": str(audit_root), "trackedPaths": len(paths), "longestTrackedPath": longest,
            "longestRelativeCharacters": len(longest), "projectedCharacters": projected, "limit": limit,
            "coreLongpaths": longpaths.lower() == "true",
            "recommendation": None if ok else "use a shorter root such as C:\\vc32\\<id>, or opt in per command with git -c core.longpaths=true; do not change global Git configuration automatically",
        },
    }, plain_language("PASS" if ok else "BLOCKED"))


def main(argv: list[str] | None = None) -> int:
    try:
        parser = JsonArgumentParser(description="Check the path budget before materializing an audit candidate")
        parser.add_argument("--source", required=True)
        parser.add_argument("--candidate", required=True)
        parser.add_argument("--audit-root", required=True)
        parser.add_argument("--limit", type=int, default=240)
        args = parser.parse_args(argv)
        if args.limit < 1:
            raise JsonArgumentError("--limit must be a positive integer")
        value = report(Path(args.source), args.candidate, Path(args.audit_root), args.limit)
        code = 0 if value["status"] == "PASS" else 2
    except JsonArgumentError as exc:
        value = finalize({
            "status": "FAIL",
            "checkId": "VC-AUDIT-PATH-ARGUMENTS",
            "message": str(exc),
        }, plain_language("FAIL"))
        code = 3
    except Exception as exc:
        value = finalize({
            "status": "BLOCKED",
            "checkId": "VC-AUDIT-PATH-ENVIRONMENT",
            "message": "the path budget could not be checked in the current environment",
            "details": {"errorType": type(exc).__name__, "error": str(exc)},
        }, plain_language("BLOCKED"))
        code = 2
    emit(value)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
