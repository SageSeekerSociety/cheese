"""撞上档位策略的调用变成给人的提议，或者一次说得出口的拒绝。

四条判据（结论 3 后半、结论 40 后半）：

  ① 档内的调用直接放行
  ② 超档 + 处置是「变提议」 → 产物是一条提议，这次调用没有发生
  ③ 超档 + 处置是「拒绝」   → 一次看得见的拒绝，不是悄悄降档（I27）
  ④ 要一台自托管机器，机主收到的是同一条提议路径

外加一条守卫：全仓「超档怎么办」只有 `app/domain/policy/gate.py` 一处回答。

这里全是纯的：闸门不碰库、不读时钟、不问「现在谁在看」，所以同一次调用问两遍得
到同一个答案。提议真的送到人手上那一段走 `announce` → 寻址 → 账本，在
`tests/integration/test_policy_gate_proposal.py` 里走完整条通路。
"""

import ast
import pathlib

import pytest

from app.domain.agent.market import (
    COMPUTE_TIERS,
    TIER_BYO,
    TIER_FRONTIER,
    TIER_INCLUDED,
    TIER_PREMIUM,
)
from app.domain.policy.gate import (
    ALLOWED_TIERS_KEY,
    DENY,
    OVER_TIER_KEY,
    PROPOSE,
    Allowed,
    Call,
    OverTier,
    Proposal,
    Resource,
    check,
    policy_of,
)


def _model(tier: str = TIER_FRONTIER) -> Call:
    return Call(
        resource=Resource.model,
        subject="fable",
        label="Claude Fable 5",
        tier=tier,
        approver="owner",
    )


def _machine(tier: str = TIER_BYO) -> Call:
    return Call(
        resource=Resource.machine,
        subject="dev-box-7",
        label="小王的工作站",
        tier=tier,
        approver="xiaowang",
    )


# —— ① 档内的调用直接放行 ————————————————————————————————————


@pytest.mark.parametrize("call", [_model(TIER_INCLUDED), _machine(TIER_BYO)])
@pytest.mark.parametrize("disposition", [DENY, PROPOSE])
def test_a_call_inside_the_allowed_tiers_just_goes_through(call, disposition):
    """档内就是档内，处置是什么都无关——处置只在超档时才有话说。"""
    policy = policy_of(
        {
            ALLOWED_TIERS_KEY: [TIER_INCLUDED, TIER_BYO],
            OVER_TIER_KEY: disposition,
        }
    )
    assert check(call, policy, "cheese") == Allowed(call)


@pytest.mark.parametrize("call", [_model(TIER_FRONTIER), _machine(TIER_PREMIUM)])
def test_a_project_that_has_not_restricted_tiers_lets_everything_through(call):
    """默认是不限档——今天所有项目的行为，部署窗口两个方向读得懂同一份设置。"""
    assert check(call, policy_of(None), "cheese") == Allowed(call)


def test_allowing_no_tier_at_all_is_not_the_same_as_saying_nothing():
    """空列表是「一档都不许」，不是「没说过」：两者的答案正好相反。"""
    with pytest.raises(OverTier):
        check(_model(TIER_INCLUDED), policy_of({ALLOWED_TIERS_KEY: []}), "cheese")


# —— ② 超档 + 变提议：产物是一条提议，调用没有发生 ————————————————


def test_an_over_tier_call_becomes_a_proposal_to_a_person():
    call = _model(TIER_FRONTIER)
    verdict = check(
        call,
        policy_of({ALLOWED_TIERS_KEY: [TIER_INCLUDED], OVER_TIER_KEY: PROPOSE}),
        "cheese",
    )
    assert isinstance(verdict, Proposal)
    # 下一步在这个人手上。
    assert verdict.approver == "owner"
    # 谁要的、要什么、为什么停下来，都在给人看的那一句里。
    assert "cheese" in verdict.content
    assert "Claude Fable 5" in verdict.content
    assert "owner" in verdict.content


def test_a_proposal_carries_the_call_that_did_not_happen():
    """产物是提议，`Allowed` 没有出现过——调用点拿不到一个可以照常执行的答案。"""
    call = _model(TIER_PREMIUM)
    verdict = check(
        call,
        policy_of({ALLOWED_TIERS_KEY: [TIER_INCLUDED], OVER_TIER_KEY: PROPOSE}),
        "cheese",
    )
    assert not isinstance(verdict, Allowed)
    assert verdict.call == call


