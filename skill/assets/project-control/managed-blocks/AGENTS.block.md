<!-- vibe-control:start v0.3.6 -->
## Vibe Control

开始写任务前读取项目根 `KEY_OBJECTIVES.md`、`.vibe-control/key-objectives-lock.json`、`.vibe-control/project-governance-lock.json` 中锁定的 `packageMode`／`releaseIntent`、其引用的 `.vibe-control/automation-policy.json` 与 `.vibe-control/stage-state.json`。接纳 blocker、规划修复、修改架构／case／oracle、验收或交接前必须重新读取 `KEY_OBJECTIVES.md`。

候选冻结、验收声明和交接前运行：

`python .vibe-control/runtime/0.3.6/control.py validate --project .`

确定性检查失败只允许修复、重跑或降低声明，不得人工改写为 PASS。
`LOCAL_EXPERIMENT`、`PRIVATE_OPERATION`、`EXTERNAL_RELEASE` 分别限定最高 `VERIFIED`、`ACCEPTED`、`RELEASE_READY`；不得静默改变发行意图。
`packageMode=DEVELOPMENT` 时无论产品意图如何都不得超过 `DEVELOPMENT_CHECKED`。普通任务不得修改 `KEY_OBJECTIVES.md`；只能通过 `revise-objectives --plan/--apply` 变更并使下游事实失效。
新项目缺少显式 automation mode 不得 bootstrap；旧 Schema 3.2 项目缺少策略时按 `MANUAL_STAGE_CONFIRMATION` 运行。策略只能通过 `automation --plan/--apply` 改变。
自动模式普通阶段不等待批准；负责上下文是控制面、里程碑提交和已授权非强制推送的唯一写入者。候选闭合、人工 checkpoint/owner decision、边界变化、R3/不可逆操作、硬失败、推送冲突或用户中断时必须停止，并运行 `python .vibe-control/runtime/0.3.6/control.py dashboard --project .` 生成外部缓存的非权威复核快照。
<!-- vibe-control:end -->
