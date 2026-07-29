# 人工决策与量化

## 只问边界问题

必须确认：目标、范围、非目标、事实源、治理单元、项目定位、当前交付目标、项目预期发行状态、新项目自动化模式、产品语义、R2/R3 case、主观体验、发布、安全、权限、数据破坏和不可逆操作。

无需确认：合同范围内的低影响架构细节、实现顺序、命名、局部重构、调试方法和可逆工具选择。记录关键假设即可。

同一时刻最多提出一个阻断性问题。每个问题提供 2–3 个互斥选项，把推荐项放第一位；每项必须显示风险分、人工负担分和影响。未知不是默认批准。

项目尚无治理锁时，`releaseIntent` 是写入任何 bootstrap/control 文件之前的必答问题。只能使用 `LOCAL_EXPERIMENT`、`PRIVATE_OPERATION`、`EXTERNAL_RELEASE` 三项；不得从“个人项目”“本地 Skill”“暂时免费”或仓库可见性推断。选择细节与固定分数见 [bootstrap.md](bootstrap.md)。

`releaseIntent` 的人工回答可被控制器固定和哈希绑定，但“回答者确实是用户”仍是显式人工边界，不能由 JSON 自证。缺少回答时保持 `DRAFT/DIAGNOSTIC`，不得由代理填写默认值。

统一选项格式：`选项标签 — 风险 N/100；人工负担 M/100；影响：...`。确认、拒绝、继续、停止、采用默认值也属于选项，不能省略分数。

边界仍未知时，一个阻断问题只能改变一个变量，禁止把“确认摘要”与尚未回答的平台、技术栈或范围选择混在一起。已由用户或权威事实源明确的轴不得重复询问；所有边界事实稳定后，用**一次综合确认**同时绑定需求摘要、`KEY_OBJECTIVES.md`、规范化 positioning 与已明确的三选一自动化模式。综合确认只能复述已解决事实和剩余 `UNKNOWN`，不得夹带新的架构或产品选择。

选项之间只能比较同一变量。把用户类型、平台、存储、同步或发布捆绑成套餐会隐藏权衡，必须拆成后续独立问题。

## Schema 3.2 项目定位与检查点的人工边界

首次 bootstrap 不允许只问“是什么项目”。以下决定必须分轴完成：

- `primaryExperience`；
- 全部 `capabilityDomains[]`；
- 当前里程碑的 `deliveryObjective`；
- `releaseIntent`；
- `runtimeTargets[]`；
- `targetEnvironments[]`；
- `distributionChannels[]`；
- `firstVerticalSlice.successSignals[]`、`humanQualityGates[]` 与 `nonGoals[]`。

操作系统、设备与架构属于 target environment；Steam、TapTap、应用商店或内部分发属于 distribution channel。两者不能绑成一个选项。`deliveryObjective` 与 `releaseIntent` 也必须分开询问：Demo 可以预期外部发行，production candidate 也可能只用于私有运行。

仓库发现只提供背景，不能替用户确认任何目标轴。Profile、adapter 和 Skill routing 可以由控制器从定位确定性派生；规范化定位摘要和关键目标摘要可以由同一份受跟踪的综合确认记录绑定，但必须保留各自独立 SHA-256。回答者真实性仍是人工边界，JSON 不能自证。

Required Skill 缺失时，安装是单独授权问题。必须展示 Skill ID、来源、将写入的位置、内容寻址方式、权限、风险/负担和不安装的阻断范围；只允许从本地受管包或 Codex 策展/推荐源安装。Advisory Skill 缺失只产生 warning，不应打断用户。Skill 安装不需要私钥，任何安装问题都不得索取或生成私钥。

重定位也是边界决定：先用 `reposition --plan` 只读展示精确变化、规则差异和失效集合，再单独询问是否应用该 plan hash。不得把重定位批准与继续实现、接受 warning 或发布批准捆绑。

目标修订同理：先运行 `revise-objectives --plan` 展示需求来源、目标 ID、治理成本和下游失效，再询问是否应用精确 plan hash。一次已确认的修订不需要在 lock-task、freeze 或 audit 阶段重复询问。

定位与 case 已固定后，主代理自动草拟 acceptance checkpoint，并一次展示每项外部可观察事实、case/assertion、预期结果、声明等级与 `notProven`。用户只确认或修改完整摘要，不承担逐哈希、逐字段核对；确认后 lock-task、execute 和 audit 不得重复询问。只有 checkpoint 语义、case/oracle、声明上限或确认记录变化时才重新确认并失效下游对象。

