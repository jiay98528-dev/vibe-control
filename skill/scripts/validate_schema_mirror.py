#!/usr/bin/env python3
"""Fail closed unless the complete public Schema 4.0 interface exactly mirrors runtime."""
from __future__ import annotations
import json
from pathlib import Path
EXPECTED_SCHEMAS = frozenset(["adapter-descriptor.schema.json","adapter-invocation.schema.json","approval-signature.schema.json","audit-closure.schema.json","automation-policy.schema.json","bootstrap-spec.schema.json","candidate-manifest.schema.json","case-catalog.schema.json","execution-evidence.schema.json","external-evidence-attestation.schema.json","external-release-audit.schema.json","handoff.schema.json","key-objectives-lock.schema.json","key-objectives-revision.schema.json","migration-plan.schema.json","migration-spec.schema.json","package-audit-evidence-manifest.schema.json","package-audit-receipt.schema.json","package-audit-report.schema.json","profile.schema.json","progress-event.schema.json","progress-plan.schema.json","progress-report-packet.schema.json","project-governance-lock.schema.json","project-positioning.schema.json","release-receipt.schema.json","resolved-rule-set.schema.json","review-attestation.schema.json","rule-overlay.schema.json","skill-binding.schema.json","stage-state.schema.json","task-contract.schema.json","task-lock.schema.json","task-progress.schema.json","upgrade-plan.schema.json","upgrade-spec.schema.json"])
def main() -> int:
    root = Path(__file__).resolve().parents[1]
    runtime = root / "assets" / "project-control" / "runtime" / "schemas"
    public = root / "assets" / "project-control" / "schemas"
    runtime_names = {path.name for path in runtime.glob("*.json")}
    public_names = {path.name for path in public.glob("*.json")}
    errors: list[dict[str, str]] = []
    for name in sorted(EXPECTED_SCHEMAS - runtime_names):
        errors.append({"id": "SCHEMA-MIRROR-RUNTIME-MISSING", "schema": name})
    for name in sorted(EXPECTED_SCHEMAS - public_names):
        errors.append({"id": "SCHEMA-MIRROR-PUBLIC-MISSING", "schema": name})
    for name in sorted((runtime_names | public_names) - EXPECTED_SCHEMAS):
        errors.append({"id": "SCHEMA-MIRROR-UNEXPECTED", "schema": name})
    for name in sorted(EXPECTED_SCHEMAS & runtime_names & public_names):
        if (runtime / name).read_bytes() != (public / name).read_bytes():
            errors.append({"id": "SCHEMA-MIRROR-BYTES", "schema": name})
    print(json.dumps({"status": "PASS" if not errors else "FAIL", "schemaVersion": "4.0", "schemas": len(EXPECTED_SCHEMAS), "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1
if __name__ == "__main__":
    raise SystemExit(main())
