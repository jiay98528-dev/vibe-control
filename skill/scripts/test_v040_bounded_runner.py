#!/usr/bin/env python3
"""Windows cleanup output must never corrupt bounded-suite transcripts."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent))

import bounded_test_runner as bounded  # noqa: E402


class FakeProcess:
    pid = 4242

    def poll(self):
        return None

    def communicate(self, timeout=None):
        return ("", "")

    def kill(self):
        raise AssertionError("the mocked taskkill path should close the process")


def test_windows_taskkill_decode_policy() -> dict:
    if os.name != "nt":
        return {"case": "windows-taskkill-decode-policy", "status": "PASS", "applicable": False}

    with mock.patch.object(bounded.subprocess, "run") as run:
        run.return_value.returncode = 0
        bounded.terminate_process_tree(FakeProcess())
        kwargs = run.call_args.kwargs
        assert kwargs.get("text") is True
        assert kwargs.get("encoding") == "utf-8"
        assert kwargs.get("errors") == "replace"
    return {"case": "windows-taskkill-decode-policy", "status": "PASS", "applicable": True}


def main() -> int:
    result = test_windows_taskkill_decode_policy()
    report = {
        "status": "PASS",
        "suite": "v040-bounded-runner",
        "counters": {"total": 1, "passed": 1, "failed": 0, "skipped": 0},
        "cases": [result],
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
