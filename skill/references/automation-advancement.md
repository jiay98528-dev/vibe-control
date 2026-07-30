# 默认自动推进与定点人工复核

在需求、关键目标、项目定位和发行意图已经确定后读取本文件。新项目与由 3.2 升级的项目默认获得**本地、可逆、有界**的推进权限，不再为普通阶段逐次询问。

## 默认策略

控制器物化：

```text
mode = AUTO_LOCAL_TO_REVIEW
commitPolicy = MILESTONE_COMMITS
pushPolicy = NONE
```

该默认值允许规划、Team/SubAgent 委派、产品实现、最小开发检查、候选冻结、独立执行/审核和通过检查的里程碑提交。它不允许 push、force-push、merge、rebase、创建 remote/PR、tag、release、安装依赖或 Skill、权限扩大、不可逆操作、R3 动作、`accept` 或人工检查点批准。

用户主动要求逐阶段控制时可改为 `MANUAL_STAGE_CONFIRMATION / MANUAL / NONE`。只有用户明确授权并绑定既有 remote、branch、upstream 与去凭据 URL 哈希后，才可改为 `AUTO_PUSH_TO_REVIEW / MILESTONE_COMMITS / EXISTING_UPSTREAM_MILESTONES`。策略变化使用内容绑定的 `automation --plan` / `--apply <plan-hash>`；不得凭聊天摘要扩大副作用权限。

## 启动与执行顺序

任何项目写入或边界提问前先按 [progress-dashboard.md](progress-dashboard.md) 初始化本机账本和仪表台。然后：

```text
定位与目标
→ 生成里程碑、计分项和 verificationStrategy
→ TEAM / SUBAGENT / SERIAL 实现
→ Implementer 最小开发检查
→ Coordinator 整合并冻结候选
→ Executor 执行锁定 case
→ Auditor 新鲜只读审核
→ 更新仪表台
→ Owner review
```

普通节点完成时 Coordinator 追加 progress event、重建 Dashboard 并发送非阻塞进度；不得把节点更新变成批准请求。角色、任务包和测试瘦身见 [execution-routing.md](execution-routing.md)，后端解析见 [multi-session-routing.md](multi-session-routing.md)。

## CLI

```text
automation --project <root> --spec <policy.json> --plan
automation --project <root> --spec <policy.json> --apply <plan-hash>
automation --project <root> --action dispatch|continue|push
automation --project <root> --action commit [--message "<single-line-subject>"]

progress --project <root> --action init --spec <plan.json>
progress --project <root> --action update --spec <event.json> --expected-revision <n>
progress --project <root> --action stop --spec <report-packet.json> --expected-revision <n>
progress --project <root> --action clear --scope current-task|project --confirm <project-instance-id>
dashboard --project <root> [--output-dir <external-path>]
```

`progress` 只写用户缓存中的临时账本和投影；`dashboard` 只读重建快照。二者都不得修改项目、控制面或声明。`automation --action` 在副作用前重新验证策略、任务/候选、路径、分支、工作树和相应权限。

里程碑默认提交标题为 `chore(governance): record <taskId> milestone`，可用 `--message` 提供单行标题。Hook 拒绝时保留文件、撤销本次新增 staging，并返回原始输出；不得绕过 hook。自动 push 只能非强制推送策略绑定的既有 upstream；远端冲突、认证失败或身份变化立即停止。

## Guard 与停止点

`ACTION_GUARD`、`CLAIM_GUARD`、`HUMAN_DECISION`、`ENVIRONMENT_BLOCKED` 和 `ADVISORY` 的语义以 [execution-routing.md](execution-routing.md) 为准。`CLAIM_GUARD` 不应把可在原合同内修复的开发工作永久停住；`ADVISORY` 不得阻断。

必须交还 Owner 的条件：

- 候选、自动检查点与审核输入已闭合，等待 owner review；
- 到达 `HUMAN` checkpoint 或 owner decision；
- 目标、范围、case/oracle、风险、发行意图、权限或其他锁定边界需要变化；
- 将执行 R3、不可逆或额外授权操作；
- `ACTION_GUARD`、不可恢复的环境阻断、推送冲突、`PAUSED_NO_PROGRESS` 或用户中断。

停止时运行 `progress stop` 并生成同源 `index.html`、`status.json`、`summary.md`。报告末尾必须有零术语说明，并给出 `RECOMMENDED`、`ALTERNATIVE`、`OPEN` 三项下一步；宿主有结构化提问工具时必须调用。详见 [progress-dashboard.md](progress-dashboard.md) 与 [human-decisions.md](human-decisions.md)。

Dashboard 是外部缓存投影，不是事实源、证据、审核或批准。它与 `validate` 冲突时重新计算共享只读 projection 并保持 fail-closed，不能相信更乐观的一方。
