#!/usr/bin/env python3
"""Documentation and routing contract checks for vibe-control 0.4.0."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (SKILL_ROOT / relative).read_text(encoding="utf-8")


class V040SkillStructureTests(unittest.TestCase):
    def test_skill_is_concise_and_routes_new_references_directly(self) -> None:
        text = read("SKILL.md")
        self.assertLessEqual(len(text.splitlines()), 180)
        for name in (
            "0.4.0-requirements.md",
            "progress-dashboard.md",
            "execution-routing.md",
            "multi-session-routing.md",
            "human-decisions.md",
            "schema-guide.md",
        ):
            self.assertIn(f"references/{name}", text)

        for target in re.findall(r"\]\(([^)]+\.md)\)", text):
            self.assertFalse(target.startswith("../"), target)
            self.assertTrue((SKILL_ROOT / target).is_file(), target)

    def test_version_and_development_posture_are_explicit(self) -> None:
        combined = "\n".join(
            (read("SKILL.md"), read("AGENTS.md"), read("CLAUDE.md"))
        )
        self.assertIn("0.4.0 DEVELOPMENT_DIAGNOSTIC", combined)
        self.assertIn("formalClaimsAllowed=false", combined)
        self.assertIn("DEVELOPMENT_CHECKED", combined)
        self.assertIn("不得创建或改写任何 `v0.4.0`", combined)

    def test_dashboard_is_first_and_local_history_is_clearable(self) -> None:
        skill = read("SKILL.md")
        dashboard = read("references/progress-dashboard.md")
        self.assertRegex(
            skill,
            r"Before inspecting, asking a boundary question, or writing the project, initialize",
        )
        for token in (
            "progress-ledger.json",
            "progress --project <root> --action clear",
            "--confirm <project-instance-id>",
            "不写项目",
            "不自动过期",
            "本机临时数据",
        ):
            self.assertIn(token, dashboard)

    def test_default_automation_and_side_effect_ceiling(self) -> None:
        automation = read("references/automation-advancement.md")
        self.assertIn("mode = AUTO_LOCAL_TO_REVIEW", automation)
        self.assertIn("commitPolicy = MILESTONE_COMMITS", automation)
        self.assertIn("pushPolicy = NONE", automation)
        self.assertIn("不再为普通阶段逐次询问", automation)
        for forbidden in ("force-push", "merge", "rebase", "tag", "release", "`accept`"):
            self.assertIn(forbidden, automation)

    def test_team_first_routing_and_role_separation(self) -> None:
        routing = read("references/multi-session-routing.md")
        execution = read("references/execution-routing.md")
        self.assertIn("`TEAM`", routing)
        self.assertIn("`SUBAGENT`", routing)
        self.assertIn("`SERIAL`", routing)
        self.assertLess(routing.index("`TEAM`"), routing.index("`SUBAGENT`"))
        self.assertLess(routing.index("`SUBAGENT`"), routing.index("`SERIAL`"))
        self.assertIn("NO_SKILL_FIXED_LIMIT", routing)
        self.assertIn("宿主规定", routing)
        self.assertIn("跨里程碑", routing)
        self.assertIn("边界窄、一次性", routing)
        for role in ("Coordinator", "Implementer", "Executor", "Auditor", "Owner"):
            self.assertIn(role, execution)
        self.assertIn("不得为了制造更多 PASS 盲跑全量测试", execution)

    def test_guard_classes_keep_hard_boundaries_and_soft_process(self) -> None:
        execution = read("references/execution-routing.md")
        for guard in (
            "ACTION_GUARD",
            "CLAIM_GUARD",
            "HUMAN_DECISION",
            "ENVIRONMENT_BLOCKED",
            "ADVISORY",
        ):
            self.assertIn(guard, execution)
        self.assertIn("固定阶段顺序", execution)
        self.assertIn("最低核心", execution)
        self.assertIn("不得因严重度", execution)

    def test_plain_language_and_three_next_actions_are_required(self) -> None:
        dashboard = read("references/progress-dashboard.md")
        schema = read("references/schema-guide.md")
        self.assertIn("给没有开发背景的人看的说明", dashboard)
        for field in (
            "projectPurpose",
            "whatWasDone",
            "whatWorksNow",
            "whatStillDoesNotWork",
            "userImpact",
            "canContinue",
            "canRelease",
        ):
            self.assertIn(field, schema)
        for option in ("RECOMMENDED", "ALTERNATIVE", "OPEN"):
            self.assertIn(option, dashboard)
        self.assertIn("结构化提问工具时必须调用", dashboard)
        self.assertIn("后台节点更新不得弹出问题", dashboard)

    def test_scorecard_is_reproducible_and_not_a_time_estimate(self) -> None:
        dashboard = read("references/progress-dashboard.md")
        for domain in (
            "FUNCTIONALITY",
            "ROBUSTNESS_SECURITY",
            "AUDIT",
            "PROCESS",
        ):
            self.assertIn(domain, dashboard)
        for weight in ("40%", "25%", "20%", "15%"):
            self.assertIn(weight, dashboard)
        self.assertIn("不是剩余工时预测", dashboard)
        self.assertIn("N/A", dashboard)

    def test_ui_metadata_matches_current_route(self) -> None:
        metadata = read("agents/openai.yaml")
        self.assertIn("$vibe-control 0.4.0", metadata)
        self.assertIn("Schema 4.0", metadata)
        self.assertIn("TEAM → SUBAGENT → SERIAL", metadata)
        self.assertNotIn("CODEX_THREADS", metadata)


if __name__ == "__main__":
    unittest.main()