def test_the_same_call_asked_twice_gets_the_same_answer():
    """纯函数：算两遍不会变成第二种处置，也不会变成第二次打扰。"""
    policy = policy_of({ALLOWED_TIERS_KEY: [TIER_INCLUDED], OVER_TIER_KEY: PROPOSE})
    assert check(_model(), policy, "cheese") == check(_model(), policy, "cheese")


# —— ③ 超档 + 拒绝：看得见的拒绝，不是悄悄降档 ——————————————————


def test_an_over_tier_call_is_refused_out_loud_when_that_is_the_disposition():
    with pytest.raises(OverTier) as refusal:
        check(
            _model(TIER_FRONTIER),
            policy_of({ALLOWED_TIERS_KEY: [TIER_INCLUDED], OVER_TIER_KEY: DENY}),
            "cheese",
        )
    # 拒绝说得出是什么被拒、凭哪一档被拒。
    assert "Claude Fable 5" in str(refusal.value)
    assert TIER_FRONTIER in str(refusal.value)


def test_refusing_is_the_default_disposition():
    """没写处置就是拒绝，和今天「解析不出来就报错」同一种失败（I27）。"""
    with pytest.raises(OverTier):
        check(_model(), policy_of({ALLOWED_TIERS_KEY: [TIER_INCLUDED]}), "cheese")


def test_an_unreadable_disposition_falls_back_to_refusing():
    with pytest.raises(OverTier):
        check(
            _model(),
            policy_of({ALLOWED_TIERS_KEY: [TIER_INCLUDED], OVER_TIER_KEY: "降档"}),
            "cheese",
        )


def test_a_refused_call_never_comes_back_as_a_cheaper_one():
    """悄悄降档是这条判据要杀的那种失败：屏幕上写着 A，跑的是 B。

    闸门一共只有三种出路，没有第四种，所以「返回一个别的模型」写不出来。
    """
    policy = policy_of({ALLOWED_TIERS_KEY: [TIER_INCLUDED], OVER_TIER_KEY: DENY})
    try:
        verdict = check(_model(TIER_FRONTIER), policy, "cheese")
    except OverTier:
        return
    pytest.fail(f"超档的调用被放行成了 {verdict!r}")


# —— ④ 要一台自托管机器，机主收到的是同一条提议路径 ————————————


def test_asking_for_someone_elses_machine_proposes_to_its_owner():
    """结论 40：自托管要机主点头。走的是模型那条一模一样的路。"""
    verdict = check(
        _machine(COMPUTE_TIERS["device"]),
        policy_of({ALLOWED_TIERS_KEY: [TIER_INCLUDED], OVER_TIER_KEY: PROPOSE}),
        "cheese",
    )
    assert isinstance(verdict, Proposal)
    assert verdict.approver == "xiaowang"
    assert "小王的工作站" in verdict.content


def test_a_machine_and_a_model_take_the_same_road():
    """一个闸门两个调用者：两种资源的答案是同一个类型、同一个形状。"""
    policy = policy_of({ALLOWED_TIERS_KEY: [TIER_INCLUDED], OVER_TIER_KEY: PROPOSE})
    model_verdict = check(_model(TIER_FRONTIER), policy, "cheese")
    machine_verdict = check(_machine(TIER_BYO), policy, "cheese")
    assert type(model_verdict) is type(machine_verdict) is Proposal
    assert model_verdict.approver != machine_verdict.approver


# —— 守卫：「超档怎么办」只有一处回答 ————————————————————————


def test_only_the_gate_knows_what_each_disposition_does():
    """两个处置（`DENY` / `PROPOSE`）只有 `policy/gate.py` 认识。

    别处引用其中任何一个，就是在自己回答「超档怎么办」——第二份声明，两份迟早
    不一致。调用点问的是 `check()`，写侧校验读的是 `DISPOSITIONS` 这个封闭集合，
    它只说「合法的处置有哪几个」，不说每一个意味着什么。
    """
    app_root = pathlib.Path(__file__).resolve().parents[2] / "app"
    gate_file = app_root / "domain" / "policy" / "gate.py"
    answers = {"DENY", "PROPOSE"}
    offenders = []
    for path in app_root.rglob("*.py"):
        if path == gate_file:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            named = (
                node.id
                if isinstance(node, ast.Name)
                else node.attr
                if isinstance(node, ast.Attribute)
                else None
            )
            if named in answers:
                offenders.append(f"{path.relative_to(app_root).as_posix()}:{named}")
    assert offenders == [], f"这些地方自己回答了「超档怎么办」：{offenders}"
