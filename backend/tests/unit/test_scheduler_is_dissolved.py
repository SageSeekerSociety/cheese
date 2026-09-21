"""架构守卫：没有 scheduler 部件了。

结论 16「总览 = 每个项目自带的根房间，没有 scheduler 部件：总览里的芝士就是调度者」。
`app/domain/scheduler/` 扛的五件事各回各家——巡检退役、PR/上游轮询归 review 领域、
孤儿轮次清扫和闸门扫底进 `app/core/background.py` 的轮次活性层。

**为什么是一条守卫而不是一条功能测试**：搬家之后，每一件事各自的行为都由它自己那
几条测试盯着；一个包被解散这件事本身没有行为可以断言。能看见「它又回来了」的只有
「全仓零命中」这一条——一个新写的 `from app.domain.scheduler import ...` 不会让任何
一条别的测试变红，只会让「谁在按钟推进工作」重新有两个住处。
"""

import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

REPO = Path(__file__).resolve().parents[3]

#: `docs/plans/` 和 `docs/topics/` 里是**写完就定格的记录**——一份带日期的方案、
#: 一次 rebase 的语义冲突怎么解的、某条结论是哪天合的。它们写下时是真的，读的人
#: 也是当记录读。守卫逼着去改它们，改出来的不是更新，是一份假的历史。所以这条
#: 守卫盯的是活代码、配置，和描述「现在是什么样」的文档。
_SKIP_PATHS = ("docs/plans/", "docs/topics/")

_THIS_FILE = Path(__file__).resolve().relative_to(REPO).as_posix()


def _repo_text() -> list[tuple[str, str]]:
    """向 git 要被跟踪的文件，不去走工作树。

    这条守卫判的是版本库里还有没有 scheduler，而工作树上另有一份只属于这台机器的
    东西。`backend/.env` 就是：每个人照 `.env.example` 抄一份出来，本 PR 之前的那
    份里有 `SCHEDULER_INTERVAL_SECONDS` 这一行，于是一个早就配好 `.env` 的检出会
    红，而红的理由跟版本库里的代码无关。CI 和新开的 worktree 都不生成这个文件，两
    边都看不见这种红。被 gitignore 掉的其余东西（评测结果、谁临时写下的笔记）是同
    一个口子。
    """
    listed = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    found: list[tuple[str, str]] = []
    for name in listed:
        if not name or name == _THIS_FILE or name.startswith(_SKIP_PATHS):
            continue
        path = REPO / name
        # 跟踪着的符号链接（`.claude/skills/` 下那一批）指到版本库外面去。
        if path.is_symlink():
            continue
        try:
            found.append((name, path.read_text()))
        except (UnicodeDecodeError, OSError):
            continue
    return found


def _hits(pattern: str) -> list[str]:
    rx = re.compile(pattern)
    out: list[str] = []
    for name, text in _repo_text():
        for number, line in enumerate(text.splitlines(), start=1):
            if rx.search(line):
                out.append(f"{name}:{number}: {line.strip()}")
    return out


def test_nothing_names_the_scheduler_package() -> None:
    """解散就是删代码：留一个 import 就等于留一个第二住处。"""
    assert _hits(r"app[./]domain[./]scheduler") == []


def test_the_scheduler_interval_knob_is_gone() -> None:
    """它开的那件事（定期巡检）退役了，旋钮跟着走——一个没有消费者的设置项，
    下一个人读到只会以为巡检还在，只是关着。"""
    assert _hits(r"scheduler_interval_seconds|SCHEDULER_INTERVAL_SECONDS") == []


@pytest.fixture(scope="module")
def probe() -> TestClient:
    """The app with no lifespan run: the router answers every probe below
    before a dependency or a handler does, so this needs no database. Same
    client shape as tests/contract/test_api_addressing_contract.py."""
    return TestClient(app)


@pytest.mark.parametrize(
    "path",
    [
        "/admin/scheduler/tick",
        "/admin/scheduler/poll-open-prs",
        "/admin/scheduler/sweep-abandoned-gates",
    ],
)
def test_the_scheduler_routes_are_not_mounted(probe: TestClient, path: str) -> None:
    """三个手动扳机跟着包一起退场——它们还是全仓唯一一组没有鉴权的 `/admin/*`。
    闸门那条死锁的出口仍然在，只是只剩周期扫底一条，上界是 `gate_sweep_interval_s`。

    问服务端，不问 schema，也不问 `app.routes`。`app.openapi()` 只收
    `route.include_in_schema` 为真的（`fastapi/openapi/utils.py`），而
    `app/api/routes/` 里这个标志关掉了 17 处——把它们挂回去时顺手关掉，schema
    读法就看不见。`app.routes` 也不行：FastAPI 把 include 进来的 router 存成一条
    自己 `path` 是 `None` 的记录，照那张表找什么都找不到。发一次请求两头都覆盖。

    404 是只有「没挂」才答得出的：挂着但拒绝调用者是 401/403，挂着但方法不对是
    405，挂着而没有数据库则直接抛出到客户端外面——三种都是红的。"""
    response = probe.post(path)
    assert response.status_code == 404, (
        f"POST {path} 答了 {response.status_code}，说明它又挂回来了"
    )
