from __future__ import annotations

import datetime as dt
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from . import VERSION

STATUSES = ("PASS", "BLOCKED", "FAIL", "INVALIDATED")
SCHEMA_VERSION = "4.0"
_PLAIN_LANGUAGE_ENGLISH_TERMS = re.compile(r"\b(?:schema|claim|commit|tree|hash)\b", re.IGNORECASE)
_PLAIN_LANGUAGE_ID_PREFIXES = re.compile(r"\b(?:HC|VC|CTRL|KO|KF|CP|CASE)-[A-Za-z0-9]", re.IGNORECASE)
_PLAIN_LANGUAGE_CHINESE_TERMS = (
    "哈希", "控制面", "声明等级", "候选提交", "目录树", "门禁", "审计",
    "运行时", "工作树", "执行器", "证据链", "架构",
)


def _plain_language_default(status: str) -> dict[str, str]:
    """Return a conservative, user-facing summary for every controller result.

    This projection deliberately avoids controller enums and check identifiers.
    Callers may replace individual fields when they can say something more
    specific without widening the proven scope.
    """
    return {
        "projectPurpose": "帮助用户让项目按已确认的目标持续推进，并随时看清当前结果。",
        "whatWasDone": "已完成当前步骤中能够安全完成的工作，并记录了结果。",
        "whatWorksNow": "目前只能确认这次实际查看和运行过的部分。",
        "whatStillDoesNotWork": "没有实际运行或仍有问题的部分，还不能算作完成。",
        "userImpact": "你可以根据这些结果决定继续、修正问题或亲自查看，但不要把它扩大到未覆盖的部分。",
        "canContinue": "可以继续原范围内的工作。" if status == "PASS" else "需要先处理当前问题，再继续推进。",
        "canRelease": "现在不能作为最终版本交付。",
    }


class ControlError(Exception):
    def __init__(self, check_id: str, message: str, *, status: str = "FAIL", details: Any = None):
        super().__init__(message)
        self.check_id = check_id
        self.message = message
        self.status = status
        self.details = details


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ControlError("VC-FILE-MISSING", f"required file is missing: {path}", status="BLOCKED") from exc
    except json.JSONDecodeError as exc:
        raise ControlError("VC-JSON-MALFORMED", f"invalid JSON in {path}: line {exc.lineno}, column {exc.colno}") from exc


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def check(check_id: str, status: str, message: str, **details: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"id": check_id, "status": status, "message": message}
    if details:
        item["details"] = details
    return item


def envelope(*, status: str, checks: list[dict[str, Any]] | None = None,
             formal: dict[str, Any] | None = None, state: Any = None,
             data: Any = None, error: Any = None,
             plain_language: dict[str, Any] | None = None) -> dict[str, Any]:
    checks = checks or []
    plain = _plain_language_default(status)
    if plain_language is not None:
        if not isinstance(plain_language, dict):
            raise ControlError("HC-PLAIN-LANGUAGE", "plainLanguage override must be an object")
        unknown = sorted(set(plain_language) - set(plain))
        if unknown:
            raise ControlError("HC-PLAIN-LANGUAGE", "plainLanguage override contains unknown fields", details=unknown)
        plain.update(plain_language)
    if any(not isinstance(value, str) or not value.strip() for value in plain.values()):
        raise ControlError("HC-PLAIN-LANGUAGE", "all seven plainLanguage fields must be nonempty text")
    leaked = sorted({
        term
        for value in plain.values()
        for term in _PLAIN_LANGUAGE_CHINESE_TERMS
        if term in value
    })
    english_leak = next((_PLAIN_LANGUAGE_ENGLISH_TERMS.search(value) for value in plain.values() if _PLAIN_LANGUAGE_ENGLISH_TERMS.search(value)), None)
    id_leak = next((_PLAIN_LANGUAGE_ID_PREFIXES.search(value) for value in plain.values() if _PLAIN_LANGUAGE_ID_PREFIXES.search(value)), None)
    if leaked or english_leak or id_leak:
        raise ControlError("HC-PLAIN-LANGUAGE", "plainLanguage must describe user-visible function and consequence without internal control terminology", details={"chineseTerms": leaked, "englishTerm": english_leak.group(0) if english_leak else None, "idPrefix": id_leak.group(0) if id_leak else None})
    result: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "runtimeVersion": VERSION,
        "status": status,
        "checkedAt": now_iso(),
        "integrity": {
            "status": status if status in {"FAIL", "INVALIDATED"} else ("BLOCKED" if any(c["status"] == "BLOCKED" for c in checks) else "PASS"),
            "checks": checks,
            "counts": {name: sum(1 for item in checks if item.get("status") == name) for name in STATUSES},
        },
        "formal": formal or {"eligible": False, "maxClaimLevel": "DIAGNOSTIC", "blockers": []},
        "state": state,
    }
    if data is not None:
        result["data"] = data
    if error is not None:
        result["error"] = error
    # Keep the zero-context projection last so streaming and simple readers can
    # reliably finish on the user-facing consequence rather than an internal
    # object or diagnostic identifier.
    result["plainLanguage"] = plain
    return result


