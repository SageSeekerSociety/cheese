"""一台机器只有一种启动环境，所以代理要自己把 Claude Code 扶起来（结论 46）。

A machine is launched with no base URL, this proxy on `HTTPS_PROXY` and a fake
ticket — one shape, whether or not the deployment owns an Anthropic
subscription at all. Claude Code asks for four things on its way up that have
nothing to do with inference (identity, settings, policy, telemetry) plus its
feature flags, and every one of them has to be answered here or the process
never reaches a prompt.

This file is the answer table's contract: one assertion per row, status code
plus the fields a client must find. It also covers the other half of the
control point — the launch environment names no model, so the model resolved at
admission has to reach the one place either pool reads a model from, which is
the request body.
"""

import importlib.util
import json
from pathlib import Path

import pytest

CORE = (
    Path(__file__).resolve().parents[3]
    / "deploy"
    / "metering-proxy"
    / "cheese_billing_core.py"
)
spec = importlib.util.spec_from_file_location("cheese_billing_core", CORE)
assert spec and spec.loader
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

PROJECT = "11111111-1111-1111-1111-111111111111"
TOPIC = "22222222-2222-2222-2222-222222222222"


def _answer(path: str, host: str = "api.anthropic.com", rc: bool = True):
    return core.control_answer(host, path, PROJECT, TOPIC, rc=rc)


# —— 应答表：每条路径一条断言（状态码 + 必需字段）————————————————————


def test_identity_is_cheeses_own_and_never_an_anthropic_account():
    answer = _answer("/api/oauth/profile")
    assert answer.status == 200
    body = json.loads(answer.body)
    assert body["account"]["uuid"] == TOPIC
    assert body["organization"]["uuid"] == PROJECT
    assert "@" in body["account"]["email"]
    # Whatever the deployment's own subscription is called, it is not this.
    assert "anthropic.com" not in body["account"]["email"]


def test_settings_are_answered_empty_rather_than_fetched():
    answer = _answer("/api/claude_code/settings")
    assert answer.status == 204
    assert answer.body == b""


def test_policy_allows_the_remote_control_the_platform_drives_every_turn_through():
    answer = _answer("/api/claude_code/policy_limits")
    assert answer.status == 200
    body = json.loads(answer.body)
    assert body["restrictions"]["allow_remote_control"]["allowed"] is True


def test_feature_evaluation_turns_on_the_bridge_cheese_needs():
    answer = _answer("/api/eval/anything?client=cli")
    assert answer.status == 200
    features = json.loads(answer.body)["features"]
    assert features["tengu_ccr_bridge"]["defaultValue"] is True
    assert features["tengu_ccr_v2_bridge_create_cli"]["defaultValue"] is True
    assert features["tengu_ccr_v2_session_crud_cli"]["defaultValue"] is True


def test_a_session_without_rc_is_still_answered_here_but_told_nothing_is_on():
    """Answered either way — the table is what boots the process, and nothing
    on it may reach Anthropic. What differs is the content: the bridge flags
    are what make Claude Code open `/v1/code/…`, and a session whose token
    carries no rc claim has no Cheese route for those to take."""
    answer = _answer("/api/eval/anything?client=cli", rc=False)
    assert answer.status == 200
    assert json.loads(answer.body)["features"] == {}


def test_telemetry_is_consumed_here_whichever_host_it_was_sent_to():
    """RC payloads carry control-session identifiers; forwarding them would take
    both the payload and an upstream credential past this boundary."""
    for host, path in (
        ("api.anthropic.com", "/api/event_logging/v2/batch"),
        ("api.statsig.com", "/v1/rgstr"),
        ("statsig.anthropic.com", "/v1/initialize"),
    ):
        answer = core.control_answer(host, path, PROJECT, TOPIC, rc=True)
        assert answer is not None, (host, path)
        assert answer.status == 200
        assert json.loads(answer.body) == {}


def test_a_model_request_is_not_in_the_table():
    """The table answers everything a boot needs and nothing a turn needs: an
    inference request has to reach a pool, and one answered here would be a
    silent, fabricated reply."""
    assert _answer("/v1/messages") is None
    assert _answer("/v1/messages/count_tokens") is None


# —— 绑定解析出来的模型进到请求体 ————————————————————————————————


def _rewrite(
    body: bytes,
    model: str = "claude-opus-5",
    chunk: int = 1 << 20,
    keep_haiku: bool = False,
) -> bytes:
    rewrite = core.ModelRewrite(model, keep_haiku=keep_haiku)
    out = b"".join(
        rewrite.feed(body[i : i + chunk]) for i in range(0, len(body), chunk)
    )
    return out + rewrite.feed(b"")


def test_the_admitted_model_replaces_what_the_client_asked_for():
    body = json.dumps(
        {"model": "claude-sonnet-4-5", "max_tokens": 1024, "messages": []}
    ).encode()
    assert json.loads(_rewrite(body))["model"] == "claude-opus-5"


def test_everything_else_in_the_body_survives_byte_for_byte():
    original = {
        "model": "claude-sonnet-4-5",
        "system": "你是芝士。",
        "messages": [{"role": "user", "content": "跑一下测试"}],
        "tools": [{"name": "Bash"}],
        "stream": True,
    }
    rewritten = json.loads(_rewrite(json.dumps(original).encode()))
    assert rewritten == {**original, "model": "claude-opus-5"}


