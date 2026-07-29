from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .common import ControlError, envelope, error_envelope, exit_code, write_json_atomic
from .automation_control import automation_action, automation_apply, automation_plan
from .controller import (
    accept, audit, bootstrap, execute, freeze, handoff, ingest, inspect,
    lock_task, migration_apply, migration_plan, release_check, reposition_apply,
    reposition_plan, resolve_rules, revise_objectives_apply, revise_objectives_plan, validate,
)
from .dashboard import generate_dashboard
from .upgrade_control import upgrade_apply, upgrade_plan


class JsonParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ControlError("CLI-INVALID-ARGUMENTS", message)


def parser() -> JsonParser:
    root = JsonParser(prog="vibe-control", description="vibe-control deterministic runtime 3.2", add_help=True)
    sub = root.add_subparsers(dest="command", required=True, parser_class=JsonParser)
    for name in ("inspect", "validate", "release-check"):
        item = sub.add_parser(name); item.add_argument("--project", default="."); item.add_argument("--output")
    item = sub.add_parser("bootstrap"); item.add_argument("--project", default="."); item.add_argument("--spec", required=True); item.add_argument("--output")
    item = sub.add_parser("resolve-rules"); item.add_argument("--project", default="."); item.add_argument("--spec", required=True); item.add_argument("--output")
    item = sub.add_parser("reposition"); item.add_argument("--project", default="."); item.add_argument("--spec", required=True); mode=item.add_mutually_exclusive_group(required=True); mode.add_argument("--plan", action="store_true"); mode.add_argument("--apply", metavar="PLAN_HASH"); item.add_argument("--output")
    item = sub.add_parser("revise-objectives"); item.add_argument("--project", default="."); item.add_argument("--spec", required=True); mode=item.add_mutually_exclusive_group(required=True); mode.add_argument("--plan", action="store_true"); mode.add_argument("--apply", metavar="PLAN_HASH"); item.add_argument("--output")
    item = sub.add_parser("lock-task"); item.add_argument("--project", default="."); item.add_argument("--contract", required=True); item.add_argument("--output")
    item = sub.add_parser("freeze"); item.add_argument("--project", default="."); item.add_argument("--contract"); item.add_argument("--actor", required=True); item.add_argument("--session", required=True); item.add_argument("--output")
    item = sub.add_parser("execute"); item.add_argument("--project", default="."); item.add_argument("--actor", required=True); item.add_argument("--session", required=True); item.add_argument("--case", action="append"); item.add_argument("--output")
    item = sub.add_parser("ingest"); item.add_argument("--project", default="."); item.add_argument("--attestation", required=True); item.add_argument("--output")
    item = sub.add_parser("audit"); item.add_argument("--project", default="."); item.add_argument("--review", required=True); item.add_argument("--output")
    item = sub.add_parser("accept"); item.add_argument("--project", default="."); item.add_argument("--decision", required=True); item.add_argument("--output")
    item = sub.add_parser("handoff"); item.add_argument("--project", default="."); item.add_argument("--handoff-output"); item.add_argument("--output")
    item = sub.add_parser("automation"); item.add_argument("--project", default="."); item.add_argument("--spec"); mode=item.add_mutually_exclusive_group(required=True); mode.add_argument("--plan", action="store_true"); mode.add_argument("--apply", metavar="PLAN_HASH"); mode.add_argument("--action", choices=("dispatch", "continue", "commit", "push")); item.add_argument("--message"); item.add_argument("--output")
    item = sub.add_parser("dashboard"); item.add_argument("--project", default="."); item.add_argument("--output-dir"); item.add_argument("--output")
    item = sub.add_parser("migrate"); item.add_argument("--project", default="."); mode=item.add_mutually_exclusive_group(required=True); mode.add_argument("--plan", action="store_true"); mode.add_argument("--apply", metavar="PLAN_HASH"); item.add_argument("--spec"); item.add_argument("--output")
    item = sub.add_parser("upgrade"); item.add_argument("--project", default="."); mode=item.add_mutually_exclusive_group(required=True); mode.add_argument("--plan", action="store_true"); mode.add_argument("--apply", metavar="PLAN_HASH"); item.add_argument("--spec"); item.add_argument("--output")
    item = sub.add_parser("risk"); item.add_argument("--score", required=True, type=int); item.add_argument("--forced-r3", action="store_true"); item.add_argument("--output")
    return root


