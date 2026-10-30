"""整理记忆（dream）的判据：什么时候跑、什么时候不跑、什么时候不认账。

这里测的是**规矩**，不是那几次调用答得好不好（跑起来的那一半在集成测试里）。
几条规矩各自有一个会让人吃亏的地方，所以各测各的：

- **阈值和间隔都可以按项目配**，而配坏了要退回默认值而不是关掉整理——设置是给人
  改的，改坏一个字段不该静默地让一个项目的记忆再也没人整理。
- **删得太多就当没删**：`removal_refused` 的两个条件（比例和条数）都要过。
- **prompt 里那几条必须都在**：它们是照搬 CC 来的，掉一条不会报错，只会让整理
  少做一件事——而少做的那件事（修剪 team 要保守）是错的写法里最难发现的一类。
- **一次调用的答案只按它说的改**：漏写一个文件不是删了它，一个带目录的名字整份
  不认。
"""

from datetime import UTC, datetime, timedelta

from app.domain.memory.dream import (
    DEFAULT_MIN_INTERVAL,
    DEFAULT_THRESHOLD_OUTPUT_TOKENS,
    DreamAnswerError,
    DreamSettings,
    dream_answer,
    dream_settings,
    due_decision,
    refused_scopes,
    removal_refused,
    trim_tree,
)
from app.domain.memory.dream_prompt import (
    PRIVATE_DREAM_SYSTEM,
    TEAM_DREAM_SYSTEM,
    private_dream_prompt,
    team_dream_prompt,
)
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
    kept = trim_tree(files)
    assert "MEMORY.md" in kept
    assert "a.md" not in kept


def test_a_small_tree_goes_in_whole():
    files = {"MEMORY.md": "- a", "a.md": "正文"}
    assert trim_tree(files) == files


# --- 照搬 CC 的那几条：掉一条不会报错，只会让整理少做一件事 ---------------


def test_the_team_pass_is_told_to_prune_conservatively():
    """修剪 team 拿不准就留着：跑偏一次就是别人的一条承重笔记被删掉。"""
    assert "拿不准就留着" in TEAM_DREAM_SYSTEM
    assert "不要删" in TEAM_DREAM_SYSTEM


def test_the_team_pass_reads_the_team_tree_and_nothing_else():
    """整理只整理、不提取（#1336）：team 那一份的输入只有 team 的树。"""
    prompt = team_dream_prompt({"MEMORY.md": "- [a](a.md) — a", "a.md": "A"})
    assert "- [a](a.md) — a" in prompt
    assert "房间" not in prompt


def test_the_private_pass_reads_the_persons_tree_and_the_team_index():
    """private 那一份看得见两边，所以「只是重复了 team 的那条」只在这里删。"""
    prompt = private_dream_prompt({"x.md": "X"}, "- [t](t.md) — t")
    assert "X" in prompt and "- [t](t.md) — t" in prompt
    assert "又说了一遍" in PRIVATE_DREAM_SYSTEM
    assert "又说了一遍" not in TEAM_DREAM_SYSTEM


def test_both_passes_are_told_the_shape_of_the_answer():
    for system in (TEAM_DREAM_SYSTEM, PRIVATE_DREAM_SYSTEM):
        assert '"write"' in system and '"delete"' in system


# --- 一次调用的答案：只按它说的改 ---------------------------------------------


def test_an_answer_changes_only_what_it_names():
    """模型漏写的文件原样留着：一次没提到不等于删掉。"""
    before = {"MEMORY.md": "- [a](a.md) — a\n- [b](b.md) — b", "a.md": "A", "b.md": "B"}
    answer = dream_answer(
        before,
        '{"write": {"MEMORY.md": "- [a](a.md) — a"}, "delete": ["b.md"],'
        ' "summary": "删了 b"}',
    )
    assert answer.tree == {"MEMORY.md": "- [a](a.md) — a", "a.md": "A"}
    assert answer.summary == "删了 b"


def test_an_answer_that_names_another_directory_is_refused_whole():
    """一个带目录的名字可能落进别人的树：整份不认，一条都不写。"""
    before = {"a.md": "A"}
    for answer in (
        '{"write": {"../alice/a.md": "x"}}',
        '{"write": {"team/a.md": "x"}}',
        '{"delete": ["../alice/a.md"]}',
        "这不是 JSON",
    ):
        try:
            dream_answer(before, answer)
        except DreamAnswerError:
            continue
        raise AssertionError(f"accepted: {answer}")
