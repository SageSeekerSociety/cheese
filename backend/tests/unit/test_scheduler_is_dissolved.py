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
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

#: 不扫的目录：版本库内部、装出来的依赖、以及只在一台机器上活着的草稿。
_SKIP_DIRS = {
    ".git",
    ".claude",
    "node_modules",
    "tmp",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "htmlcov",
}

_THIS_FILE = Path(__file__).resolve()


def _repo_text_files() -> list[Path]:
    found: list[Path] = []
    stack = [REPO]
    while stack:
        current = stack.pop()
        for entry in current.iterdir():
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in _SKIP_DIRS:
                    stack.append(entry)
                continue
            if entry.resolve() == _THIS_FILE:
                continue
            try:
                entry.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            found.append(entry)
    return found


def _hits(pattern: str) -> list[str]:
    rx = re.compile(pattern)
    out: list[str] = []
    for path in _repo_text_files():
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if rx.search(line):
                out.append(f"{path.relative_to(REPO)}:{number}: {line.strip()}")
    return out


def test_nothing_names_the_scheduler_package() -> None:
    """解散就是删代码：留一个 import 就等于留一个第二住处。"""
    assert _hits(r"app[./]domain[./]scheduler") == []


def test_the_scheduler_interval_knob_is_gone() -> None:
    """它开的那件事（定期巡检）退役了，旋钮跟着走——一个没有消费者的设置项，
    下一个人读到只会以为巡检还在，只是关着。"""
    assert _hits(r"scheduler_interval_seconds|SCHEDULER_INTERVAL_SECONDS") == []


@pytest.mark.parametrize(
    "path",
    ["/admin/scheduler/tick", "/admin/scheduler/poll-open-prs"],
)
def test_the_scheduler_routes_are_not_mounted(path: str) -> None:
    """三个手动扳机跟着包一起退场。周期扫底仍然是那条死锁的出口，
    `gate_sweep_interval_s` 就是它的上界。"""
    from app.main import app

    mounted = {getattr(route, "path", None) for route in app.routes}
    assert path not in mounted
