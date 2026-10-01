"""跑哪个骨架：部署列出可用的、排好偏好，项目可以指定其中一个（结论 28）。

骨架不是产品概念，是开发者选项：它不在类型上，不在实例上，普通用户看不到。所以
「跑的是哪个」只有一个答法——`core/config.py` 的 `agent_harnesses` 加项目自己的
设置，再加房间那台机器挂着哪些——而这几条钉的就是那个答法的次序，以及它对一个这
套部署没有的名字说什么。

配错名字**不兜底**：兜底的那一版会让一套配错的部署安静地跑另一个骨架，而会话行是
用骨架当键的，于是没有一条功能测试会红，只有人会发现房间里换了个东西在说话。
"""

import pytest

from app.core.config import settings
from app.domain.agent.harness import (
    CLAUDE_CODE,
    CODEX,
    HARNESS_SETTING,
    HARNESSES,
    PI,
    harness_for,
    harness_on,
)


def _listing(monkeypatch, *names: str) -> None:
    monkeypatch.setattr(settings, "agent_harnesses", list(names))


def test_a_deployment_that_lists_nothing_runs_one_the_registry_knows(
    monkeypatch,
) -> None:
    """没配也得有一个答案——不然每一套没写这行 env 的部署都起不来。"""
    _listing(monkeypatch)
    assert harness_for(None) in HARNESSES


@pytest.mark.parametrize("said_nothing", [None, {}, {HARNESS_SETTING: ""}])
def test_a_project_that_says_nothing_runs_the_deployment_s_first_choice(
    monkeypatch, said_nothing
) -> None:
    _listing(monkeypatch, PI, CLAUDE_CODE)
    assert harness_for(said_nothing) == PI


def test_a_project_runs_the_listed_harness_it_names(monkeypatch) -> None:
    _listing(monkeypatch, CLAUDE_CODE, PI)
    assert harness_for({HARNESS_SETTING: PI}) == PI


def test_a_project_naming_one_the_deployment_does_not_list_runs_the_first_listed(
    monkeypatch,
) -> None:
    """项目指定的那个这套部署没列：退到部署偏好的第一个，不是拒绝这个项目。"""
    _listing(monkeypatch, CLAUDE_CODE)
    assert harness_for({HARNESS_SETTING: PI}) == CLAUDE_CODE


@pytest.mark.parametrize("listed", ["something-we-do-not-run", CODEX])
def test_a_deployment_listing_a_harness_it_does_not_run_refuses(
    monkeypatch, listed
) -> None:
    """名字写错、或者有适配层却没注册（今天的 Codex），这套部署都起不来。"""
    _listing(monkeypatch, CLAUDE_CODE, listed)
    with pytest.raises(ValueError, match=listed):
        harness_for(None)


def test_a_project_naming_a_harness_with_no_adapter_refuses() -> None:
    with pytest.raises(ValueError, match="something-we-do-not-run"):
        harness_for({HARNESS_SETTING: "something-we-do-not-run"})


def test_on_a_machine_without_the_project_s_choice_the_next_listed_one_runs(
    monkeypatch,
) -> None:
    _listing(monkeypatch, CLAUDE_CODE, PI)
    assert harness_on({HARNESS_SETTING: PI}, lambda name: name != PI) == CLAUDE_CODE
    assert harness_on(None, lambda name: name != CLAUDE_CODE) == PI


def test_on_a_machine_that_runs_none_of_them_nothing_is_chosen(monkeypatch) -> None:
    _listing(monkeypatch, CLAUDE_CODE, PI)
    assert harness_on({HARNESS_SETTING: PI}, lambda _: False) is None
