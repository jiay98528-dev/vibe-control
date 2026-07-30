# Team、SubAgent 与串行兼容路由

在需要持久协作、并行实现、独立执行或审核时读取本文件。先枚举当前宿主真正可调用且获授权的工具，再解析一个主后端；不得根据产品名、模型自述、静态说明或历史运行推断能力。

非 Codex 宿主使用完全相同的能力判断：能提供持久会话与隔离工作的编辑器可映射为 `TEAM`，只有子智能体能力时显式降级为 `SUBAGENT`，两者都没有时使用 `SERIAL`。

## 后端优先级

| 后端 | 选择条件 | 行为 |
| --- | --- | --- |
| `TEAM` | 宿主具备持久任务/会话、消息、等待、状态检查和工作隔离 | Coordinator 保持路由和控制面；持久 worker 处理持续实现，新鲜只读成员承担审核。Codex Threads、AgentTeam 和 IDE Team 都是 provider，不是独立治理模式。 |
| `SUBAGENT` | Team 不可用，但有 spawn/message/wait 子智能体能力 | 所有执行角色降级为有界子智能体；父上下文仍是唯一 Coordinator。 |
| `SERIAL` | 两类工具均不可用 | Coordinator 串行完成；审核标为非独立诊断，不得伪造 Team、并行或隔离。 |

如果宿主规定创建用户可见或持久 Team 必须获得显式授权，使用结构化提问工具询问一次；未获授权时降级为 `SUBAGENT`，不阻断项目。若宿主允许当前任务直接创建 Team，则默认使用，不再询问。子智能体调用按宿主权限执行，不另问任意数量配额。

在 `TEAM` 主后端中，把跨里程碑、需要持续上下文或长期所有权的工作交给持久 Team 成员；若宿主同时提供 SubAgent，把边界窄、一次性、可独立回收的检索、机械修改或探针交给 SubAgent。SubAgent 仍回报 Coordinator，不建立第二控制面，也不改变 Team 的主后端身份。

数量策略为 `NO_SKILL_FIXED_LIMIT`。Skill 不设置 Team 成员或 SubAgent 数量上限；实际派发只受宿主容量、有用的独立任务数、文件所有权、工作树和资源约束。“无 Skill 上限”不授权无边界批量派发。

## 单写与隔离

Coordinator 是任务合同、目标、case/oracle、控制面、进度账本、候选、整合、提交和用户沟通的唯一写入者。Worker、Executor、Auditor 的报告都是输入，不是批准。

并行写入必须使用隔离 worktree 和互不重叠的文件所有权；只读调查可共享目录。无法可靠隔离时串行写入。任何执行角色不得自行 merge、rebase、push、tag、release、修改控制合同或批准人工检查点。

每个任务包包含目标、允许/禁止路径、基线、相关事实源、检查点、最小验证、停止条件和回报格式。Implementer 不接收完整保证矩阵或审计答案；Auditor 不接收实现者摘要、已知缺陷、候选实际结果或预期总裁决。详细角色边界见 [execution-routing.md](execution-routing.md)。

## 自动推进

默认策略在任务内授权有界 Team/SubAgent 派发和本地里程碑提交。普通阶段不暂停；Coordinator 在每个节点完成时维护外部 progress ledger。后端降级不能扩大权限、降低硬检查或提高独立性声明。

候选冻结后，Auditor 必须是未参与实现的新鲜只读上下文。`TEAM` 使用独立新成员/任务，`SUBAGENT` 新建未接收答案的审核子智能体，`SERIAL` 明确标记非独立。Auditor 完成锁定检查点即停止；更换会话不能重置探索预算。

持久 Team 使用宿主原生增量 wait；单次等待不得阻塞用户更新超过 60 秒，状态未变时避免重复全量读取。SubAgent 使用原生 mailbox/wait；Serial 不建立虚构等待。任何后端收到完成、失败、阻断或用户消息都立即交给 Coordinator。

出现 Owner review、人工检查点、边界变化、R3/不可逆操作、动作阻断、不可恢复环境阻断、远端冲突或用户中断时，所有后端停止。Coordinator 更新仪表台并按 [human-decisions.md](human-decisions.md) 提交唯一当前决定。

候选物化、短路径预算、manifest 验证和正式包外审仍遵循 [controller-assurance.md](controller-assurance.md)；协调 provider 不改变证据标准。
