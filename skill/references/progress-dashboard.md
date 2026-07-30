# 本地进度账本、仪表台与普通话报告

在 `bootstrap / adopt / start / resume`、任务节点完成、停止或向用户提交执行/门禁/审计报告时读取本文件。该界面帮助没有开发背景的 Owner 恢复项目焦点，不诊断或评价用户本人。

## 启动顺序与存储

任何项目写入或边界提问前先运行 `progress init`。即使项目没有 Git、`.vibe-control` 或 task，也要建立本机记录：

```text
%LOCALAPPDATA%\vibe-control\workspaces\<project-instance-id>\<safe-task-id>-<digest>\
├─ progress-ledger.json
├─ status.json
├─ summary.md
└─ index.html
```

其他平台使用用户缓存目录。`project-instance-id` 由规范化绝对路径与可用 Git 身份派生；无 Git 时只使用路径身份。task 目录使用可读安全片段和原始 task ID 摘要，防止不同名称清洗后串线。预任务记录使用稳定占位 task ID，锁定任务后建立新 task 视图而不改写旧事件。

账本和快照是本机临时数据：不写项目、不进入 Git、不参与候选、证据、审核、阶段或声明资格，也不自动过期。用户可手动执行：

```text
progress --project <root> --action clear --scope current-task|project \
  --confirm <project-instance-id>
```

清除只删除匹配项目身份的本机展示历史。Dashboard 文件丢失可从 ledger 重建；ledger 丢失时只能显示“本机进度记录已丢失”，不得从聊天或旧报告猜测历史。

## 单写、并发与事件

Coordinator 是唯一账本写入者。Team、SubAgent、Executor 和 Auditor 回报结构化结果，由 Coordinator 复核后追加事件；它们不得直接写账本、控制面或批准检查点。

公开写接口为：

```text
progress --project <root> --action init --spec <plan.json>
progress --project <root> --action update --spec <event.json> --expected-revision <n>
progress --project <root> --action stop --spec <report-packet.json> --expected-revision <n>
dashboard --project <root> [--output-dir <external-path>]
```

每次写入使用文件锁、revision 比较、临时文件和原子替换。revision 不匹配必须返回冲突并保留两方数据；调用方重新读取后合并，不得 last-write-wins。节点状态仅为：

```text
PENDING | ACTIVE | COMPLETED | BLOCKED | FAILED | SUPERSEDED
```

节点完成、失败、取代或停止都追加事件并重建三份同源快照。普通节点只发送非阻塞进度，不询问用户。

`stop` 后普通 `update` 必须阻断。Owner 选择继续时，Coordinator 提交一次独立的恢复事件：`resumeAcknowledgement` 必须绑定当前停止报告的 `reportRevision` 与 `reportSha256`，并使用 `actorId=owner / action=CONTINUE`。恢复只归档旧报告并增加一次 revision，不得在同一事件里改变节点；下一次 revision 才能继续执行。旧确认重放、重复 stop 或没有活动报告时提交确认都必须失败。

## 零背景导向

每份快照先用普通语言回答：

- 项目给谁使用、解决什么问题；
- 当前正在完成什么可见结果；
- 已完成和仍未完成的功能；
- 现在能做什么、不能做什么；
- 不处理当前问题的直接后果；
- 哪些结论已有依据，哪些仍没有依据。

哈希、内部 ID、枚举、Schema、commit/tree、claim 和 check ID 只能放在折叠的“技术详情”。展示模式名为 `ZERO_CONTEXT_ORIENTATION`，不得写成对用户能力或状态的判断。

## 四域量化

Task 的 `scorecardPlan[]` 在实现前锁定每个分母。每项必须通过类型化 `factSources` 引用检查点、case、证据、独立审核或最低核心控制，不能由 Dashboard 临时创造，也不能用功能检查点冒充审核或流程闭包。四域为：

| 领域 | 分子 / 分母 |
| --- | --- |
| `FUNCTIONALITY` | 已满足的功能检查项 / 已计划功能检查项 |
| `ROBUSTNESS_SECURITY` | 已闭合的恢复、安全和故障项 / 已计划项 |
| `AUDIT` | 已有合格证据和审核结论的项 / 要求审核项 |
| `PROCESS` | 已满足最低边界和任务约束的项 / 已计划项 |

任务锁定后每域至少一项。`UNKNOWN`、`PENDING`、无合格证据或只由自报支持的项不计入分子。没有计分基线时显示 `N/A`，不得伪造 0% 或 100%。

综合交付准备度固定为：

```text
FUNCTIONALITY × 40%
+ ROBUSTNESS_SECURITY × 25%
+ AUDIT × 20%
+ PROCESS × 15%
```

保留一位小数，同时显示各域分子、分母、证据覆盖与未知项。页面必须明确：“这是交付准备度，不是剩余工时预测。”综合值不能覆盖任一硬 blocker 或声明上限。

## 页面行为

`index.html` 使用自包含 HTML、CSS、SVG 和嵌入数据，无网络依赖。展示四域量化条、综合图、里程碑时间线、派生状态、候选范围、case 计数、blocker、已证明/未证明和下一步。支持深浅主题、键盘导航、减少动画及 375/768/1440px；转义所有项目内容，不执行项目提供的 HTML 或脚本。

Dashboard 与 `validate` 消费同一只读 projection。Declared state 单独展示并标记漂移；任何更乐观的缓存不得覆盖派生事实。生成前后项目工作树、stage-state 和 evidence 字节必须相同。

## 报告末尾与下一步

每份执行、门禁、审计报告最后必须有标题：

```text
给没有开发背景的人看的说明
```

并包含 `plainLanguage`：项目用途、做了什么、现在能用什么、还不能用什么、用户影响、能否继续、能否发行。该段禁止使用内部枚举、控制面 ID、哈希、Schema、claim、commit/tree；技术事实必须翻译成功能和后果。

仅在真正停止、Owner 复核或需要边界决定时生成三个下一步入口：

1. `RECOMMENDED`：推荐行动与直接后果；
2. `ALTERNATIVE`：另一可行行动与取舍；
3. `OPEN`：用户自由输入方向；它只收集意图，不授权执行，收到内容后再计算风险与人工负担。

两个具体候选必须显示风险、人工负担和直接后果；三个入口都必须绑定当前目标、节点、检查点或 blocker，并标记为建议而非事实。宿主有结构化提问工具时必须调用；仅在工具不可用时用文本序号回退。后台节点更新不得弹出问题。
