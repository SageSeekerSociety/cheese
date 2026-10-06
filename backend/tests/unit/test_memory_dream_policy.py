"""整理记忆（dream）的判据：什么时候跑、什么时候不跑、什么时候不认账。

这里测的是**规矩**，不是那轮会话跑得好不好（跑起来的那一半在集成测试里）。
三条规矩各自有一个会让人吃亏的地方，所以各测各的：

- **阈值和间隔都可以按项目配**，而配坏了要退回默认值而不是关掉整理——设置是给人
  改的，改坏一个字段不该静默地让一个项目的记忆再也没人整理。
- **删得太多就当没删**：`removal_refused` 的两个条件（比例和条数）都要过。
- **prompt 里那三段必须都在**：它们是照搬 CC 来的，掉一段不会报错，只会让整理
  少做一件事——而少做的那件事（不许把 private 升进 team、不和 CLAUDE.md 抢着改）
  是错的写法里最难发现的一类。
"""

from datetime import UTC, datetime, timedelta

from app.domain.memory.dream import (
    DEFAULT_MIN_INTERVAL,
    DEFAULT_THRESHOLD_OUTPUT_TOKENS,
    DreamSettings,
    briefing,
    dream_settings,
    due_decision,
    refused_scopes,
    removal_refused,
    trim_scopes,
)
from app.domain.memory.dream_prompt import DreamBriefing, dream_prompt
from app.domain.memory.tree import BULK_DELETE_MIN

_NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _settings(**section) -> DreamSettings:
    return dream_settings({"memory_dream": section} if section else None)


# --- 阈值和间隔：按项目配，配坏了退回默认 ---------------------------------


def test_a_project_with_no_settings_gets_the_defaults():
    for raw in (None, {}, {"memory_dream": None}, {"memory_dream": "nonsense"}):
        settings = dream_settings(raw)
        assert settings.threshold_output_tokens == DEFAULT_THRESHOLD_OUTPUT_TOKENS
        assert settings.min_interval == DEFAULT_MIN_INTERVAL


def test_a_project_can_set_its_own_numbers():
    settings = _settings(threshold_output_tokens=500, min_interval_hours=0.5)
    assert settings.threshold_output_tokens == 500
    assert settings.min_interval == timedelta(minutes=30)


def test_a_broken_number_falls_back_instead_of_switching_the_dream_off():
    """一个写坏的字段不能静默地关掉一个项目的整理。

    退回默认值的代价是「还按老规矩跑」；不退回的代价是记忆再也没人整理，而且没有
    任何地方会报错——后台工作最坏的样子就是「以为它在跑」。
    """
    for bad in ("500", 0, -1, True, None):
        settings = _settings(threshold_output_tokens=bad, min_interval_hours=bad)
        assert settings.threshold_output_tokens == DEFAULT_THRESHOLD_OUTPUT_TOKENS
        assert settings.min_interval == DEFAULT_MIN_INTERVAL


# --- 该不该跑：量到了、也够久了 -------------------------------------------


def test_a_project_that_has_not_spent_enough_is_not_due():
    decision = due_decision(
        _settings(threshold_output_tokens=1000),
        tokens=999,
        last_dream_at=None,
        now=_NOW,
    )
    assert not decision.due
    assert decision.reason == "还没到量"
    assert decision.tokens == 999


def test_a_project_that_spent_enough_and_waited_long_enough_is_due():
    decision = due_decision(
        DreamSettings(1000, timedelta(hours=4)),
        tokens=1000,
        last_dream_at=_NOW - timedelta(hours=4),
        now=_NOW,
    )
    assert decision.due
    assert decision.reason == ""


def test_a_fresh_project_is_not_held_back_by_an_interval_it_never_waited():
    """从没整理过的项目只看量：它没有「距上次多久」这回事。"""
    assert due_decision(
        DreamSettings(10, timedelta(days=30)), tokens=10, last_dream_at=None, now=_NOW
    ).due


def test_a_second_dream_waits_for_the_interval_even_when_the_tokens_are_there():
    decision = due_decision(
        DreamSettings(1000, timedelta(hours=4)),
        tokens=99_999,
        last_dream_at=_NOW - timedelta(hours=3, minutes=59),
        now=_NOW,
    )
    assert not decision.due
    assert decision.reason == "距上次整理还没到间隔"


# --- 删得太多就当没删 ------------------------------------------------------


def test_deleting_a_few_files_is_ordinary_pruning():
    assert not removal_refused(BULK_DELETE_MIN, 100)
    assert not removal_refused(BULK_DELETE_MIN, BULK_DELETE_MIN)


def test_deleting_most_of_a_small_scope_is_ordinary_pruning():
    """小树里删掉一半也可能只是两三条，那不值当拦。"""
    assert not removal_refused(2, 4)
    assert not removal_refused(3, 6)


def test_deleting_over_half_and_over_three_at_once_is_refused():
    # 4 条里删 4 条、7 条里删 4 条都过；8 条里删 4 条正好是「一半」，不过。
    assert removal_refused(BULK_DELETE_MIN + 1, BULK_DELETE_MIN + 1)
    assert removal_refused(BULK_DELETE_MIN + 1, 2 * BULK_DELETE_MIN + 1)
    assert not removal_refused(4, 8)


def _tree(prefix: str, count: int) -> dict[str, str]:
    return {f"{prefix}/{chr(ord('a') + i)}.md": "x" for i in range(count)}