`HUMAN` checkpoint 不能由测试或 reviewer 自动批准。Owner decision 在同一候选上一次列全适用人工检查点，并逐项记录 `PASS | REJECT`；缺项、重复、漂移或任一拒绝只阻断相应 `ACCEPTED` 及以上声明，不外推到其他产品事实。

## 自动推进的人工边界

新项目必须在启动综合确认中明确选择以下一项；没有回答不得写控制面：

- `AUTO_LOCAL_TO_REVIEW`（推荐）— 风险 45/100；人工负担 20/100；影响：普通阶段自动规划、委派、实现、验证和里程碑提交，到固定人工复核点停止，不推送。
- `AUTO_PUSH_TO_REVIEW` — 风险 60/100；人工负担 15/100；影响：在本地自动路径上增加向精确绑定的既有 upstream 非强制推送；认证失败、非 fast-forward、远端身份漂移或工作区污染立即停止。
- `MANUAL_STAGE_CONFIRMATION` — 风险 25/100；人工负担 65/100；影响：保留逐阶段确认，不自动提交或推送。

三种选择只能映射到 [automation-advancement.md](automation-advancement.md) 中固定的 commit/push 权限组合。旧 Schema 3.2 项目没有策略时按手动模式兼容，不得把“继续开发”“沿用默认”或历史提交行为解释为自动授权。选择加入或改变模式必须先只读 `automation --plan`，再针对精确 plan hash 单独 `--apply`；这次 apply 是边界决定，会归档当前 task、失效下游记录并回到 `DRAFT`。

自动模式中的普通计划、实现、验证、整合和已授权里程碑提交/推送不是新的人工决策点，只发送非阻塞进度更新。遇到以下任一条件必须停止并把控制权交还用户：

- 自动检查点全部报告完毕并形成待复核候选；
- 任一 `HUMAN` checkpoint 或 owner decision；
- 目标、范围、case/oracle、风险、发行意图、自动化权限或其他锁定边界需要改变；
- R3、不可逆或需要额外授权的操作；
- 硬失败、推送冲突或用户中断。

自动 reviewer 只能提供候选绑定的诊断或审核输入，不能批准人工检查点、代替 owner decision 或运行 `accept`。即使任务没有主观质量门，候选闭合后也必须进行一次 owner review。到达复核点时生成外部缓存的 `index.html`、`status.json` 和 `summary.md`；Dashboard 只帮助人工理解当前证据与未证明边界，不能改变阶段、授予 claim、覆盖 evidence 或解除 blocker。

## 风险分 0–100

风险分是启发式排序，不是失败概率：

| 因子 | 最大分 |
| --- | ---: |
| 影响范围 | 20 |
| 不可逆性 | 20 |
| 模块耦合 | 15 |
| 可观察/验证缺口 | 15 |
| 权威或需求歧义 | 10 |
| 外部环境与依赖 | 10 |
| 安全、隐私与数据敏感度 | 10 |

`R0` 只表示只读。写任务：`R1=0–34`、`R2=35–69`、`R3=70–100`。发布、权限扩大、安全边界、破坏性数据操作和不可逆迁移无条件升级 R3。

展示风险级别前运行项目固定 runtime 或全局 wrapper 的 `risk --score`，不得由模型自由映射区间。

每个因子记录分数、证据和置信度。证据不足不得通过低分降低风险；标记低置信并请求必要的边界决定。

## 人工负担分 0–100

| 因子 | 最大分 |
| --- | ---: |
| 出现频率 | 30 |
| 所需时间 | 25 |
| 专业门槛 | 20 |
| 上下文切换 | 15 |
| 决策复杂度 | 10 |

负担分只帮助设计交互，不能推翻安全或授权要求。

## 决策与 warning 接受

决策记录绑定问题、选项、所选项、对象/范围、候选、决定人、时间和失效事件。人工批准不得外推。

硬检查不可豁免。warning 可以接受，但必须记录理由、精确范围、相关候选/合同和 `invalidatedBy`；相关输入、范围或候选变化后自动失效，高风险项可以额外设置日期。

每次候选冻结、验收、迁移和交接时，操作面只显示一个下一项人工决定；无则明确写“无”。
