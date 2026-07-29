---
name: vibe-control
description: Establish, adopt, resume, validate, and adversarially assure an observable control plane for VibeCoding projects. Use implicitly only when the user clearly asks to create a complete new software product, game, service, application, or repository; use explicitly for project bootstrap, governance adoption, task control, candidate freeze, evidence integrity, multi-agent routing, audit, handoff, recovery from agent/process drift, or development and review of a governance controller, validator, gate, state machine, evidence protocol, or release-claim mechanism. Do not trigger implicitly for an isolated feature, bug fix, code explanation, ordinary review, or general discussion.
---

# Vibe Control

Build a project-local, version-pinned control plane before broad implementation. Keep authorization, evidence, candidate identity, and claims strict; keep architecture and implementation strategy flexible inside the confirmed boundary.

## Non-negotiable boundary

- Treat scripts as integrity controllers, not product-quality judges.
- Hard checks cover only deterministic facts: schema/state legality, safe paths, hashes, tracked files, reference closure, candidate commit/tree, clean state, transcript/artifact binding, required-case coverage, nonzero execution, zero skip, counter conservation, claim ceilings, and hash-driven invalidation.
- A hard-check failure may block a claim or transition. It must not block diagnosis or further implementation.
- Never waive a hard failure. Fix, rerun, reduce the claim, or retain `BLOCKED`, `FAIL`, or `INVALIDATED`.
- Emit semantic conflicts, stale status, suspicious co-change, independence questions, and product-quality concerns as `warning`, `investigation`, or `human-decision`.
- Never infer product, design, security, performance, or release PASS from a structurally valid control plane.
- Only the responsible agent/context writes governance state or makes the final claim. Workers and auditors cannot approve themselves.
- After requirement facts are written, derive project-root `KEY_OBJECTIVES.md`, obtain one consolidated confirmation, and lock it before task planning. Before accepting a blocker, planning a repair, changing architecture/cases/oracles, accepting, sealing, or handing off, reread that document. See [key-objectives.md](references/key-objectives.md).
- After positioning is confirmed, convert every locked success signal into exactly one candidate-bound acceptance checkpoint. An `ACCEPTED`-capable task must also map every human quality gate exactly once. Show the normalized checkpoint summary once, obtain one confirmation, and bind its hash before `lock-task`. See [checkpoint-contract.md](references/checkpoint-contract.md).
- Audit the preset checkpoints first under `CONFORMANCE_PLUS_BOUNDED_EXPLORATION`. Once `stopCondition=ALL_REQUIRED_CHECKPOINTS_REPORTED` is reached, stop the conformance audit; permit at most three ordinary exploratory findings per candidate. A fourth creates a candidate-bound `audit-closure`, so a failed review cannot reset the budget by changing session or finding IDs. Current-goal, minimum-core, and locked-safety findings remain outside that numerical budget but must satisfy their own task/claim admission rules.
- Before creating a project control plane, ask the user for that **project's expected release intent**. Do not infer it from project size, user count, repository name, or a template; do not write bootstrap files until one intent is explicitly selected.
- `releaseIntent` describes the product's delivery boundary, never installation, licensing, payment, public distribution, or Git tagging of this Skill. `LOCAL_EXPERIMENT` caps at `VERIFIED`; `PRIVATE_OPERATION` caps at `ACCEPTED`; only `EXTERNAL_RELEASE` can use the `RELEASE_READY` path.
- Candidate-bound R2 review and owner decisions are tracked human attestations, not cryptographic licensing. Ed25519 public-key verification, the external release audit, and the receipt are required only for an actual `EXTERNAL_RELEASE` R3 task. A private key is never required to install, use, version, or locally tag this Skill, and it must remain outside the Skill and project control directory.
- Version `0.3.6` is installed in `DEVELOPMENT_DIAGNOSTIC` mode while Schema 3.2 enforcement, automation-policy 1.0 and orchestration compatibility remain unsealed. Its package manifest and assurance matrix can never self-grant `FORMAL_GATE_READY`; `formalClaimsAllowed` remains false and an unsealed package is capped at `DEVELOPMENT_CHECKED`. Diagnostic development remains available, but formal acceptance, release readiness, and package sealing are fail-closed until an exact future candidate completes independent package-audit closure.
- Browser WebGL gameplay uses the separate `browser-webgl-game-runtime` proof boundary. It activates only for confirmed `GAMEPLAY` positioning with an explicit WebGL runtime target, requires a locked Playwright command and candidate-bound artifacts, and never proves a native shell, target hardware, game feel, human approval, or release readiness.
- A new project must explicitly select `MANUAL_STAGE_CONFIRMATION`, `AUTO_LOCAL_TO_REVIEW`, or `AUTO_PUSH_TO_REVIEW` in the consolidated startup confirmation. An unanswered automation choice blocks bootstrap. A legacy Schema 3.2 project without an automation policy remains manual-compatible and gains no automatic side-effect authority until a content-bound `automation --plan` / `--apply` opt-in completes. See [automation-advancement.md](references/automation-advancement.md).
- A Schema 3.1 project may use the content-bound `migrate --plan [--spec]` / `--apply <plan-hash> --spec` path. Migration archives the complete old control plane, invalidates every downstream fact, and returns to `DRAFT / BLOCKED / DIAGNOSTIC`; it never rebinds old evidence. Schema 2.0 remains pinned to runtime 0.2.2 and returns `VC-REINSTALL-REQUIRED` instead of being converted.
- On an external R3 **project** release path, `executor`、`auditor`、`release-auditor`、`owner` must use distinct actors and public keys, and the release-auditor must differ from the internal review auditor. This project-level signed chain is separate from the Skill package audit tags. The controller executes local cases in a temporary Git worktree at the candidate commit, never from the caller's worktree. Absence, drift, a non-PASS review/audit, credential reuse, or invalid required signatures blocks that external project release claim.
- When modifying a controller, validator, gate, Schema or claim protocol, read [controller-assurance.md](references/controller-assurance.md) and [incident-2026-07-25.md](references/incident-2026-07-25.md). A happy-path fixture or manifest PASS cannot close a public hard claim.