def risk(score: int, forced: bool) -> dict[str, Any]:
    if not 0 <= score <= 100: raise ControlError("HC-RISK-RANGE", "risk score must be between 0 and 100")
    level = "R3" if forced or score >= 70 else ("R2" if score >= 35 else "R1")
    return envelope(status="PASS", data={"score": score, "level": level, "forcedR3": forced})


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    project = Path(getattr(args, "project", "."))
    if args.command == "inspect": return inspect(project)
    if args.command == "bootstrap": return bootstrap(project, Path(args.spec))
    if args.command == "resolve-rules": return resolve_rules(project, Path(args.spec))
    if args.command == "reposition": return reposition_plan(project, Path(args.spec)) if args.plan else reposition_apply(project, Path(args.spec), args.apply)
    if args.command == "revise-objectives": return revise_objectives_plan(project, Path(args.spec)) if args.plan else revise_objectives_apply(project, Path(args.spec), args.apply)
    if args.command == "lock-task": return lock_task(project, Path(args.contract))
    if args.command == "validate": return validate(project)
    if args.command == "freeze": return freeze(project, args.actor, args.session, Path(args.contract) if args.contract else None)
    if args.command == "execute": return execute(project, args.actor, args.session, args.case)
    if args.command == "ingest": return ingest(project, Path(args.attestation))
    if args.command == "audit": return audit(project, Path(args.review))
    if args.command == "accept": return accept(project, Path(args.decision))
    if args.command == "release-check": return release_check(project)
    if args.command == "handoff": return handoff(project, Path(args.handoff_output) if args.handoff_output else None)
    if args.command == "automation":
        if args.action:
            if args.spec:
                raise ControlError("CLI-INVALID-ARGUMENTS", "automation --action does not accept --spec")
            if args.message is not None and args.action != "commit":
                raise ControlError("CLI-INVALID-ARGUMENTS", "automation --message is only valid with --action commit")
            return automation_action(project, args.action, args.message)
        if args.message is not None:
            raise ControlError("CLI-INVALID-ARGUMENTS", "automation --message is only valid with --action commit")
        if not args.spec:
            raise ControlError("CLI-INVALID-ARGUMENTS", "automation --plan/--apply requires --spec")
        return automation_plan(project, Path(args.spec)) if args.plan else automation_apply(project, Path(args.spec), args.apply)
    if args.command == "dashboard": return generate_dashboard(project, Path(args.output_dir) if args.output_dir else None)
    if args.command == "migrate":
        if args.apply and not args.spec:
            raise ControlError("CLI-INVALID-ARGUMENTS", "migrate --apply requires --spec")
        return migration_plan(project, Path(args.spec) if args.spec else None) if args.plan else migration_apply(project, args.apply, Path(args.spec))
    if args.command == "upgrade":
        if args.apply and not args.spec:
            raise ControlError("CLI-INVALID-ARGUMENTS", "upgrade --apply requires --spec")
        return upgrade_plan(project, Path(args.spec) if args.spec else None) if args.plan else upgrade_apply(project, args.apply, Path(args.spec))
    if args.command == "risk": return risk(args.score, args.forced_r3)
    raise ControlError("CLI-UNKNOWN-COMMAND", f"unknown command: {args.command}")


def emit(value: dict[str, Any], output: str | None = None) -> None:
    if output: write_json_atomic(Path(output).resolve(), value)
    sys.stdout.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main() -> int:
    try:
        args = parser().parse_args()
        result = dispatch(args)
    except ControlError as exc:
        result = error_envelope(exc)
    except Exception as exc:  # stable fail-closed surface; never expose traceback
        result = error_envelope(ControlError("CLI-INTERNAL-ERROR", f"internal error: {type(exc).__name__}"))
    emit(result, getattr(locals().get("args", None), "output", None))
    return exit_code(result["status"])
