---
name: vibe-control
description: Establish, adopt, resume, automatically advance, observe, validate, and adversarially assure a project-local VibeCoding control plane. Use implicitly only for unmistakable whole-project creation; use explicitly for bootstrap/adoption, key objectives, task checkpoints, persistent progress dashboards, Team/SubAgent routing, candidate/evidence integrity, audit, handoff, drift recovery, or development of a governance controller, gate, Schema, validator, or claim protocol. Do not trigger implicitly for an isolated feature, bug fix, explanation, ordinary review, or general discussion.
---

# Vibe Control

Create an observable control plane before broad implementation. Keep authorization, candidate identity, evidence and claims strict; keep architecture, work breakdown, stage order and implementation strategy flexible inside the locked boundary.

## Core boundary

- Reread project-root `KEY_OBJECTIVES.md` before accepting a blocker, planning a repair, changing architecture/cases/oracles, accepting, sealing, or handing off. Follow [key-objectives.md](references/key-objectives.md).
- Hard-check only deterministic minimum facts: safe paths, hashes, tracked identity, reference closure, candidate commit/tree, clean state, checkpoint/case provenance, nonzero conserved counters, zero skip, transcript/artifact binding, role/permission boundaries, claim ceilings and invalidation. Follow [evidence-policy.md](references/evidence-policy.md).
- Never infer product, design, security, performance or release PASS from a valid control plane. Warnings, ordinary process improvements and threat models outside the locked boundary are not blockers.
- Before project writes, explicitly obtain `releaseIntent = LOCAL_EXPERIMENT | PRIVATE_OPERATION | EXTERNAL_RELEASE`; do not infer it. This is a product delivery boundary, not Skill installation, licensing or Git tagging.
- Skill installation and local automatic development do not require or create private keys. Do not introduce licensing or distribution credentials into this workflow.
- Version `0.4.0` uses Schema 4.0 and remains `DEVELOPMENT_DIAGNOSTIC`, `formalClaimsAllowed=false`, capped at `DEVELOPMENT_CHECKED`. Installation, Dashboard output or self-tests never grant package sealing.
- When changing a controller, gate, Schema, evidence or claim protocol, read [controller-assurance.md](references/controller-assurance.md), [incident-2026-07-25.md](references/incident-2026-07-25.md), and the [0.4.0 requirements](references/0.4.0-requirements.md).

## Route

| Intent | Route | Read |
| --- | --- | --- |
| Whole new project | `bootstrap` | [bootstrap.md](references/bootstrap.md), [project-positioning.md](references/project-positioning.md), [progress-dashboard.md](references/progress-dashboard.md) |
| Adopt or migrate an existing project | `adopt` | [adoption.md](references/adoption.md), [schema-guide.md](references/schema-guide.md), [progress-dashboard.md](references/progress-dashboard.md) |
| Start/resume/advance work | `start` / `resume` / `advance` | [task-control.md](references/task-control.md), [automation-advancement.md](references/automation-advancement.md), [execution-routing.md](references/execution-routing.md) |
| Route Team/SubAgent/model contexts | `configure-models` | [multi-session-routing.md](references/multi-session-routing.md), [model-routing.md](references/model-routing.md) |
| Freeze, execute, audit, accept or hand off | corresponding CLI | [checkpoint-contract.md](references/checkpoint-contract.md), [evidence-policy.md](references/evidence-policy.md) |
| Ask an Owner question or report a stop | human decision | [human-decisions.md](references/human-decisions.md), [progress-dashboard.md](references/progress-dashboard.md) |
| Understand machine objects | machine-state operation | [schema-guide.md](references/schema-guide.md) |

Read only the selected route's references; all references above are one level from this file.

## Entry protocol

1. State that `$vibe-control` is active and whether the next action is read-only or writes local/project state.
2. Before inspecting, asking a boundary question, or writing the project, initialize the external-cache progress ledger and Dashboard. This local temporary history is manually clearable and never evidence. See [progress-dashboard.md](references/progress-dashboard.md).
3. Resolve the governance unit, then run `inspect`. If multiple independently delivered products exist, ask one boundary question.
4. If governed, read the governance lock, locked objectives, state and only its current task/candidate chain. Do not repair machine contradictions through narrative.
5. If unmanaged, persist requirement facts, derive and once confirm `KEY_OBJECTIVES.md`, confirm positioning and release intent, derive checkpoints, then bootstrap. Ask only boundary-changing questions.
6. Materialize the safe automation default: `AUTO_LOCAL_TO_REVIEW / MILESTONE_COMMITS / NONE`. Do not ask whether to enable it. Manual mode is user-requested; push needs explicit, content-bound upstream authorization.
7. Generate and lock milestones, four-domain scorecard items, checkpoint expectations, `verificationStrategy`, `guardPolicy`, and `reportingPolicy` before implementation.

## Default automatic work

`advance` is a Skill workflow, not a CLI. It runs:

```text
local Dashboard → plan/action map → dispatch/implement → narrow developer checks
→ freeze candidate → independent execute → fresh audit → Dashboard → Owner review
```

