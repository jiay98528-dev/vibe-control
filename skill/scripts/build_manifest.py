#!/usr/bin/env python3
"""Build or verify vibe-control/package-manifest.json."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


REQUIRED_ASSURANCE_CONTROL_IDS = frozenset({
    "CTRL-ASSURE-001", "CTRL-ASSURE-002", "CTRL-ASSURE-003", "CTRL-ASSURE-004",
    "CTRL-ASSURE-005", "CTRL-ASSURE-006", "CTRL-ASSURE-007", "CTRL-ASSURE-008",
    "CTRL-CONFIRMED-001", "CTRL-CONFIRMED-002", "CTRL-CONFIRMED-003",
    "CTRL-CONFIRMED-004", "CTRL-CONFIRMED-005", "CTRL-CONFIRMED-006",
    "CTRL-CONFIRMED-007", "CTRL-CONFIRMED-008", "CTRL-CONFIRMED-009",
    "CTRL-CONFIRMED-010", "CTRL-CONFIRMED-011", "CTRL-CONFIRMED-012",
    "CTRL-CONFIRMED-013", "CTRL-CONFIRMED-014", "CTRL-CONFIRMED-015",
    "CTRL-CONFIRMED-016", "CTRL-CONFIRMED-017", "CTRL-CONFIRMED-018",
    "CTRL-CONFIRMED-019", "CTRL-CONFIRMED-020", "CTRL-CONFIRMED-021",
    "CTRL-CONFIRMED-022", "CTRL-CONFIRMED-023", "CTRL-CONFIRMED-024",
    "CTRL-CONFIRMED-025", "CTRL-CONFIRMED-026", "CTRL-CONFIRMED-027",
    "CTRL-CONFIRMED-028", "CTRL-CONFIRMED-029", "CTRL-CONFIRMED-030",
})


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: dict) -> None:
    """Manifest bytes are integrity inputs; never inherit Windows text-newline conversion."""
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def manifest_diff(recorded: dict, current: dict) -> dict:
    """Return compact, deterministic counters for a byte-level manifest mismatch."""
    recorded_files = {item["path"]: item for item in recorded.get("files", []) if isinstance(item, dict) and "path" in item}
    current_files = {item["path"]: item for item in current.get("files", []) if isinstance(item, dict) and "path" in item}
    paths = sorted(set(recorded_files) | set(current_files))
    mismatched = [path for path in paths if recorded_files.get(path) != current_files.get(path)]
    metadata_keys = sorted((set(recorded) | set(current)) - {"files"})
    metadata_mismatch = [key for key in metadata_keys if recorded.get(key) != current.get(key)]
    return {
        "expectedFiles": len(recorded_files),
        "actualFiles": len(current_files),
        "mismatchedFiles": len(mismatched),
        "metadataMismatches": len(metadata_mismatch),
        "mismatchPaths": mismatched,
        "metadataKeys": metadata_mismatch,
    }


def assurance_validation(root: Path) -> dict:
    """Run the canonical static matrix validator and return a deterministic summary."""
    validator = root / "scripts" / "validate_assurance_matrix.py"
    try:
        result = subprocess.run(
            [sys.executable, str(validator), "--skill-root", str(root)],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        report = json.loads(result.stdout)
    except Exception:
        return {"status": "FAIL", "readiness": "DIAGNOSTIC", "formalClaimsAllowed": False}
    return {
        "status": report.get("status", "FAIL") if result.returncode == 0 else "FAIL",
        "readiness": report.get("readiness", "DIAGNOSTIC"),
        "formalClaimsAllowed": report.get("formalClaimsAllowed") is True,
    }


def build(root: Path) -> dict:
    files = []
    for path in sorted(root.rglob("*")):
        if (
            not path.is_file()
            or path.name == "package-manifest.json"
            or "__pycache__" in path.parts
            or ".git" in path.parts
            or ".vibe-control" in path.parts
            or path.relative_to(root).as_posix() == "assets/project-control/runtime/release-receipt.json"
        ):
            continue
        files.append({
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        })
    try:
        loaded_assurance = json.loads((root / "references" / "controller-assurance-matrix.json").read_text(encoding="utf-8-sig"))
    except Exception:
        loaded_assurance = None
    assurance = loaded_assurance if isinstance(loaded_assurance, dict) else {}
    requirements = assurance.get("requirements", [])
    confirmed = assurance.get("confirmedControls", [])
    assurance_items = [*(requirements if isinstance(requirements, list) else []), *(confirmed if isinstance(confirmed, list) else [])]
    assurance_ids = {item.get("id") for item in assurance_items if isinstance(item, dict)}
    typed_items = [item for item in assurance_items if isinstance(item, dict)]
    validation = assurance_validation(root)
    implementation_ready = (
        isinstance(loaded_assurance, dict)
        and assurance.get("formalClaimsAllowed") is False
        and len(typed_items) == len(assurance_items)
        and REQUIRED_ASSURANCE_CONTROL_IDS.issubset(assurance_ids)
        and all(item.get("implementationStatus") == "IMPLEMENTED" for item in typed_items)
        and all(item.get("independentValidation") in {"PASS", "NOT_REQUIRED"} for item in typed_items)
        and validation == {"status": "PASS", "readiness": "CONTROL_IMPLEMENTATION_READY", "formalClaimsAllowed": False}
    )
    return {
        "packageId": "vibe-control",
        "version": (root / "VERSION").read_text(encoding="utf-8").strip(),
        # Development packages never derive formal posture from implementation
        # closure. A separate, candidate-bound package audit is required before
        # any future sealed release can change this value.
        "maturity": "DEVELOPMENT_DIAGNOSTIC",
        "assuranceValidation": validation,
        "hashAlgorithm": "sha256",
        "excludes": ["package-manifest.json", "**/__pycache__/**", ".git/**", ".vibe-control/**", "assets/project-control/runtime/release-receipt.json"],
        "files": files,
    }


def build_runtime_manifest(root: Path) -> dict:
    runtime = root / "assets" / "project-control" / "runtime"
    files = []
    for path in sorted(runtime.rglob("*")):
        if (
            not path.is_file()
            or path.name in {"runtime-manifest.json", "release-receipt.json"}
            or "__pycache__" in path.parts
        ):
            continue
        files.append({"path": path.relative_to(runtime).as_posix(), "bytes": path.stat().st_size, "sha256": sha256(path)})
    return {"schemaVersion": "3.2", "runtimeVersion": (root / "VERSION").read_text(encoding="utf-8").strip(), "hashAlgorithm": "sha256", "excludes": ["runtime-manifest.json", "release-receipt.json", "**/__pycache__/**"], "files": files}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    runtime_manifest_path = root / "assets" / "project-control" / "runtime" / "runtime-manifest.json"
    runtime_manifest = build_runtime_manifest(root)
    if not args.verify:
        write_json(runtime_manifest_path, runtime_manifest)
    elif not runtime_manifest_path.is_file():
        print(json.dumps({"status": "FAIL", "checkId": "RUNTIME-MANIFEST-MISSING", "reason": "runtime manifest missing"}))
        return 1
    elif (recorded_runtime := json.loads(runtime_manifest_path.read_text(encoding="utf-8-sig"))) != runtime_manifest:
        print(json.dumps({
            "status": "FAIL",
            "checkId": "RUNTIME-MANIFEST-VERIFY",
            "reason": "runtime manifest mismatch",
            "counters": manifest_diff(recorded_runtime, runtime_manifest),
        }, ensure_ascii=False))
        return 1
    manifest_path = root / "package-manifest.json"
    current = build(root)
    if args.verify:
        if not manifest_path.is_file():
            print(json.dumps({"status": "FAIL", "checkId": "PKG-MANIFEST-MISSING", "reason": "package manifest missing"}))
            return 1
        recorded = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        ok = recorded == current
        print(json.dumps({
            "status": "PASS" if ok else "FAIL",
            "checkId": "PKG-MANIFEST-VERIFY",
            "files": len(current["files"]),
            "counters": manifest_diff(recorded, current),
        }, ensure_ascii=False))
        return 0 if ok else 1
    write_json(manifest_path, current)
    print(json.dumps({"status": "CREATED", "path": str(manifest_path), "files": len(current["files"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
