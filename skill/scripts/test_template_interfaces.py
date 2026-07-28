#!/usr/bin/env python3
"""Keep user-facing JSON templates aligned with the public/runtime Schema 3.2 interface."""
from __future__ import annotations

import json
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def test_template_interfaces() -> None:
    root = Path(__file__).resolve().parents[1]
    templates = root / "assets" / "project-control" / "templates"
    schemas = root / "assets" / "project-control" / "schemas"
    mappings = {
        "review-attestation.json": "review-attestation.schema.json",
        "release-receipt.json": "release-receipt.schema.json",
        "external-release-audit.json": "external-release-audit.schema.json",
        "approval-signature.json": "approval-signature.schema.json",
    }
    for template_name, schema_name in mappings.items():
        value = load(templates / template_name); schema = load(schemas / schema_name)
        missing = sorted(set(schema["required"]) - set(value))
        if missing:
            raise AssertionError(f"{template_name} omits required keys: {missing}")
    bootstrap = load(templates / "bootstrap-spec.json")
    if bootstrap.get("releaseIntent") != "REQUIRES_USER_SELECTION":
        raise AssertionError("bootstrap template must force an explicit user-selected release intent")
    if bootstrap.get("trustedKeys") != []:
        raise AssertionError("bootstrap template must not imply private-key or public-key setup for non-release projects")


def main() -> int:
    try:
        test_template_interfaces()
        print(json.dumps({"status": "PASS", "test": "test_template_interfaces"}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "test": "test_template_interfaces", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