def test_refused_scopes_counts_each_scope_on_its_own():
    """team 和某个人的 private 是两棵树：一个人清空自己那棵，不该拿团队那棵作数。

    分开数而不是合起来数，是因为这两种删除说的不是一件事：team 少掉一大半更像是
    那棵树出了事，而一个人的 private 少掉一大半可能只是他自己清了一遍——拿一个去
    替另一个作数，就成了「谁都别删」。
    """
    before = {**_tree("team", 5), **_tree("private/bob", 5)}
    # 各少一条：两条都不算「删多了」。
    assert refused_scopes(before, {**_tree("team", 4), **_tree("private/bob", 4)}) == {}
    # bob 那棵被清光，team 一条没动：只拦 bob 那一棵。
    refused = refused_scopes(before, _tree("team", 5))
    assert set(refused) == {"private/bob"}
    assert len(refused["private/bob"]) == 5
    # 两棵各自少掉一大半：两棵都拦，各报各的那几条。
    refused = refused_scopes(before, {**_tree("team", 1), **_tree("private/bob", 1)})
    assert set(refused) == {"team", "private/bob"}
    assert refused["team"] == ("team/b.md", "team/c.md", "team/d.md", "team/e.md")
    assert refused["private/bob"][0] == "private/bob/b.md"


def test_a_scope_that_lost_nothing_is_not_reported():
    assert refused_scopes({"team/a.md": "a"}, {"team/a.md": "a"}) == {}


# --- 提示词预算：索引永远在 -------------------------------------------------


def test_the_index_always_makes_it_into_the_prompt():
    """超预算时抽掉的是正文，不是索引。

    索引里那 150 字符正是「这条讲什么」，而它恰恰是整理唯一需要的东西——连有哪
    几条记忆都不知道，就只剩猜了。
    """
    files = {
        "MEMORY.md": "## 记忆\n- [a](a.md) — 讲 a",
        "a.md": "x" * 30_000,
    }
    kept = trim_scopes({"team": files})["team"]
    assert "MEMORY.md" in kept
    assert "a.md" not in kept


def test_a_small_tree_goes_in_whole():
    files = {"MEMORY.md": "- a", "a.md": "正文"}
    assert trim_scopes({"team": files})["team"] == files


def test_a_briefing_keeps_every_scope_it_was_given():
    scopes = {"team": {"MEMORY.md": "- a"}, "private/bob": {"MEMORY.md": "- b"}}
    out = briefing(scopes, ["### <#1> 房间"], code_project=True)
    assert set(out.scopes) == {"team", "private/bob"}
    assert out.code_project


# --- 照搬 CC 的那三段：掉一段不会报错，只会让整理少做一件事 ---------------


def test_the_dream_prompt_has_all_four_phases_in_order():
    prompt = dream_prompt(DreamBriefing(scopes={}, rooms=[], code_project=False))
    phases = [
        "## 第一阶段 —— 摸底",
        "## 第二阶段 —— 找新信号",
        "## 第三阶段 —— 合并",
        "## 第四阶段 —— 修剪，并把索引压回去",
    ]
    positions = [prompt.index(phase) for phase in phases]
    assert positions == sorted(positions)


def test_the_dream_prompt_keeps_the_two_hard_rules():
    """两条规矩必须原样在：private 不许升进 team、和仓库的说明文件冲突只标注。

    它们不影响「整理跑没跑起来」，只影响跑偏的那一次有没有人拦住——而跑偏一次就
    是某个人的私人偏好被写进了全项目共看的那棵树，事后无从分辨哪一条本来是私人的。
    """
    for code_project in (True, False):
        prompt = dream_prompt(
            DreamBriefing(scopes={}, rooms=[], code_project=code_project)
        )
        assert "不许把 private 的内容升级进 team" in prompt
        assert "拿不准就留着" in prompt
    code = dream_prompt(DreamBriefing(scopes={}, rooms=[], code_project=True))
    assert "不要改说明文件" in code


def test_the_dream_prompt_says_what_it_may_write_and_what_it_may_not():
    prompt = dream_prompt(DreamBriefing(scopes={}, rooms=[], code_project=False))
    assert ".cheese/memory" in prompt
    assert "删除" in prompt


def test_a_code_project_is_told_about_its_repository_and_a_doc_project_is_not():
    code = dream_prompt(DreamBriefing(scopes={}, rooms=[], code_project=True))
    doc = dream_prompt(DreamBriefing(scopes={}, rooms=[], code_project=False))
    assert "## 仓库\n" in code
    assert "## 和仓库的说明文件核对" in code
    assert "## 仓库\n" not in doc
    assert "## 和仓库的说明文件核对" not in doc
    assert "CLAUDE.md" not in doc


def test_a_code_project_is_not_told_which_instruction_file_it_has():
    """A hosted repository may have a CLAUDE.md, an AGENTS.md, or none: the
    prompt has it look, and never says one is there or already in front of it."""
    code = dream_prompt(DreamBriefing(scopes={}, rooms=[], code_project=True))
    assert "AGENTS.md" in code
    assert "CLAUDE.md 在仓库根目录" not in code
    assert "系统提示词里有这个项目的 CLAUDE.md" not in code
    assert "这一段其余跳过" in code


def test_the_briefing_carries_the_rooms_into_the_prompt():
    prompt = dream_prompt(
        DreamBriefing(
            scopes={"team": {"MEMORY.md": "- a"}},
            rooms=["### <#7> 房间\n实况文档：\n在做 X"],
            code_project=False,
        )
    )
    assert "<#7> 房间" in prompt
    assert "MEMORY.md" in prompt