def test_a_body_arriving_in_pieces_is_rewritten_the_same_way():
    """The proxy streams request bodies — holding a grown conversation in RAM is
    what OOM-killed it — so the member can straddle any chunk boundary."""
    body = json.dumps(
        {"max_tokens": 8, "model": "claude-sonnet-4-5", "messages": []}
    ).encode()
    for size in (1, 3, 7, 16, 64):
        assert json.loads(_rewrite(body, chunk=size))["model"] == "claude-opus-5"


def test_the_word_model_inside_a_message_is_not_the_model():
    """A naive search would rewrite what a person typed. Read as JSON, the only
    candidate is the member at the top level."""
    body = json.dumps(
        {
            "messages": [{"role": "user", "content": '{"model":"claude-haiku-4-5"}'}],
            "metadata": {"model": "nested"},
            "model": "claude-sonnet-4-5",
        }
    ).encode()
    rewritten = json.loads(_rewrite(body))
    assert rewritten["model"] == "claude-opus-5"
    assert rewritten["metadata"]["model"] == "nested"
    assert "claude-haiku-4-5" in rewritten["messages"][0]["content"]


def test_a_body_with_no_model_member_is_forwarded_unchanged_and_says_so():
    """Not a third exit. The request still goes where admission sent it; what
    this records is that the turn ran on the client's own choice, which is the
    one thing the control point cannot silently swallow."""
    body = json.dumps({"messages": []}).encode()
    rewrite = core.ModelRewrite("claude-opus-5")
    assert rewrite.feed(body) == b""
    assert rewrite.feed(b"") == body
    assert rewrite.missed is True


def test_a_head_larger_than_the_limit_is_released_rather_than_held():
    """The memory bound is the whole point: a body whose model member never
    arrives must not turn into an unbounded buffer."""
    body = json.dumps({"system": "x" * 4096, "model": "claude-sonnet-4-5"}).encode()
    rewrite = core.ModelRewrite("claude-opus-5", limit=256)
    out = rewrite.feed(body[:512]) + rewrite.feed(body[512:]) + rewrite.feed(b"")
    assert out == body
    assert rewrite.missed is True


@pytest.mark.parametrize(
    "supply",
    [
        {"pool": "gateway", "key": "sk-virt-9", "model": "glm-5.2"},
        {"pool": "subscription", "model": "claude-opus-5"},
    ],
)
def test_the_model_travels_on_the_admission_answer(monkeypatch, supply):
    """One round trip says may-it-run, which pool, and which model — the proxy
    has no second source for any of the three."""
    import io
    from unittest import mock

    captured = json.dumps({"data": {"allow": True, "supply": supply}}).encode()

    class _Resp:
        def __enter__(self):
            return io.BytesIO(captured)

        def __exit__(self, *a):
            return False

    with mock.patch.object(core.urllib.request, "urlopen", return_value=_Resp()):
        verdict = core._post_admission("http://backend/llm/admission", "tok", 3.0)

    assert verdict.model == supply["model"]
    assert verdict.pool == supply["pool"]


def test_an_admission_answer_that_names_no_model_leaves_the_body_alone():
    """A backend too old to say, or one that refused: nothing is invented here."""
    assert core.Verdict(True, "ok").model == ""


def test_the_subscription_leaves_the_clis_own_background_requests_on_haiku():
    """Claude Code names the haiku family for work it does on its own account —
    session titles, file-path suggestions — and the subscription pool serves
    that family. Rewriting those to the binding means a project bound to opus
    writes its session titles with opus, for work nobody bound to anything."""
    body = json.dumps({"model": "claude-3-5-haiku-20241022", "messages": []}).encode()
    assert json.loads(_rewrite(body, keep_haiku=True))["model"] == (
        "claude-3-5-haiku-20241022"
    )
    # The turn itself still gets the binding.
    turn = json.dumps({"model": "claude-sonnet-4-5", "messages": []}).encode()
    assert json.loads(_rewrite(turn, keep_haiku=True))["model"] == "claude-opus-5"


def test_the_gateway_rewrites_a_haiku_request_too():
    """The opposite case, and why this is a flag rather than a rule: LiteLLM
    does not serve `claude-3-5-haiku-*` at all, so a request left naming it
    fails outright."""
    body = json.dumps({"model": "claude-3-5-haiku-20241022", "messages": []}).encode()
    assert json.loads(_rewrite(body, model="glm-5.2"))["model"] == "glm-5.2"


def test_a_refusal_says_what_kind_it_is_so_it_can_be_rendered_as_itself():
    """Every `allow=false` used to be rendered "cheese project budget: …" with a
    429. A card bound to a model the catalogue cannot serve is refused too now
    (I27), and reporting that as a spent quota points the user at the one thing
    that is fine."""
    import io
    from unittest import mock

    captured = json.dumps(
        {
            "data": {
                "allow": False,
                "reason": "这条活绑的模型 'gpt-9' 在当前项目里用不了，请改绑",
                "reason_kind": "binding",
                "supply": {},
            }
        }
    ).encode()

    class _Resp:
        def __enter__(self):
            return io.BytesIO(captured)

        def __exit__(self, *a):
            return False

    with mock.patch.object(core.urllib.request, "urlopen", return_value=_Resp()):
        verdict = core._post_admission("http://backend/llm/admission", "tok", 3.0)

    assert verdict.allow is False
    assert verdict.reason_kind == core.BINDING


def test_a_backend_that_names_no_kind_is_read_as_a_budget_refusal():
    """The kind a proxy older than this field always assumed."""
    assert core.Verdict(False, "budget spent").reason_kind == core.BUDGET