## Route the request

| Intent | Route | Required reference |
| --- | --- | --- |
| Create a complete new project | `resolve-rules` → `bootstrap` | [bootstrap.md](references/bootstrap.md), [project-positioning.md](references/project-positioning.md), [task-control.md](references/task-control.md), [human-decisions.md](references/human-decisions.md), [profiles.md](references/profiles.md) |
| Add governance to an existing project | `adopt` | [adoption.md](references/adoption.md), [human-decisions.md](references/human-decisions.md) |
| Start or resume a controlled task | `start` / `resume` | [checkpoint-contract.md](references/checkpoint-contract.md), [task-control.md](references/task-control.md), [multi-session-routing.md](references/multi-session-routing.md) |
| Automatically advance to the next owner review | `advance` | [automation-advancement.md](references/automation-advancement.md), [multi-session-routing.md](references/multi-session-routing.md), [human-decisions.md](references/human-decisions.md) |
| Configure automation or render a review snapshot | `automation` / `dashboard` | [automation-advancement.md](references/automation-advancement.md), [human-decisions.md](references/human-decisions.md) |
| Configure models | `configure-models` | [model-routing.md](references/model-routing.md), [human-decisions.md](references/human-decisions.md) |
| Freeze, execute, audit, accept, release-check, or hand off | corresponding CLI command | [evidence-policy.md](references/evidence-policy.md), [task-control.md](references/task-control.md) |
| Change a Schema 3.2 project milestone/environment | `reposition` | [project-positioning.md](references/project-positioning.md), [evidence-policy.md](references/evidence-policy.md) |
| Change confirmed objectives | `revise-objectives` | [key-objectives.md](references/key-objectives.md), [evidence-policy.md](references/evidence-policy.md) |
| Encounter a Schema 3.1 control plane | `migrate --plan` → confirmed spec → `--apply` | [adoption.md](references/adoption.md), [checkpoint-contract.md](references/checkpoint-contract.md), [evidence-policy.md](references/evidence-policy.md) |
| Encounter a Schema 2.0 control plane | fresh-bootstrap proposal only | [adoption.md](references/adoption.md), [evidence-policy.md](references/evidence-policy.md) |
| Build, repair, or audit governance enforcement | `assure-controller` | [controller-assurance.md](references/controller-assurance.md), [incident-2026-07-25.md](references/incident-2026-07-25.md), [controller-assurance-matrix.json](references/controller-assurance-matrix.json) |
| Understand machine objects | any machine-state operation | [schema-guide.md](references/schema-guide.md) |

