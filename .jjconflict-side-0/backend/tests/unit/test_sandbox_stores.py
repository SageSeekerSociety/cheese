"""沙箱容器里，**只有 `/topics` 挂载是活得比容器久的**，别处写什么都随容器一起没。

话题工作树（连同它的 `.venv/`）在 `/topics` 下，是宿主 bind mount 进来的；容器
自己的 `/`（含 `$HOME`）是 overlay 写入层，重建即清空。麻烦在于 `.venv` **不是
自足的**：沙箱镜像的系统 python 是 3.11，本项目要 >=3.13，所以 uv 在运行时下载一
个托管解释器，而 `.venv/bin/python` 是一条指向它的**绝对**软链（`pyvenv.cfg` 的
`home =` 也是）。uv 默认把它放 `~/.local/share/uv` —— 写入层。于是每次容器重建，
活下来的那个 venv 就指向了不存在的东西：

    $ .venv/bin/python -V
    No such file or directory
    $ .venv/bin/pyright --version
    cannot execute: required file not found

2026-08-13 在沙箱里实测过：`uv run` 自己会修（整个重建 venv），所以这是**代价**
不是**故障**——但代价是每次重建 15 秒 + 重新下载 33MiB 解释器，而解释器落到
`/topics` 下之后同样场景是 1 秒、不下载。

所以下面钉的是一条性质：**每一个共享 store 都必须落在 `/topics` 挂载下面**。
写成遍历而不是逐个断言，是因为会出事的从来不是已经在表里的那几个，而是以后新加
的那一个——新加时忘了这条，症状是「有时候好有时候坏」，不会有任何东西报错。
"""

from pathlib import Path

from app.domain.workspace.service import (
    SANDBOX_TOPICS_ROOT,
    sandbox_store_env,
)


def _store_env(root: Path) -> dict[str, str]:
    """把 `sandbox_store_env` 吐出来的 `-e K=V` 序列还原成 dict。"""
    args = sandbox_store_env(root)
    return dict(a.split("=", 1) for a in args if a != "-e")


def test_every_shared_store_points_at_the_persistent_mount(tmp_path: Path):
    """没有一个 store 可以落在容器写入层上。

    `/topics` 是唯一活得比容器久的地方；任何指向别处的 store，都会在下一次容器
    重建时静悄悄地消失。
    """
    env = _store_env(tmp_path)

    assert env, "一个 store 都没有？"
    for var, value in env.items():
        assert value.startswith(f"{SANDBOX_TOPICS_ROOT}/"), (
            f"{var}={value} 不在 {SANDBOX_TOPICS_ROOT} 挂载下面，容器一重建就没了"
        )


def test_the_managed_interpreter_is_one_of_them(tmp_path: Path):
    """uv 下载的解释器必须被明确安排到共享 store 里。

    少了这条 env，uv 就退回 `~/.local/share/uv`——那是写入层，而 `.venv/bin/python`
    是一条指向它的绝对软链。这条 env 不在的时候**没有任何报错**，只有下一次容器
    重建之后 venv 变成断链。
    """
    env = _store_env(tmp_path)

    assert "UV_PYTHON_INSTALL_DIR" in env, (
        "沙箱没有被告知托管解释器装哪儿，uv 会用 $HOME 下的默认位置——"
        "那是容器写入层，重建即失"
    )


def test_store_dirs_are_created_and_writable_by_the_container_user(tmp_path: Path):
    """宿主侧的目录由后端建，填内容的却是容器里的非 root 用户（两边 uid 未必
    相同），所以建完要放开权限——否则 uv 装不进去，又会退回默认位置。"""
    env = _store_env(tmp_path)

    for value in env.values():
        host_dir = tmp_path / Path(value).name
        assert host_dir.is_dir(), f"{host_dir} 没被建出来"
        assert host_dir.stat().st_mode & 0o777 == 0o777


def test_store_dirs_cannot_be_mistaken_for_a_topic_worktree(tmp_path: Path):
    """store 和话题工作树是同一个父目录下的兄弟。点开头才不会撞上 `topic_<hex>`
    或者合并用的 `_merge`——撞上的后果是一个话题的工作树被当成 store。"""
    env = _store_env(tmp_path)

    for value in env.values():
        name = Path(value).name
        assert name.startswith("."), f"{name} 没有点开头，可能撞上工作树目录名"
