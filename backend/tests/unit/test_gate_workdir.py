"""质量闸门必须把话题工作树挂在**沙箱用过的那个绝对路径**上。

这不是洁癖，是闸门能不能跑起来的唯一条件。`uv`/`pip` 生成的 console script
（pyright、pytest、alembic）会把自己 venv 的**绝对路径**烤进 shebang。分身的沙箱
把工作树跑在 `/topics/topic_<hex8>`（`sandbox_topic_workdir` 是真实路径，不是
`/work` 重映射——硬链接不能跨 bind mount），所以那些脚本
的第一行指向 `/topics/topic_<hex8>/.venv/bin/python`。闸门要是把同一份工作树挂到
`/work`，这行路径在容器里就不存在，`.venv/bin/pyright` 直接 exec 失败。

坏就坏在它**不响**：闸门照样启动，ruff 照样 PASS（原生二进制，没有 shebang），
其余每一项落进 BLOCKED，于是 `CHECK_STRICT=1` 下每张卡都回「检查没能跑起来」，
附一条 check.sh 试图 `uv sync` 一个它在 `--network none` 里永远下载不到的
替身 venv 时的 DNS 报错。两条路径当初是一致的，是沙箱后来搬到真实路径才分的家，
而没有任何东西拦住这次分家。

下面两条就是那个拦阻：一条钉住两边算出来的路径必须逐字相同，一条钉住闸门真的
把它用进了 docker argv。
"""

import uuid
from pathlib import Path

from app.domain.workspace.service import (
    SANDBOX_TOPICS_ROOT,
    _worktree_path,
    gate_workdir_for,
    sandbox_topic_workdir,
)


def test_the_gate_lands_on_the_exact_path_the_sandbox_used():
    """闸门算出来的容器内路径，必须和沙箱的工作目录逐字相同。

    差一个字符，venv 里所有 console script 的 shebang 就都指不到东西。
    """
    project_id = uuid.uuid4()

    for _ in range(4):  # 目录名由话题 id 派生，多取几个 id 免得撞上巧合
        topic_id = uuid.uuid4()
        assert gate_workdir_for(_worktree_path(project_id, topic_id)) == (
            sandbox_topic_workdir(topic_id)
        )


def test_the_gate_path_lives_under_the_topics_mount():
    """顺带钉住它不会退回 `/work` —— 那正是坏掉的那半。"""
    got = gate_workdir_for(Path("/srv/ws/.worktrees/some-project/topic_abc"))

    assert got == f"{SANDBOX_TOPICS_ROOT}/topic_abc"
    assert not got.startswith("/work")


# 闸门真正递给 docker 的那串 argv 是不是用了这个路径，钉在
# `test_gate_command.py::test_worktree_is_mounted_where_the_agent_built_its_venv`
# ——那条测试本来就在，写的时候 `/work` 也确实是对的；它抓不到这次分家，是因为
# 它钉的是字面量 `/work` 而不是「和沙箱一致」这个性质。现在它改成跟
# `sandbox_topic_workdir` 对比了。这里不重复。