Read only the references needed for the selected route. Do not load every reference by default.

## Common entry protocol

1. State that `$vibe-control` is being used and whether the current action is read-only or will pause for approval.
2. Resolve the governance unit. Use the Git root when it is the only product; if a monorepo contains multiple independently released products, list candidates and ask one boundary question.
3. Run read-only inspection:

   `python <skill-root>/scripts/vibe_control.py inspect --project <root>`

4. If `.vibe-control/` exists, read `project-governance-lock.json`, its referenced `key-objectives-lock.json`, project-root `KEY_OBJECTIVES.md`, `stage-state.json`, and only the current referenced task/candidate objects. The lock is the machine authority for `releaseIntent`; do not invent a second narrative policy source.
5. If control state is incomplete or contradictory, keep the claim `DIAGNOSTIC`; do not repair machine state through narrative.
6. For a project without a governance lock, first persist written requirement sources, derive the root `KEY_OBJECTIVES.md`, and then establish the positioning axes from [project-positioning.md](references/project-positioning.md). `deliveryObjective`, `releaseIntent`, and automation mode remain separate explicit fields and are never inferred, but known answers may be presented together with objectives in one consolidated confirmation. Bootstrap materializes the confirmed positioning and automation policy. An unanswered or ambiguous boundary, including the automation choice, keeps the project in pre-bootstrap `DRAFT`.
7. Show the proposed positioning/objective summary, explicit trust boundary, excluded threat model, and exactly one of the three automation modes, then ask once for the consolidated startup confirmation. Materialize that record and run read-only `resolve-rules`; report the derived Profile/adapter/Skill/human-gate routing, warnings and investigations without asking again unless a conflict exposes a real boundary change. Bootstrap recompiles the rules and never trusts caller-supplied output.
8. Before task planning, draft observable checkpoints from the confirmed success signals, fixed cases, typed oracle expectations and human gates. Show their `notProven` limits and obtain one checkpoint-summary confirmation; do not ask the user to approve each assertion separately.
9. Ask only boundary-changing questions. Let the model decide low-impact implementation details inside the contract and record assumptions.

## Greenfield behavior

- Trigger implicitly only for an unmistakable whole-project creation request.
- Use an adaptive Socratic interview. Do not write before the user confirms the consolidated vision summary.
- Before writing the approved bootstrap spec, obtain explicit release-intent and automation-mode values; they may share the final consolidated confirmation with already answered positioning and objective axes, but must never be silently inferred or defaulted. The template's `REQUIRES_USER_SELECTION` values are intentionally invalid.
- Do not manufacture a complete vision from a one-line idea. Until the user has grounded the target user, problem/outcome, success signal, first-slice boundary, non-goals, platform/environment constraints, data/safety constraints, and human-quality decisions, keep missing fields explicitly `UNKNOWN` and ask one atomic boundary question.
- During discovery, vary one unresolved boundary at a time. Do not re-ask fields already fixed by authority documents. At the end, present all resolved axes and the derived objectives together for one confirmation.
- After confirmation, create new control files and missing thin authority files. For any existing file, show a diff and obtain approval before editing.
- Suggest Git initialization when absent. If declined, create only a `DRAFT/DIAGNOSTIC` control plane and forbid formal candidates.
- After the first vertical slice and cases are fixed, follow the confirmed automation policy: manual mode asks before product-code execution; either automatic mode treats the startup authorization as permission to advance within the locked contract and stops only at a fixed review point.

