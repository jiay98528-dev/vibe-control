#!/usr/bin/env python3
"""Build or verify vibe-control/package-manifest.json."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from public_report import JsonArgumentError, JsonArgumentParser, emit, finalize


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
    "CTRL-CONFIRMED-031", "CTRL-CONFIRMED-032", "CTRL-CONFIRMED-033",
    "CTRL-CONFIRMED-034", "CTRL-CONFIRMED-035", "CTRL-CONFIRMED-036",
    "CTRL-CONFIRMED-037", "CTRL-CONFIRMED-038", "CTRL-CONFIRMED-039",
    "CTRL-CONFIRMED-040", "CTRL-CONFIRMED-041", "CTRL-CONFIRMED-042",
    "CTRL-CONFIRMED-043",
})


def plain_language(status: str) -> dict[str, str]:
    ok = status in {"PASS", "CREATED"}
    return {
        "projectPurpose": "确认这份开发工具的文件清单与实际内容保持一致。",
        "whatWasDone": "已重新读取工具文件并逐项核对清单记录。",
        "whatWorksNow": "工具内容和清单目前一致，可以继续后续核对。" if ok else "当前文件内容和清单记录不一致。",
        "whatStillDoesNotWork": "这项检查不代表工具或采用它的项目已经可以最终交付。" if ok else "清单缺失、内容变化或输入错误仍需处理。",
        "userImpact": "可以继续准备后续检查，但仍要完成项目本身的验收。" if ok else "如果继续使用当前清单，后续检查可能对应错误的文件内容。",
        "canContinue": "可以继续下一项核对。" if ok else "需要先更新或修复清单问题。",
        "canRelease": "这项结果不能单独证明工具或项目可以作为最终版本交付。",
    }


def public_report(value: dict) -> dict:
    return finalize(value, plain_language(str(value.get("status") or "FAIL")))


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
    return {"schemaVersion": "4.0", "runtimeVersion": (root / "VERSION").read_text(encoding="utf-8").strip(), "hashAlgorithm": "sha256", "excludes": ["runtime-manifest.json", "release-receipt.json", "**/__pycache__/**"], "files": files}


def main(argv: list[str] | None = None) -> int:
    try:
        parser = JsonArgumentParser()
        parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
        parser.add_argument("--verify", action="store_true")
        args = parser.parse_args(argv)
        root = Path(args.root).resolve()
        runtime_manifest_path = root / "assets" / "project-control" / "runtime" / "runtime-manifest.json"
        runtime_manifest = build_runtime_manifest(root)
        if not args.verify:
            write_json(runtime_manifest_path, runtime_manifest)
        elif not runtime_manifest_path.is_file():
            value = {"status": "FAIL", "checkId": "RUNTIME-MANIFEST-MISSING", "reason": "runtime manifest missing"}
            code = 1
            emit(public_report(value))
            return code
        elif (recorded_runtime := json.loads(runtime_manifest_path.read_text(encoding="utf-8-sig"))) != runtime_manifest:
            value = {
                "status": "FAIL",
                "checkId": "RUNTIME-MANIFEST-VERIFY",
                "reason": "runtime manifest mismatch",
                "counters": manifest_diff(recorded_runtime, runtime_manifest),
            }
            emit(public_report(value))
            return 1
        manifest_path = root / "package-manifest.json"
        current = build(root)
        if args.verify:
            if not manifest_path.is_file():
                value = {"status": "FAIL", "checkId": "PKG-MANIFEST-MISSING", "reason": "package manifest missing"}
                code = 1
            else:
                recorded = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
                ok = recorded == current
                value = {
                    "status": "PASS" if ok else "FAIL",
                    "checkId": "PKG-MANIFEST-VERIFY",
                    "files": len(current["files"]),
                    "counters": manifest_diff(recorded, current),
                }
                code = 0 if ok else 1
        else:
            write_json(manifest_path, current)
            value = {"status": "CREATED", "path": str(manifest_path), "files": len(current["files"])}
            code = 0
    except JsonArgumentError as exc:
        value = {"status": "FAIL", "checkId": "MANIFEST-INVALID-ARGUMENTS", "reason": str(exc)}
        code = 3
    except Exception as exc:
        value = {
            "status": "FAIL",
            "checkId": "MANIFEST-INPUT-ERROR",
            "reason": "manifest input could not be read or processed",
            "details": {"errorType": type(exc).__name__, "error": str(exc)},
        }
        code = 1
    emit(public_report(value))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
