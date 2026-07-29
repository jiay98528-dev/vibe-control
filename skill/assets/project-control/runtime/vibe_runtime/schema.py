from __future__ import annotations

from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .common import ControlError, load_json

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"


def validate_object(kind: str, value: Any) -> None:
    path = SCHEMA_DIR / f"{kind}.schema.json"
    schema = load_json(path)
    errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value), key=lambda item: list(item.absolute_path))
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "$"
        stable_ids = {
            "stage-state": "HC-SCHEMA-STATE",
            "execution-evidence": "HC-SCHEMA-EVIDENCE",
            "candidate-manifest": "HC-SCHEMA-CANDIDATE",
            "task-contract": "HC-SCHEMA-CONTRACT",
            "project-governance-lock": "HC-SCHEMA-GOVERNANCE-LOCK",
            "key-objectives-lock": "HC-SCHEMA-KEY-OBJECTIVES",
            "key-objectives-revision": "HC-SCHEMA-KEY-OBJECTIVES-REVISION",
            "external-release-audit": "HC-SCHEMA-EXTERNAL-RELEASE-AUDIT",
            "release-receipt": "HC-SCHEMA-RELEASE-RECEIPT",
            "package-audit-report": "HC-SCHEMA-PACKAGE-AUDIT-REPORT",
            "package-audit-receipt": "HC-SCHEMA-PACKAGE-AUDIT-RECEIPT",
            "bootstrap-spec": "HC-POSITIONING-SCHEMA",
            "automation-policy": "HC-SCHEMA-AUTOMATION-POLICY",
            "migration-spec": "HC-SCHEMA-MIGRATION-SPEC",
            "migration-plan": "HC-SCHEMA-MIGRATION-PLAN",
            "upgrade-spec": "HC-SCHEMA-UPGRADE-SPEC",
            "upgrade-plan": "HC-SCHEMA-UPGRADE-PLAN",
            "project-positioning": "HC-POSITIONING-SCHEMA",
            "resolved-rule-set": "HC-RULESET-BINDING",
            "adapter-descriptor": "HC-ADAPTER-CAPABILITY",
            "adapter-invocation": "HC-ADAPTER-INVOCATION",
            "skill-binding": "HC-SKILL-BINDING",
        }
        raise ControlError(stable_ids.get(kind, f"HC-SCHEMA-{kind.upper().replace('-', '_')}"), f"{kind} schema violation at {location}: {first.message}")
