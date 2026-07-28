# 关键目标缰绳

## 目的

`KEY_OBJECTIVES.md` 位于需求事实源之后、任务规划之前。它不是另一份需求大全，而是多轮会话和对抗审核共同使用的范围锚点：先问“这项发现影响哪个已确认目标或最低核心”，再决定是否修复。

固定顺序为：

`需求事实源落盘 → 推导 KEY_OBJECTIVES.md → 一次综合确认 → 内容锁定 → 任务规划`

已由权威需求回答的内容不得重复询问。项目定位、发行意图和关键目标可以在同一次综合确认中呈现，但必须保存为相互独立的字段。

## 文档最小结构

- 文档 ID、正整数修订、`DRAFT | CONFIRMED | SUPERSEDED` 状态和来源文档；
- 一句 North Star；
- 一至五个 `KO-xxx`；
- 至少一个 `KF-xxx`；
- 至少一个 `NG-xxx`；
- 信任对象、不信任输入、明确排除的攻击者能力，以及重开威胁模型的触发条件；
- 最低安全与证据核心；
- 治理成本预算；
- 审计接纳、停止和变更规则。

控制器只检查文件身份、Git 跟踪、哈希、格式化 ID 与引用闭包，不评判文字是否合理。目标质量与取舍由用户在综合确认中决定。

## 审计发现分类

| 分类 | 默认作用 |
| --- | --- |
| `CURRENT_GOAL_DEFECT` | 直接阻断映射目标与声明 |
| `MINIMUM_CORE_VIOLATION` | 直接阻断证据／安全最低核心 |
| `SAFETY_OVERRIDE` | 仅在绑定已锁定 `KF-*` 安全边界、受影响声明和证据后直接阻断 |
| `HUMAN_DECISION` | 只阻断 `affectedClaims` 明确列出的声明 |
| `PROCESS_WARNING` | 提示，不改变当前范围或资格 |
| `INVESTIGATION` | 记录待查事实，不先行归因 |
| `FUTURE_PROPOSAL` | 后续候选，不进入当前任务 |
| `OUT_OF_SCOPE` | 明确排除 |

严重度只表达影响程度，不能自行扩大任务范围。前三类开放 finding 必须包含目标引用、受影响声明、复现、非空证据、最小修复和新增治理成本；`SAFETY_OVERRIDE` 至少引用一个当前目标锁中的 `KF-*`。超出已确认威胁模型的理论攻击默认是 `INVESTIGATION`、`FUTURE_PROPOSAL` 或 `OUT_OF_SCOPE`，不能通过改贴 P0/P1 或 safety 标签进入当前 blocker。

例如，若项目已明确“信任经代码审查的精确候选与 CI runner，但不信任下载产物和测试自报；暂不防御候选、安装器与 verifier 联合恶意造假”，那么真实的文件关联误测、进程未退出、CRLF 构建失败或候选 EXE 未重哈希可映射为当前缺陷；“候选可能串通 verifier”则不得阻断，除非用户先修订威胁模型。严格 FIFO、marker 约束或大小写收紧若不影响当前用户路径，应分别保持普通缺陷、加固建议或 P3 warning，而不是自动升级证明体系。

## 读取与变更规则

主线程在接纳 blocker、规划修复、修改架构／case／oracle、验收、交接前重新读取目标文档。普通 worker、执行者与审核者不得修改它。

目标变化只能通过 `revise-objectives --plan --spec <spec>` 查看差异、风险和失效集合，再以 `--apply <plan-hash>` 执行同一计划。应用后回到 `DRAFT / DIAGNOSTIC`；旧 task、candidate、evidence、review、decision 和 handoff 只保留为历史诊断。
