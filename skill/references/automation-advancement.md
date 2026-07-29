# 自动推进与定点人工复核

在需求、`KEY_OBJECTIVES.md`、项目定位与发行意图已经确定后读取本文件。目标是用一次授权替代普通阶段的反复停顿，同时保留边界变化、不可逆操作和人工质量判断的明确停止点。自动推进不改变检查点、证据、角色分离或声明上限。

## 启动综合确认

把自动化模式加入项目启动的同一次综合确认；不得在用户未回答时写入新项目控制面。只提供以下互斥选项：

- `AUTO_LOCAL_TO_REVIEW`（推荐）— 风险 45/100；人工负担 20/100；影响：自动规划、委派、实现、验证并创建通过检查的里程碑提交，到人工复核点停止，不推送。
- `AUTO_PUSH_TO_REVIEW` — 风险 60/100；人工负担 15/100；影响：在本地自动模式上，允许向已存在且已绑定的 upstream 分支非强制推送里程碑提交；远端冲突或身份变化立即停止。
- `MANUAL_STAGE_CONFIRMATION` — 风险 25/100；人工负担 65/100；影响：保留逐阶段确认，不自动提交或推送。

固定组合为：手动模式使用 `commitPolicy=MANUAL / pushPolicy=NONE`；本地自动模式使用 `MILESTONE_COMMITS / NONE`；推送自动模式使用 `MILESTONE_COMMITS / EXISTING_UPSTREAM_MILESTONES`。不得自由拼接出第四种权限组合。

确认必须记录精确项目、当前关键目标与定位身份、模式、提交/推送权限、固定停止条件和用户确认记录，并以规范化内容哈希绑定到 `.vibe-control/automation-policy.json`；治理锁和 task lock 必须引用同一策略身份。旧 Schema 3.2 项目缺少自动化策略时按 `MANUAL_STAGE_CONFIRMATION` 解释；不得静默补写策略或取得自动副作用权限。

模式只能通过内容绑定的 `automation --plan` / `--apply <plan-hash>` 修改。apply 前展示变化、权限差异和失效集合；变更后归档当前 task，并使 candidate、evidence、review、decision 与 handoff 失效，状态回到 `DRAFT / BLOCKED / DIAGNOSTIC`。

## 自动推进执行

`advance` 是 Skill 工作流，不是 CLI。负责上下文先探测实际工具，再且只选择一个后台：

1. `CODEX_THREADS`：仅在用户可见任务的创建、消息、检查和 cursor wait 能力均实际可用且获授权时使用。
2. `SUBAGENTS`：无上述能力但可 spawn/message/wait 子智能体时使用；非 Codex 宿主显式采用此降级。
3. `SERIAL`：两者均不可用时由负责上下文串行执行，且不得宣称独立审核。

Skill 不设置 worker 或子智能体数量上限。实际数量由宿主容量、有用的独立任务数、文件所有权和隔离条件决定。负责上下文是控制面、整合、提交、推送和用户沟通的唯一写入者；worker 只执行有目标、允许/禁止文件、基线、验收命令、停止条件和回报格式的任务包。并行写入必须使用隔离 worktree 和互不重叠的所有权，否则串行。

自动模式按“计划 → 委派/实现 → 验证 → 整合 → 里程碑提交 → 可选推送”推进。普通阶段只给非阻塞进度更新，不请求阶段性批准。只有阶段检查已通过、工作区归属清楚且提交内容仍在合同范围内，才能创建里程碑提交。

`AUTO_PUSH_TO_REVIEW` 只允许把当前分支的里程碑提交非强制推送到策略中已存在、精确绑定的 remote/upstream。每次推送前重新核对分支、upstream 和去凭据远端 URL 哈希，并逐提交检查 task baseline 之后的完整历史；任一提交触及 `forbiddenPaths`、越出 `allowedPaths`、写入未授权控制面路径或引入 merge commit，都以 `HC-AUTOMATION-PUSH-SCOPE`／`HC-AUTOMATION-MILESTONE-HISTORY` 停止。只检查工作树干净或最终树差异不足以授权推送，因为已提交或先增加后删除的越界路径仍会进入远端历史。认证失败、非 fast-forward、远端身份漂移或工作区污染均停止。任何自动模式都禁止自动 merge、rebase、创建 remote、创建 PR、tag、release 或运行 `accept`，也不得安装缺失 Skill、扩大权限或执行未另行授权的 R3 操作。