def error_envelope(exc: ControlError) -> dict[str, Any]:
    return envelope(
        status=exc.status,
        checks=[check(exc.check_id, exc.status, exc.message, details=exc.details) if exc.details is not None else check(exc.check_id, exc.status, exc.message)],
        formal={"eligible": False, "maxClaimLevel": "DIAGNOSTIC", "blockers": [exc.check_id]},
        error={"id": exc.check_id, "message": exc.message, "details": exc.details},
    )


def exit_code(status: str) -> int:
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 3, "INVALIDATED": 4}.get(status, 3)


def git(root: Path, *args: str, required: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if required and result.returncode != 0:
        raise ControlError("VC-GIT-ERROR", result.stderr.strip() or "Git command failed", status="BLOCKED")
    return result.stdout.strip() if result.returncode == 0 else ""


def git_root(project: Path) -> Path:
    value = git(project, "rev-parse", "--show-toplevel", required=False)
    if not value:
        raise ControlError("VC-GIT-REQUIRED", "a Git repository is required", status="BLOCKED")
    return Path(value).resolve()


def clean_status(root: Path) -> list[str]:
    return [line for line in git(root, "status", "--porcelain=v1").splitlines() if line]


def safe_relative(root: Path, value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ControlError("HC-PATH-SAFETY", f"unsafe managed path: {value}")
    unresolved = root / candidate
    cursor = root.resolve()
    for part in candidate.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ControlError("HC-PATH-SYMLINK", f"symbolic links are not accepted: {value}")
    resolved = unresolved.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ControlError("HC-PATH-SAFETY", f"managed path escapes project: {value}") from exc
    return resolved


def file_ref(root: Path, path: Path, *, tracked: bool = True) -> dict[str, Any]:
    root = root.resolve()
    if ".." in path.parts:
        raise ControlError("HC-PATH-SAFETY", f"managed path contains traversal: {path}")
    candidate = path if path.is_absolute() else root / path
    try:
        lexical_relative = candidate.absolute().relative_to(root)
    except ValueError as exc:
        raise ControlError("HC-PATH-SAFETY", f"managed path is outside project: {path}") from exc
    cursor = root
    for part in lexical_relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ControlError("HC-PATH-SYMLINK", f"symbolic links are not accepted: {path}")
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ControlError("HC-PATH-SAFETY", f"managed path escapes project: {path}") from exc
    if not resolved.is_file():
        raise ControlError("HC-FILE-MISSING", f"required file is missing: {relative}", status="BLOCKED")
    if tracked and not git(root, "ls-files", "--error-unmatch", "--", relative, required=False):
        raise ControlError("HC-FILE-TRACKED", f"file is not tracked: {relative}")
    return {"path": relative, "bytes": resolved.stat().st_size, "sha256": sha256_file(resolved), "tracked": tracked}


def verify_ref(root: Path, ref: dict[str, Any], check_id: str) -> dict[str, Any]:
    try:
        path = safe_relative(root, ref.get("path", ""))
        if not path.is_file():
            return check(check_id, "INVALIDATED", "referenced file is missing", path=ref.get("path"))
        actual = sha256_file(path)
        if actual != ref.get("sha256") or path.stat().st_size != ref.get("bytes"):
            return check(check_id, "INVALIDATED", "referenced file hash or size drifted", path=ref.get("path"), actualSha256=actual)
        if ref.get("tracked") and not git(root, "ls-files", "--error-unmatch", "--", ref["path"], required=False):
            return check(check_id, "FAIL", "referenced file is not tracked", path=ref.get("path"))
        return check(check_id, "PASS", "reference is content-bound", path=ref.get("path"))
    except ControlError as exc:
        return check(check_id, exc.status, exc.message)


def verify_dependencies(runtime_root: Path) -> list[dict[str, Any]]:
    lock_path = runtime_root / "dependency-lock.json"
    lock = load_json(lock_path)
    checks: list[dict[str, Any]] = []
    py_ok = (3, 12) <= tuple(__import__("sys").version_info[:2]) < (3, 13)
    checks.append(check("HC-DEPENDENCY-PYTHON", "PASS" if py_ok else "BLOCKED", "Python version is locked" if py_ok else "Python 3.12.x is required"))
    for name, expected in lock.get("packages", {}).items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        ok = actual == expected
        checks.append(check(f"HC-DEPENDENCY-{name.upper().replace('-', '_')}", "PASS" if ok else "BLOCKED", "dependency version matches lock" if ok else "dependency missing or version mismatch", package=name, expected=expected, actual=actual))
    return checks
