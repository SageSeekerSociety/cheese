"""跑哪个骨架，由部署设置答，项目可以盖过它（结论 28）。

骨架不是产品概念，是开发者选项：它不在类型上，不在实例上，普通用户看不到。所以
「跑的是哪个」只有一个答法——`core/config.py` 的 `agent_harness` 加项目自己的设
置——而这几条钉的就是那个答法的次序，以及它对一个这套部署没有的名字说什么。

配错名字**不兜底**：兜底的那一版会让一套配成 codex 的部署安静地跑 Claude Code，
而两边的会话行是用骨架当键的，于是没有一条功能测试会红，只有人会发现房间里换了
个东西在说话。
"""

import pytest

from app.core.config import settings
from app.domain.agent.harness import (
    CODEX,
    HARNESS_SETTING,
    HARNESSES,
    deployment_harness,
    harness_for,
)
from tests.support.stand_in_harness import registered

# 部署设置要指向一个有适配层、而且这套部署注册了的骨架；注册表今天只有 Claude
# Code——默认那个，指向它证明不了「设置赢了默认」。所以下面凡是要一个第二名字的
# 地方，先把 Codex 注册成替身。`harness_for` 那条不用：项目设置只认「有适配层」，
# 没注册的骨架是轮次开始时在房间里说出来的事，不是配置错误。


def test_a_deployment_that_says_nothing_runs_one_the_registry_knows() -> None:
    """没配也得有一个答案——不然每一套没写这行 env 的部署都起不来。"""
    assert deployment_harness() in HARNESSES


def test_the_deployment_setting_is_what_runs(monkeypatch) -> None:
    registered(monkeypatch, CODEX)
    monkeypatch.setattr(settings, "agent_harness", CODEX)
    assert deployment_harness() == CODEX


def test_a_project_can_run_something_else(monkeypatch) -> None:
    monkeypatch.setattr(settings, "agent_harness", "")
    assert harness_for({HARNESS_SETTING: CODEX}) == CODEX


@pytest.mark.parametrize("said_nothing", [None, {}, {HARNESS_SETTING: ""}])
def test_a_project_that_says_nothing_runs_the_deployment_s(
    monkeypatch, said_nothing
) -> None:
    registered(monkeypatch, CODEX)
    monkeypatch.setattr(settings, "agent_harness", CODEX)
    assert harness_for(said_nothing) == CODEX


def test_a_deployment_configured_for_a_harness_it_does_not_have_refuses(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "agent_harness", "something-we-do-not-run")
    with pytest.raises(ValueError, match="something-we-do-not-run"):
        deployment_harness()


def test_a_project_configured_for_a_harness_it_does_not_have_refuses() -> None:
    with pytest.raises(ValueError, match="something-we-do-not-run"):
        harness_for({HARNESS_SETTING: "something-we-do-not-run"})
