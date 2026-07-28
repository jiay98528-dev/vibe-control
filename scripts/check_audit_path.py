#!/usr/bin/env python3
"""Read-only Windows audit checkout path-budget check."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


CHECK_ID = "VC-AUDIT-PATH-BUDGET"


def git(source: Path, *args: str) -> tuple[int, str, str]:
    result = subprocess.run(["git", "-C", str(source), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def report(source: Path, candidate: str, audit_root: Path, limit: int) -> dict:
    source = source.resolve(); audit_root = audit_root.resolve()
    code, candidate_commit, error = git(source, "rev-parse", "--verify", f"{candidate}^{{commit}}")
    if code:
        return {"status": "BLOCKED", "checkId": CHECK_ID, "message": "candidate cannot be resolved", "details": {"candidate": candidate, "error": error}}
    code, listing, error = git(source, "ls-tree", "-r", "--name-only", candidate_commit)
    if code:
        return {"status": "BLOCKED", "checkId": CHECK_ID, "message": "candidate tree cannot be enumerated", "details": {"candidateCommit": candidate_commit, "error": error}}
    paths = [value for value in listing.splitlines() if value]
    longest = max(paths, key=len, default="")
    projected = len(str(audit_root)) + (1 if longest else 0) + len(longest)
    _, longpaths, _ = git(source, "config", "--bool", "--get", "core.longpaths")
    ok = projected <= limit
    return {
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
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the path budget before materializing an audit candidate")
    parser.add_argument("--source", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--audit-root", required=True)
    parser.add_argument("--limit", type=int, default=240)
    args = parser.parse_args()
    value = report(Path(args.source), args.candidate, Path(args.audit_root), args.limit)
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if value["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