## Existing-project behavior

- The first `adopt` pass is read-only.
- Report authority candidates, conflicts, worktrees, dirty state, current claims, migration risk, proposed new files, and proposed edits.
- Before proposing control-file writes for an unmanaged project, ask the same release-intent question. For an already locked project, report its current intent rather than silently changing it; changing it requires a new approved control boundary and invalidates downstream task evidence.
- For a new control plane, collect the automation mode in the same consolidated startup confirmation. An existing Schema 3.2 project without `.vibe-control/automation-policy.json` remains manual; opt-in or mode changes require the content-bound `automation --plan` / `--apply <plan-hash>` path and invalidate the archived task chain.
- Create or modify control files only after the migration proposal is approved.
- Synchronize small managed blocks in both `AGENTS.md` and `CLAUDE.md`; point them at the lock and state files instead of copying a second policy source.
- If a managed block drifted, show the old template, current block, and new template before asking permission to update.

## Risk and questions

Use the fixed risk model and thresholds from [human-decisions.md](references/human-decisions.md). Every choice shown to the user must include:

- heuristic risk score `0–100`;
- human-burden score `0–100`;
- impact and tradeoff;
- a recommended option.

Show at most one blocking human decision at a time. Non-blocking warnings may be grouped.

Do not insert new architecture or implementation choices into the final confirmation. It may consolidate already resolved positioning, release intent, scope, requirement sources, and key objectives. Never label a risk level from memory: use the fixed thresholds or run `python <skill-root>/scripts/vibe_control.py risk --score <0-100> [--forced-r3]`.

Never display a selectable option without both scores. This includes binary `confirm/revise`, `continue/stop`, and authorization questions. Use this exact shape: `Label — risk N/100; human burden M/100; impact: ...`.

## Multi-agent orchestration compatibility

- Resolve the coordination backend from tools that are actually callable in the current host; never infer it from an Agent/model name, product label, static documentation, or a prior run.
- Use `CODEX_THREADS` only when Codex user-owned task/thread creation, messaging, cursor wait and inspection tools are both available and authorized.
- When `CODEX_THREADS` is unavailable but child-agent spawn/message/wait tools are available, explicitly downgrade every cross-session worker/auditor role to `SUBAGENTS`. The responsible parent remains the sole control-plane writer and final decision context; a child-agent report is input, not approval.
- When neither backend exists, use `SERIAL` in the responsible context and mark any would-be independent audit as non-independent. Never fabricate a Codex task, cursor, worktree, or independence claim.
- This Skill imposes no fixed numeric limit on workers or child agents. Dispatch only concrete bounded tasks that fit the host's actual capacity, useful parallelism, file ownership and isolation; a platform capacity limit is not a Skill policy limit.
- In manual mode, ask once per task before creating separate user-owned Codex tasks. In either automatic mode, the confirmed policy is the one task-scoped authorization for bounded worker dispatch; do not pause again at ordinary stages or ask for a made-up numeric quota. Subagent dispatch still follows the host's actual delegation permission.
- Put concurrent writers in separate worktrees with disjoint ownership; allow read-only workers to share the source directory. If writing isolation is unavailable, serialize writes.
- Give every worker a bounded task packet with goal, allowed and forbidden files, baseline, validation command, stop condition, and report shape.
- After candidate freeze, use a fresh read-only auditor context without expected defects or the intended conclusion. Under `SUBAGENTS`, this means a newly spawned auditor with no answer leakage; if the host cannot provide fresh isolated context, the result is diagnostic and non-independent.
- Before checkout, run `python <source>/scripts/check_audit_path.py --source <source> --candidate <candidate-branch-or-tag> --audit-root <short-audit-dir>`. Over 240 characters is `VC-AUDIT-PATH-BUDGET / BLOCKED`; use a short root such as `C:\vc35\<id>` or explicitly opt in per Git command with `-c core.longpaths=true`, never by silently changing global Git settings.
- Materialize the candidate branch/tag as the **first checkout**: `git -c core.autocrlf=true clone --no-local --branch <candidate-branch-or-tag> --single-branch <source> <audit-dir>`. Then verify exact `HEAD` and immediately run `python <audit-dir>/scripts/build_manifest.py --root <audit-dir> --verify`. `--no-checkout` is retained only in historical negative tests. A clean Git status alone is not package-integrity evidence.
- Under `CODEX_THREADS`, prefer cursor-based event waits of no more than 60 seconds and aggregate unchanged progress at 300-second intervals. Under `SUBAGENTS`, use the host's native mailbox/wait primitive; do not pretend a Codex cursor exists. `SERIAL` requires no coordination polling.
- Preserve the same candidate binding, evidence, checkpoint, audit-stop and role-separation rules under every backend. See [multi-session-routing.md](references/multi-session-routing.md).

