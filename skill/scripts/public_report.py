#!/usr/bin/env python3
"""Standard-library helpers for user-facing standalone script reports."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


PLAIN_LANGUAGE_FIELDS = (
    "projectPurpose",
    "whatWasDone",
    "whatWorksNow",
    "whatStillDoesNotWork",
    "userImpact",
    "canContinue",
    "canRelease",
)


class JsonArgumentError(ValueError):
    """Raised instead of letting argparse write usage text to stderr."""


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise JsonArgumentError(message)


def finalize(report: dict[str, Any], plain_language: dict[str, str]) -> dict[str, Any]:
    """Validate and append the seven-sentence projection as the last field."""

    if set(plain_language) != set(PLAIN_LANGUAGE_FIELDS):
        raise ValueError("plainLanguage must contain the seven public fields")
    normalized = {
        name: plain_language[name].strip()
        for name in PLAIN_LANGUAGE_FIELDS
        if isinstance(plain_language.get(name), str) and plain_language[name].strip()
    }
    if len(normalized) != len(PLAIN_LANGUAGE_FIELDS):
        raise ValueError("plainLanguage fields must be nonempty text")
    report.pop("plainLanguage", None)
    report["plainLanguage"] = normalized
    return report


def emit(report: dict[str, Any]) -> None:
    """Write exactly one JSON document to stdout."""

    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
