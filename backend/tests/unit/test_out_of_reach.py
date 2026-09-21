"""What the agent reads when its hands cannot be reached.

One sentence, chosen by the platform, carried in two copies — the backend's and
the one shipped onto the machine — because the shipped half runs beside the
room's own files with nothing of ours importable.

结论 23。不变量 I25。
"""

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


def test_what_the_agent_reads_when_its_hands_are_out_of_reach_has_no_status_code(
    monkeypatch, tmp_path
):
    """够不着是一条能力话，不是一次 HTTP 报错（结论 23、不变量 I25）。

    断言的是 agent 真的读到的那个字符串：一个裸状态码会让它回头去自己的工具调用里
    找 bug，而它需要知道的是「文件和命令这一轮没有，对话和记忆还有」。后端早就写好
    的中文 body 和 ``X-Device-Id`` 头在这条路上本来就全丢了，剩下的只有那个数字。
    """
    token = tmp_path / "execution.token"
    token.write_text("t")

    class Response:
        status = 503

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

    with pytest.raises(RuntimeError) as raised:
        client.call("context_fs")

    said = str(raised.value)
    assert not re.search(r"\b[1-5][0-9][0-9]\b", said)
    assert "HTTP" not in said
    assert said == platform_failures.MACHINE_OUT_OF_REACH