- Resolve the primary backend from callable tools as `TEAM → SUBAGENT → SERIAL`. Persistent task/thread/AgentTeam providers map to `TEAM`; within Team, use persistent members for sustained ownership and SubAgents for narrow one-off work when both are available. Ask once only when the host itself requires explicit Team creation authorization; otherwise use Team by default. If declined/unavailable, downgrade without blocking. See [multi-session-routing.md](references/multi-session-routing.md).
- The Skill sets no numeric worker/subagent limit. Respect host capacity, worktree isolation and disjoint file ownership.
- Coordinator alone writes the control plane, progress ledger, candidate, integration commits and user report. Implementer, Executor and Auditor cannot approve themselves.
- Implementer receives a lightweight run-card and runs only change-relevant quick checks. Full cases and formal gates run after freeze in Executor/Auditor contexts; do not blindly run deep suites for more PASS counts. See [execution-routing.md](references/execution-routing.md).
- Ordinary node completion only updates the ledger/Dashboard and sends a non-blocking progress note. Do not ask for stage approval.
- Stop for Owner review, any HUMAN checkpoint, objective/scope/case/oracle/risk/release/permission change, R3 or irreversible work, action guard, unrecoverable environment block, push conflict, repeated no-progress failure, or user interruption.
- Default automation never pushes, merges, rebases, creates remotes/PRs/tags/releases, installs missing dependencies/Skills, expands permissions, accepts, or approves a HUMAN checkpoint.

## Strong boundaries, weak process

Classify impact before stopping:

- `ACTION_GUARD`: path/permission escape, R3, irreversible action or remote conflict; stop the action.
- `CLAIM_GUARD`: failed/missing/drifted evidence, skip, zero execution or counter mismatch; block the claim but continue in-contract diagnosis/repair.
- `HUMAN_DECISION`: product direction, subjective quality, scope or authorization; stop for Owner.
- `ENVIRONMENT_BLOCKED`: unavailable environment; report the recovery condition, not product failure.
- `ADVISORY`: style, ordinary process suggestions, future hardening or out-of-scope exploration; record without blocking.

Fixed stage sequences, risk-to-review mappings, audit counts and generic quality gates are heuristics. Derive the actual `verificationStrategy` from current objectives, checkpoints, Profile/adapter proof boundary and minimum core. After checkpoints close, Auditor stops; new ideas enter later work unless they hit current goals, minimum core or an authorization boundary.

## Reports and Dashboard

At every node, Coordinator atomically updates the local ledger. At every stop, regenerate `index.html`, `status.json`, and `summary.md` from the same read-only projection. Dashboard changes cannot modify state/evidence or grant a claim.

Display four independently reproducible ratios—functionality, robustness/security, audit, process—and the fixed `40/25/20/15` delivery-readiness aggregate. Unknown items do not count as complete; before a locked denominator, display `N/A`. State that the score is not a remaining-time estimate.

Every execution, gate and audit report must end with:

```text
给没有开发背景的人看的说明
```

Structured output carries the same seven sentences in the final `plainLanguage` object.

Explain only what the product does, what changed, what works, what does not, user impact, whether work can continue and whether it can be released. Do not use control IDs, hashes, Schema, claim, commit/tree or unexplained engineering terms in that section.

At a real stop, offer `RECOMMENDED`, `ALTERNATIVE`, and `OPEN`: two concrete actions with consequences plus free input. If a structured question tool is callable, use it; only fall back to numbered text when no such tool exists. Never interrupt ordinary background nodes with this question.

## Deterministic commands

Normal installation self-check:

The self-check supports a Git root, a tracked repository subdirectory, and a `PORTABLE_COPY`; none of these development installation forms grants final delivery status.

```text
python <skill-root>/scripts/validate_installation.py --skill-root <skill-root>
```

Global wrapper:

```text
python <skill-root>/scripts/vibe_control.py progress --project <root> --action init|update|stop|clear ...
python <skill-root>/scripts/vibe_control.py dashboard --project <root> [--output-dir <external-path>]
python <skill-root>/scripts/vibe_control.py inspect --project <root>
python <skill-root>/scripts/vibe_control.py resolve-rules --project <root> --spec <spec.json>
python <skill-root>/scripts/vibe_control.py bootstrap --project <root> --spec <spec.json>
python <skill-root>/scripts/vibe_control.py automation --project <root> --spec <policy.json> --plan
python <skill-root>/scripts/vibe_control.py automation --project <root> --spec <policy.json> --apply <plan-hash>
python <skill-root>/scripts/vibe_control.py automation --project <root> --action dispatch|continue|commit|push
python <skill-root>/scripts/vibe_control.py reposition|revise-objectives|migrate|upgrade ...
python <skill-root>/scripts/vibe_control.py risk --score <0-100> [--forced-r3]
```

Pinned project controller:

```text
python .vibe-control/runtime/0.4.0/control.py lock-task --project . --contract <contract.json>
python .vibe-control/runtime/0.4.0/control.py freeze --project . --actor <actor> --session <session>
python .vibe-control/runtime/0.4.0/control.py execute --project . --actor <actor> --session <session> [--case <case-id>]
python .vibe-control/runtime/0.4.0/control.py ingest --project . --attestation <attestation.json>
python .vibe-control/runtime/0.4.0/control.py validate --project .
python .vibe-control/runtime/0.4.0/control.py audit --project . --review <review.json>
python .vibe-control/runtime/0.4.0/control.py accept --project . --decision <decision.json>
python .vibe-control/runtime/0.4.0/control.py release-check --project .
python .vibe-control/runtime/0.4.0/control.py handoff --project .
```

Exit codes remain `0` completed, `2` prerequisite blocked, `3` deterministic failure/invalid input, `4` invalidated. A zero exit is not a product-quality judgment.

Only package maintainers run `validate_package_release.py` and the bounded deep assurance suites. Matrix `CONTROL_IMPLEMENTATION_READY` is internal traceability; a development package is never `FORMAL_GATE_READY`.

## Completion discipline

- Report one current project purpose, task, product consequence, derived state, candidate/change scope, four-domain scorecard, case/failure/skip/timeout totals, blockers, evidence boundary and next Owner decision.
- Say “本轮覆盖内未发现 …”; never claim unknown defects do not exist.
- End with the plain-language section and three next-step entries at true stops.
- Preserve project/user changes. Never use narrative to waive a hard failure or expand automatic authority.
