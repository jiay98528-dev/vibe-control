#!/usr/bin/env python3
"""Validate package-level independent-audit closure for the exact Git candidate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    root = Path(args.skill_root).resolve()
    runtime = root / "assets" / "project-control" / "runtime"
    sys.path.insert(0, str(runtime))
    from vibe_runtime.package_release import validate_package_release

    report = validate_package_release(root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 3}.get(report["status"], 3)


if __name__ == "__main__":
    raise SystemExit(main())