## CLI 边界

公开 CLI 为：

```text
automation --project <root> --spec <policy.json> --plan
automation --project <root> --spec <policy.json> --apply <plan-hash>
automation --project <root> --action dispatch|continue|push
automation --project <root> --action commit [--message "<single-line-subject>"]
dashboard --project <root> [--output-dir <external-path>]
```

`plan` 只读；`apply` 写入已确认策略；`action` 在副作用前重新验证策略哈希、task/candidate 绑定、允许路径、Git 分支、工作树与相应权限。未知动作、策略漂移或缺少前置必须返回稳定阻断，不能由叙述性授权绕过。CLI 只执行并核对动作，不替模型生成开发计划，也不批准人工检查点。

自动提交的默认标题是 `chore(governance): record <taskId> milestone`；调用方可用 `--message` 提供单行标题。空标题、换行、Unicode 行分隔符或控制字符必须在调用 Git 前阻断。工作树解析必须消费完整 porcelain 输出，包括首行 `.vibe-control/**` 路径。若 commit hook 或 commitlint 拒绝提交，控制器保留原工作文件、清除本次新增 staging，并以 `HC-AUTOMATION-MILESTONE-COMMIT / BLOCKED` 返回退出码和 hook stdout/stderr；不得绕过 hook 或把失败叙述为已提交。

## 固定人工复核点

出现以下任一情况必须停止自动推进：

- 自动检查点全部报告完毕并形成待复核候选；
- 到达任一 `HUMAN` checkpoint 或 owner decision；
- 目标、范围、case/oracle、风险、发行意图、权限或其他锁定边界需要变化；
- 将执行 R3、不可逆或需额外授权的操作；
- 硬失败、推送冲突或用户中断。

自动 reviewer 可以提供候选绑定的诊断或审核输入，但不能批准 `HUMAN` checkpoint、代替 owner decision 或调用 `accept`。即使任务没有主观质量门，候选闭合后也必须停到一次 owner review。复核界面同一时刻只呈现一项人工决定；若无需决定，明确显示“无”。

## 复核仪表盘

到达人工复核点时运行 `dashboard`，原子生成同一快照的三份文件：

```text
index.html
status.json
summary.md
```

默认写入用户外部缓存目录：Windows 为 `%LOCALAPPDATA%\vibe-control\dashboards\<project>\<task>\`，其他平台使用用户缓存目录；除非用户显式指定外部路径，不得写入项目 Git 工作树。

Dashboard 与 `validate` 必须消费同一份无副作用 validation projection。顶层展示 derived phase/health/claim；declared state 只在单独区域展示并标记漂移，不能覆盖派生事实。自动 checkpoint 只有在其全部 case 都有合格、候选绑定的 PASS evidence，且满足 `minExecuted/maxFailed/maxSkipped/artifacts` 时才显示 PASS；一项 case PASS 而其余缺失必须显示 `PENDING/BLOCKED`。Blocker、case counters、checkpoint rows 和“已证明/未证明”只能从该 projection 派生。三份文件必须绑定同一快照 SHA-256；HTML 必须转义项目内容并可离线打开。

Dashboard 是可观察投影，不是事实源、执行证据、审核或批准。生成前后 stage-state 与 evidence 字节必须不变；生成、浏览或修改它都不能改变状态、覆盖 evidence、授予 claim 或解除 blocker。Dashboard 与 CLI 冲突是投影缺陷，不能通过相信更乐观的一方解决；必须重新运行共享 projection 并保持 fail-closed。