`advance` is a Skill workflow, not a CLI command. In either automatic mode it performs plan → dispatch/implementation → validation → integration → milestone commit → optional non-force push without blocking at ordinary stages. It must stop for a closed candidate/owner review, any `HUMAN` checkpoint, an owner decision, boundary change, R3 or irreversible work, hard failure, push conflict, or user interruption. The responsible context is the only control-plane writer and the only context permitted to create policy-authorized milestone commits or pushes; workers and reviewers cannot call `accept` or approve a human checkpoint. At each review point render the non-authoritative external-cache Dashboard described in [automation-advancement.md](references/automation-advancement.md).

## Deterministic commands

For a normal installation, run only the installation self-check before bootstrap:

```text
python <skill-root>/scripts/validate_installation.py --skill-root <skill-root>
```

Its `PASS` means the installed package is content-complete for diagnostic development. `sourceKind=GIT_ROOT | GIT_SUBDIRECTORY | PORTABLE_COPY` states which installation identity was actually observed; a portable copy deliberately has no commit/tree provenance. A development result always keeps `formalClaimsAllowed=false` and caps claims at `DEVELOPMENT_CHECKED`.

Only maintainers evaluating formal sealing run the package-release validator and deep suites. Run the long suites with bounded leaf supervision:

```text
python <skill-root>/scripts/validate_assurance_matrix.py --skill-root <skill-root>
python <skill-root>/scripts/validate_package_release.py --skill-root <skill-root>
python <skill-root>/scripts/test_package_release_audit.py --jobs 4 --case-timeout 180 --suite-timeout 240
python <skill-root>/scripts/test_assurance_matrix_fail_closed.py
python <skill-root>/scripts/test_assurance_harness.py
python <skill-root>/scripts/test_formal_activation.py
python <skill-root>/scripts/test_assurance_regressions.py --jobs 4 --case-timeout 180 --suite-timeout 240
python <skill-root>/scripts/test_v033_checkpoints.py
```

Interpret matrix `PASS / CONTROL_IMPLEMENTATION_READY` only as internal traceability. A `DEVELOPMENT_DIAGNOSTIC` package may bootstrap from an integrity-checked Git root, tracked Git subtree, or manifest-verified portable copy, but it is capped at `DEVELOPMENT_CHECKED`. The formal validator reports a development package as installation-usable but not a seal candidate; only an exact sealed Git-root candidate can ever reach `FORMAL_GATE_READY`. Even after package readiness, project release intent independently caps each task's claim.

Run the global wrapper before project pinning:

