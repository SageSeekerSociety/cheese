"""What the agent reads when a call to its hands does not come back 200.

One sentence, chosen by the platform, carried in two copies — the backend's and
the one shipped onto the machine — because the shipped half runs beside the
room's own files with nothing of ours importable. And it is one sentence for one
situation: 够不着 is a claim about the machine, not a synonym for 非 200.

结论 23。不变量 I25。
"""

import logging
import re

import pytest

from app.domain.agent import executor_transport, platform_failures


def test_the_shipped_transport_carries_the_sentence_the_platform_chose():
    """机器上那份传输层带的是同一句话，不是一个状态码。

    它在那台机器上运行，我们的东西一样都 import 不到，所以它带一份拷贝。拷贝漂了
    不会有任何东西失败：agent 照样读到一句话，只不过那句话不再告诉它这一轮还剩哪些
    通路，而是告诉它有个数字。
    """
    assert executor_transport.MACHINE_OUT_OF_REACH == (
        platform_failures.MACHINE_OUT_OF_REACH
    )


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
    assert said == platform_failures.MACHINE_OUT_OF_REACH


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
    assert said != platform_failures.MACHINE_OUT_OF_REACH
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
