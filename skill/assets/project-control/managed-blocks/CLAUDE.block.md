<!-- vibe-control:start v0.4.0 -->
## Vibe Control

在任何项目写入、边界提问或任务规划前，先在项目外的本机缓存初始化 `progress` 账本和 Dashboard。Coordinator 是账本与控制面的唯一写入者；Team、SubAgent、Executor 与 Auditor 只回报结果。

开始任务前读取项目根 `KEY_OBJECTIVES.md`、`.vibe-control/key-objectives-lock.json`、`.vibe-control/project-governance-lock.json`、当前 task lock 与 `.vibe-control/stage-state.json`。接纳 blocker、规划修复、改变架构／case／oracle、验收或交接前必须重新读取 `KEY_OBJECTIVES.md`。

默认使用 `AUTO_LOCAL_TO_REVIEW / MILESTONE_COMMITS / NONE`，按 `TEAM → SUBAGENT → SERIAL` 解析实际能力。实现者只运行改动相关的快速检查；候选冻结后的完整 case 与审核交给独立上下文。不得自动 push、merge、rebase、创建远端／PR／tag／release、批准人工检查点或执行不可逆操作。

候选冻结、验收声明和交接前运行：

`python .vibe-control/runtime/0.4.0/control.py validate --project .`

权限、路径、不可逆操作和远端冲突停止动作；测试失败、证据缺失、skip、零计数或漂移阻止声明，但允许在原任务范围内继续修复；普通流程建议不得升级为 blocker。`packageMode=DEVELOPMENT` 时不得超过 `DEVELOPMENT_CHECKED`。

节点完成只更新本机账本与 Dashboard。真正停止或进入 Owner 复核时，报告最后必须用无内部术语的普通话解释项目功能、已完成、仍未完成和用户后果，并给出推荐方案、备选方案与开放输入。
<!-- vibe-control:end -->
