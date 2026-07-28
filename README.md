<div align="center">

# vibe-control

**把“人工盯住 Agent”变成“项目启动时建立可观察、可验证的控制面”。**

Observable control planes for VibeCoding and multi-agent software development.

![Version](https://img.shields.io/badge/version-0.3.5-2563eb)
![Maturity](https://img.shields.io/badge/maturity-DEVELOPMENT__DIAGNOSTIC-f59e0b)
![Schema](https://img.shields.io/badge/schema-3.2-7c3aed)
![Python](https://img.shields.io/badge/python-3.12-3776ab)

</div>

> [!WARNING]
> 当前公开包是 `0.3.5 DEVELOPMENT_DIAGNOSTIC`。仓库公开可用不等于已经完成正式封印；`formalClaimsAllowed=false`，不得外推为产品验收或发布通过。

## 它解决什么问题

VibeCoding 的典型失败往往不是“模型完全不会写代码”，而是开发过程中逐渐出现：目标漂移、局部测试冒充整体通过、实现与验收标准共同变化、跨会话证据丢失，以及审核范围无限外扩。

`vibe-control` 是面向产品负责人、主 Agent、实现 Worker 与独立 Auditor 的治理 Skill。它采用“强边界、弱流程”：只把可确定验证的事实做成硬检查，架构和实现策略仍由模型在已确认边界内自主决定。

## 核心能力

| 能力 | 作用 |
| --- | --- |
| 关键目标缰绳 | 从需求事实源推导并锁定 `KEY_OBJECTIVES.md`，阻止审计和修复逐轮跑偏。 |
| 检查点契约 | 在实现前固定“什么结果算通过”，绑定 case、oracle、assertion 与声明上限。 |
| 候选与证据闭包 | 绑定精确 commit/tree、输入哈希、逐 case provenance、transcript、产物与 counters。 |
| 有界审核 | 先审核预设检查点；普通探索发现有候选级预算，达到停止条件后结束审核。 |
| 多智能体边界 | 单一控制面写入者、隔离写入、无答案泄漏审核；Worker 回报不能自行批准。 |
| 跨宿主兼容 | 按实际能力选择 `CODEX_THREADS → SUBAGENTS → SERIAL`，不伪造宿主不存在的功能。 |
| 发行意图分级 | 区分本地实验、私有运行和外部发行，避免把同一套高强度门禁施加给所有项目。 |
| 便携安装身份 | 区分 Git 根、Git 子目录和无 Git 副本；普通下载不再被正式封印所需的 Git 前置误伤。 |
| 有界深度测试 | 长回归按叶级 case 并发执行，提供独立超时、实时进度和守恒的最终计数。 |

```mermaid
flowchart LR
    A["需求事实源"] --> B["关键目标"]
    B --> C["任务与检查点"]
    C --> D["候选冻结"]
    D --> E["真实执行证据"]
    E --> F["独立审核"]
    F --> G["人工决定 / 交接"]
```

## 快速开始

### 1. 安装到 Codex

在 Codex 中使用内置 Skill Installer（推荐）：

```text
使用 $skill-installer 安装 https://github.com/jiay98528-dev/vibe-control/tree/main/skill
```

也可以手工安装（PowerShell）：

```powershell
git clone https://github.com/jiay98528-dev/vibe-control.git vibe-control-repo
Copy-Item -Recurse .\vibe-control-repo\skill "$env:USERPROFILE\.codex\skills\vibe-control"
```

完成后运行普通安装自检：

```powershell
python "$env:USERPROFILE\.codex\skills\vibe-control\scripts\validate_installation.py" `
  --skill-root "$env:USERPROFILE\.codex\skills\vibe-control"
```

标准下载应报告 `status=PASS`、`sourceKind=PORTABLE_COPY`、`packageMode=DEVELOPMENT` 和 `maxClaimLevel=DEVELOPMENT_CHECKED`。这证明安装内容可用于诊断开发，不证明 Git 来源或正式封印。`validate_package_release.py` 与两套深度对抗回归属于维护者封印流程，不是普通安装步骤。

### 2. 启动项目

在新项目或需要接入治理的现有项目中调用：

```text
使用 $vibe-control 启动这个项目。先建立关键目标、发行意图和验收检查点，再进入实现。
```

Skill 会先询问项目的预期发行状态，然后建立项目本地 `.vibe-control/` 控制面。它不会仅根据仓库名称、技术栈或项目体量替用户推断发行意图。

### 3. 非 Codex Agent

将 [`skill/`](skill/) 目录安装到宿主的 Skill 目录。运行时按实际工具能力降级：

1. 有 Codex task/thread 工具：`CODEX_THREADS`；
2. 没有 Codex 跨会话能力、但有子智能体工具：`SUBAGENTS`；
3. 两者都没有：`SERIAL`。

Skill 不设置固定子智能体数量上限，但仍服从宿主容量、任务可分解性、文件所有权与工作树隔离边界。

## 当前成熟度

- 版本：`0.3.5`
- Schema：`3.2`
- 首个真实运行环境：Windows + Python 3.12
- 包清单：150 个受管条目、runtime 44 个受管条目，SHA-256 内容寻址
- 姿态：`DEVELOPMENT_DIAGNOSTIC`
- 正式声明：`formalClaimsAllowed=false`
- 当前没有 `v0.3.5` release tag 或对应 package audit tag

公开仓库可以用于研究、诊断开发和本地试用；它目前不能作为 `FORMAL_GATE_READY` 的证明。

## 明确边界

- 控制器判断证据和状态是否闭合，不判断产品“是否好用”。
- 普通本地实验和私有运行不需要私钥；签名链只属于明确的外部 R3 发行路径。
- `PASS`、零退出码或 manifest 完整不能单独证明 UI、性能、安全或发布质量。
- 本项目不是许可、收费、私钥托管或软件分发平台。

## 仓库结构

```text
.
├─ README.md          # 面向用户的仓库入口
└─ skill/             # 可独立安装与校验的 vibe-control Skill 包
   ├─ SKILL.md
   ├─ agents/
   ├─ assets/
   ├─ references/
   ├─ scripts/
   └─ package-manifest.json
```

## 深入阅读

- [Skill 路由与核心边界](skill/SKILL.md)
- [关键目标机制](skill/references/key-objectives.md)
- [检查点契约](skill/references/checkpoint-contract.md)
- [任务控制](skill/references/task-control.md)
- [跨宿主多智能体路由](skill/references/multi-session-routing.md)
- [证据政策](skill/references/evidence-policy.md)
- [控制器保证闭包](skill/references/controller-assurance.md)

## 反馈问题

请在 [Issues](https://github.com/jiay98528-dev/vibe-control/issues) 中提供：精确 commit、宿主/模型、复现步骤、原始输出、预期行为，以及问题影响的检查点或声明等级。不要仅用严重度标签扩大当前任务范围。

## 许可

当前仓库尚未提供 `LICENSE` 文件。仓库公开不代表自动授予再分发或商业使用许可；如需明确许可，请先联系维护者。