```text
python <skill-root>/scripts/vibe_control.py inspect --project <root>
python <skill-root>/scripts/vibe_control.py resolve-rules --project <root> --spec <approved-schema3-spec.json>
python <skill-root>/scripts/vibe_control.py bootstrap --project <root> --spec <approved-spec.json>
python <skill-root>/scripts/vibe_control.py reposition --project <root> --spec <confirmed-positioning.json> --plan
python <skill-root>/scripts/vibe_control.py reposition --project <root> --spec <confirmed-positioning.json> --apply <plan-hash>
python <skill-root>/scripts/vibe_control.py revise-objectives --project <root> --spec <confirmed-objectives.json> --plan
python <skill-root>/scripts/vibe_control.py revise-objectives --project <root> --spec <confirmed-objectives.json> --apply <plan-hash>
python <skill-root>/scripts/vibe_control.py automation --project <root> --spec <confirmed-policy.json> --plan
python <skill-root>/scripts/vibe_control.py automation --project <root> --spec <confirmed-policy.json> --apply <plan-hash>
python <skill-root>/scripts/vibe_control.py automation --project <root> --action dispatch|continue|commit|push
python <skill-root>/scripts/vibe_control.py dashboard --project <root> [--output-dir <external-path>]
python <skill-root>/scripts/vibe_control.py risk --score <0-100> [--forced-r3]
python <skill-root>/scripts/vibe_control.py migrate --project <root> --plan
python <skill-root>/scripts/vibe_control.py migrate --project <root> --plan --spec <confirmed-migration-spec.json>
python <skill-root>/scripts/vibe_control.py migrate --project <root> --apply <plan-hash> --spec <confirmed-migration-spec.json>
```

Run the pinned project controller after bootstrap:

```text
python .vibe-control/runtime/0.3.6/control.py lock-task --project . --contract <contract.json>
python .vibe-control/runtime/0.3.6/control.py freeze --project . --actor <implementer> --session <session>
python .vibe-control/runtime/0.3.6/control.py execute --project . --actor <actor> --session <session> [--case <case-id>]
python .vibe-control/runtime/0.3.6/control.py ingest --project . --attestation <external-evidence-attestation.json>
python .vibe-control/runtime/0.3.6/control.py validate --project .
python .vibe-control/runtime/0.3.6/control.py audit --project . --review <review.json>
python .vibe-control/runtime/0.3.6/control.py accept --project . --decision <decision.json>
python .vibe-control/runtime/0.3.6/control.py release-check --project .
python .vibe-control/runtime/0.3.6/control.py handoff --project .
```

Interpret exit codes as:

- `0`: the command completed and the Schema 3.2 envelope is `PASS`;
- `2`: prerequisite/environment blocked;
- `3`: deterministic failure or invalid input;
- `4`: upstream drift invalidated the result.

Always preserve the JSON 3.2 envelope. Use only `formal.eligible` and `formal.maxClaimLevel` for controller claims; the removed top-level `claimEligible` field has no compatibility alias. A zero exit code is not a product-quality judgment.

Only for an `EXTERNAL_RELEASE` R3 task whose contract permits `RELEASE_READY`, after `audit` and `accept` have produced tracked records, the independent **release-auditor**—distinct from the implementation/execution actors and the internal review auditor—must create and sign an `external-release-audit` object under `.vibe-control/external-audits/`, including a tracked raw transcript, exact candidate/review/evidence references, and current package/runtime/matrix hashes. Commit that report. The owner then signs the project runtime's `release-receipt.json`, binding that exact candidate, decision and external-audit file; commit it. Private keys remain outside the agent/runtime environment. These two signed objects are deliberately not fabricated by a CLI convenience command. They do not apply to `LOCAL_EXPERIMENT` or `PRIVATE_OPERATION`.

`adopt`, `start`, `resume`, and `advance` remain Skill workflow routes. The implemented CLI is exactly: `inspect`, `resolve-rules`, `bootstrap`, `reposition`, `revise-objectives`, `automation`, `dashboard`, `lock-task`, `validate`, `freeze`, `execute`, `ingest`, `audit`, `accept`, `release-check`, `handoff`, `migrate`, and `risk`.

