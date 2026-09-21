"""What the agent reads when a call to its hands does not come back 200.

One sentence, declared once, in the file that both the backend imports and the
machine runs a shipped copy of. And it is one sentence for one situation: 够不着
is a claim about the machine, not a synonym for 非 200.

结论 23。不变量 I25。
"""

import logging
import re

import pytest

from app.domain.agent import executor_transport


def failing_client(monkeypatch, tmp_path, status):
    """一个每次调用都拿到 ``status`` 的执行器客户端。"""
    token = tmp_path / "execution.token"
    token.write_text("t")

    class Response:
        def __init__(self):
            self.status = status

        @staticmethod
        def read():
            return b"whatever the executor said"

    class Connection:
        sock = None

        def request(self, method, path, *, body, headers):
            return None

        @staticmethod
        def getresponse():
            return Response()

        @staticmethod
        def close():
            pass

    client = executor_transport.RemoteClient(
        {"kind": "device", "url": "http://executor.test", "token_file": str(token)}
    )
    monkeypatch.setattr(client, "connection", lambda: (Connection(), "/execution"))
    client.transport.headers = {}
    return client


def test_what_the_agent_reads_when_its_hands_are_out_of_reach_has_no_status_code(
    monkeypatch, tmp_path
):
    """够不着是一条能力话，不是一次 HTTP 报错（结论 23、不变量 I25）。

    断言的是 agent 真的读到的那个字符串：一个裸状态码会让它回头去自己的工具调用里
    找 bug，而它需要知道的是「文件和命令这一轮没有，对话和记忆还有」。后端早就写好
    的中文 body 和 ``X-Device-Id`` 头在这条路上本来就全丢了，剩下的只有那个数字。
    """
    client = failing_client(monkeypatch, tmp_path, 503)

    with pytest.raises(RuntimeError) as raised:
        client.call("context_fs")

    said = str(raised.value)
    assert not re.search(r"\b[1-5][0-9][0-9]\b", said)
    assert "HTTP" not in said
    assert said == executor_transport.MACHINE_OUT_OF_REACH


def test_a_handler_that_threw_does_not_get_reported_as_the_machine_being_gone(
    monkeypatch, tmp_path
):
    """500 说的是机器上某个工具处理函数炸了，不是这双手没了。

    两句话的后果不一样：读到「够不着」的 agent 会放弃这一轮全部文件与命令操作、并
    向人报告机器掉线，而机器好好的，下一次工具调用照样通。401（令牌过期）和执行器
    版本落后时的 4xx 走的是同一条路。
    """
    client = failing_client(monkeypatch, tmp_path, 500)

    with pytest.raises(RuntimeError) as raised:
        client.call("context_fs")

    said = str(raised.value)
    assert said != executor_transport.MACHINE_OUT_OF_REACH
    assert said == executor_transport.EXECUTOR_CALL_FAILED
    # 同样不给它一个裸数字去追。
    assert not re.search(r"\b[1-5][0-9][0-9]\b", said)


def test_the_status_and_the_body_survive_in_the_platform_log(
    monkeypatch, tmp_path, caplog
):
    """agent 读不到状态码，平台读得到。

    以前这两样一起丢：抛出去的那句话不带状态码，而 ``data`` 读完就没人再看，于是
    事后连「当时是哪个码」都查不出来。
    """
    client = failing_client(monkeypatch, tmp_path, 418)

    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError):
        client.call("context_fs")

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "418" in logged
    assert "whatever the executor said" in logged
    assert "context_fs" in logged


@pytest.mark.parametrize(
    "failure",
    [TimeoutError("timed out"), ConnectionResetError("closed without answering")],
    ids=["read timed out", "hung up without answering"],
)
def test_a_machine_that_never_answers_is_out_of_reach_too(
    monkeypatch, tmp_path, failure
):
    """够不着的那一档里最常见的一个：执行器一个字也没答（结论 23）。

    答了 502/503/504 的机器至少还答了。真正够不着的那台什么也不答，调用方等到的
    是这条连接的读超时或者一次被挂断的连接 —— 而调用方要认的是同一件事，所以它得
    是**判得出来的一种**，不是一句拿去比对的话。认不出来的后果不是少一行日志：那一
    轮余下的每一次文件与命令调用都会再各等一次同样的超时。
    """
    token = tmp_path / "execution.token"
    token.write_text("t")

    class Connection:
        sock = None

        def request(self, method, path, *, body, headers):
            return None

        @staticmethod
        def getresponse():
            raise failure

        @staticmethod
        def close():
            pass

    client = executor_transport.RemoteClient(
        {"kind": "device", "url": "http://executor.test", "token_file": str(token)}
    )
    monkeypatch.setattr(client, "connection", lambda: (Connection(), "/execution"))
    client.transport.headers = {}

    with pytest.raises(executor_transport.MachineOutOfReach) as raised:
        client.call("context_fs")

    # agent 读到的还是同一句话；多出来的只是调用方判得动的那个类型。
    assert str(raised.value) == executor_transport.MACHINE_OUT_OF_REACH


def test_a_handler_that_threw_is_still_not_that_type(monkeypatch, tmp_path):
    """判得出来的那一种只能装够不着。

    `MachineOutOfReach` 是 `RuntimeError` 的子类，所以「执行器答了 500」那条路要是
    也抛它，调用方一样会把这一轮剩下的文件与命令调用全部当掉 —— 而机器好好的。
    """
    client = failing_client(monkeypatch, tmp_path, 500)

    with pytest.raises(RuntimeError) as raised:
        client.call("context_fs")

    assert not isinstance(raised.value, executor_transport.MachineOutOfReach)
