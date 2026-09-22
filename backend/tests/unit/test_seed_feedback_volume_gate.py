"""落库闸门 —— `seed_feedback_volume.py` 里唯一一处「不让人手滑」的代码。

这个脚本的破坏力来自它的**正常用法**：默认那 800 条是**追加**的，带前缀的行只能按
前缀清。而它的默认目标是**开发库**（`app/core/config.py` 里 `database_url` 的默认值
就是 `localhost:5432/cheese`），所以「手滑一次」和「正常跑一次」在命令行上长得一模一样
—— 差别只在 shell 里导出的是哪一份 `DATABASE_URL`。

这个文件钉四件事：

* 库名不像造数库 → `--apply` 非零退出，而且**拒绝发生在碰数据库之前**。
* 拒绝的话里要有实际连接串（人得知道自己刚才打的是哪台），但**不能有密码**。
* dry run 不受影响 —— 不改库就永远能跑，不然没人敢先看一眼形状。
* 主机的名字不能当判据。理由见 `looks_like_a_throwaway_database` 的 docstring：这个
  仓库里开发库在局域网地址、生产库在回环地址，按主机名放行会是**反的**。

「拒绝发生在碰数据库之前」那一条不是靠读代码断言的：用例把一个**被调用就炸**的
session 工厂换上去。闸门要是先开了，用例会以那个 AssertionError 失败，而不是过。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import NoReturn

import pytest
from sqlalchemy.engine import make_url

from scripts import seed_feedback_volume as seed

DEV = "postgresql+asyncpg://postgres:postgres@localhost:5432/cheese"
#: 生产那台：`scripts/ops/README.md` 里 PG 就是 prod 本机 `127.0.0.1:5433` 上的
#: docker `cheesex-pg`。它**在回环地址上**，这正是「localhost 就是安全的」接反的地方。
PROD = "postgresql+asyncpg://cheese:s3cret-pw@127.0.0.1:5433/cheese"

SEEDABLE = (
    "postgresql+asyncpg://postgres:postgres@localhost:5432/cheese_test",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/cheese_test_sec1",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/cheese_e2e_7",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/fusion_test",
    "postgresql+asyncpg://postgres:postgres@10.0.0.5:5432/cheese_dev",
    "postgresql+asyncpg://postgres:postgres@10.0.0.5:5432/volseed_scratch",
)

REFUSED = (
    DEV,
    PROD,
    # 「最近/上一个」这类词里恰好含 test/latest，按子串判会误放 —— 判的是词的完整匹配。
    "postgresql+asyncpg://postgres:postgres@localhost:5432/latest_the_cheese",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/cheesex",
    "sqlite+aiosqlite:////work/backend/preview.db",
)


@pytest.mark.parametrize("url", SEEDABLE)
def test_a_disposable_name_passes(url: str) -> None:
    assert seed.looks_like_a_throwaway_database(seed.database_name(url))


@pytest.mark.parametrize("url", REFUSED)
def test_anything_else_is_refused_without_the_override(url: str) -> None:
    assert not seed.looks_like_a_throwaway_database(seed.database_name(url))
    with pytest.raises(SystemExit) as caught:
        seed.require_seedable_target(url, allow_any=False)
    assert caught.value.code not in (0, None)


def test_a_loopback_host_is_not_evidence_of_anything() -> None:
    """两个 URL 的主机都是「本机」，一个该过、一个该拦 —— 差别只在库名。

    这条用例存在的理由不是覆盖率，是**防止有人把主机名加回判据里**：那样写出来的闸门
    在开发机上拦得住（局域网 IP）、在生产上放得过（127.0.0.1），两个方向都错。
    """
    # 两台都是回环地址 —— 开发那台是配置默认值里的 localhost，生产那台是 prod 本机的
    # 127.0.0.1。主机名这一维在两台库上给出的是同一个答案，判不出任何东西。
    assert make_url(DEV).host == "localhost"
    assert make_url(PROD).host == "127.0.0.1"
    assert not seed.looks_like_a_throwaway_database(seed.database_name(PROD))
    assert seed.database_name(PROD) == seed.database_name(DEV) == "cheese"


def test_the_refusal_names_the_target_and_not_the_password() -> None:
    with pytest.raises(SystemExit) as caught:
        seed.require_seedable_target(PROD, allow_any=False)
    message = str(caught.value)
    assert "cheese" in message
    assert "127.0.0.1:5433" in message
    assert "s3cret-pw" not in message


def test_the_override_is_what_relaxes_it() -> None:
    seed.require_seedable_target(PROD, allow_any=True)  # 不抛就算过


def test_apply_never_opens_a_session_when_the_target_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """闸门和 session 的先后顺序。"""

    def boom() -> None:
        raise AssertionError("闸门没拦住：已经走到建 session 这一步了")

    monkeypatch.setattr(seed, "engine", SimpleNamespace(url=make_url(DEV)))
    monkeypatch.setattr(seed, "async_session_factory", boom)
    with pytest.raises(SystemExit):
        asyncio.run(seed.main(["--apply"]))


class _NoQueries:
    """一个只允许被 `async with` 的假 session：谁碰它一下就炸。

    dry run 那条路上 `main` 确实会 `async with async_session_factory()`（`--purge` 的
    预览要用它），但**不该有一条语句真的发出去** —— 建会话本身不连库，`execute` 才连。
    所以这里钉的不是「没建会话」，是「没读过也没写过」。
    """

    async def __aenter__(self) -> _NoQueries:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def __getattr__(self, name: str) -> NoReturn:
        raise AssertionError(f"dry run 碰了 session.{name}")


def test_a_dry_run_still_prints_the_shape_for_a_refused_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不改库就永远能看一眼 —— 闸门只挡 `--apply`。"""
    monkeypatch.setattr(seed, "engine", SimpleNamespace(url=make_url(DEV)))
    monkeypatch.setattr(seed, "async_session_factory", _NoQueries)
    code = asyncio.run(
        seed.main(["--feedback", "3", "--authors", "2", "--long-thread", "0"])
    )
    assert code == 0