## Completion discipline

- Render one current summary: governance/runtime version, unit, locked release intent/path, phase/health/claim, risk, candidate, change envelope, checkpoint/case totals, hard-check totals, exploration-budget use, warnings, investigations, sessions/roles, blockers, one human decision, and one next action.
- Phrase absence findings as “本轮覆盖内未发现 P0/P1/P2”; never claim unknown defects do not exist.
- Persist assessments only at bootstrap, candidate freeze, verification/audit, acceptance, migration, and handoff.
- In `MANUAL_STAGE_CONFIRMATION`, propose commits and pushes and wait for explicit approval. In either automatic mode, the responsible context may create validated milestone commits; only `AUTO_PUSH_TO_REVIEW` may non-force push those commits to the exact existing bound upstream.
- Automatic authorization never covers merge, rebase, new remotes, PRs, tags, releases, destructive migration, permission expansion, R3/irreversible actions, `accept`, or force-push. Stop and obtain explicit current authorization instead.
- At every fixed owner-review point, generate `index.html`, `status.json`, and `summary.md` in the external dashboard cache. Treat the snapshot as a read-only projection that cannot alter state, replace evidence, grant a claim, or close a blocker.

## Bundled resources

- `assets/project-control/templates/`: task, evidence, assessment, model-routing, and bootstrap templates.
- `assets/project-control/schemas/`: JSON Schema 2020-12 interfaces.
- `assets/project-control/runtime/control.py`: thin entry shim; deterministic implementation is split under `runtime/vibe_runtime/` and copied into each project.
- `assets/project-control/runtime/schemas/external-release-audit.schema.json`: signed candidate-bound external audit interface used by the receipt chain.
- `scripts/build_manifest.py`: rebuild or verify the Skill package manifest.
- `scripts/validate_installation.py`: normal installation self-check for Git-root, Git-subdirectory and portable development packages; it never grants a formal seal.
- `scripts/bounded_test_runner.py`: shared leaf-process supervision, timeout, progress and counter-conservation implementation for deep suites.
- `scripts/test_fixtures.py`: deterministic positive/negative tests.
- Git tag `v0.1.2-containment`: preserves the historical containment runtime and assertions.
- `scripts/test_v2_security.py`: signature, dependency, migration, receipt, handoff, and composition regressions.
- `scripts/test_assurance_regressions.py`: external-audit-derived adversarial regressions; all must pass before formal-gate readiness.
- `scripts/test_assurance_harness.py`: verifies per-case process isolation, timeout failure, stable worker protocol, and aggregate counter conservation for the adversarial suite.
- `scripts/test_assurance_matrix_fail_closed.py`: proves that a formal boolean cannot outrun implementation and independent-validation closure.
- `scripts/test_formal_activation.py`: activates an isolated temporary package copy and proves PRIVATE and EXTERNAL positive paths without changing the installed Skill.
- `scripts/test_v033_checkpoints.py`: proves checkpoint closure, typed oracle identity, finding task/claim scope, bounded exploration, and recoverable 3.1→3.2 migration.
- `scripts/test_package_release_audit.py`: creates real temporary Git seals and proves exact-candidate positive closure plus post-audit runtime/test and tag/report/hash/identity negative mutations.
- `scripts/validate_package_release.py`: the only package-level readiness aggregator; it consumes Git object identities and the external audit seal without using private keys.
- `scripts/validate_assurance_matrix.py`: checks finding coverage and claim-to-code/test traceability without claiming runtime readiness.
- `scripts/validate_schema_mirror.py`: fails if public schemas would point agents at a different interface than the pinned runtime.
- `scripts/test_template_interfaces.py`: checks that public review/receipt/audit templates expose every required runtime-schema field and all formal roles.
