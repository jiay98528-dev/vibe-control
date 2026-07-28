#!/usr/bin/env python3
"""Run the bundled deterministic project runtime."""

from pathlib import Path
import runpy


RUNTIME = Path(__file__).resolve().parents[1] / "assets" / "project-control" / "runtime" / "control.py"
runpy.run_path(str(RUNTIME), run_name="__main__")
