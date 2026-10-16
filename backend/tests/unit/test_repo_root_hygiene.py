"""仓库根目录卫生：跑出来的东西不进版本库。

三份一次性产物都进过 main：`skill-plaza-merged.zip`（解冲突合并包，2.5MB，内容随
构建变，快照已经在产物清单里）、`logs/cutover-bisect.out`（一次性 bisect 日志）、
`task-source-claim-review-prototype.html`（一次性评审原型页）。三份都没有代码引用，
删掉它们不需要改任何别的文件；难的是别再有下一份——根 `.gitignore` 当时只挡了
`logs/harness-contracts/` 一个子目录。这条守卫把根目录的 `*.zip` 和整棵 `logs/` 变成
版本库里的禁忌。

**口径只到今天点得出的这两类**：根目录的 `*.zip`（不分大小写）和整棵 `logs/`。第三
份是原型 HTML，但它不在口径里，也别顺手加进来——根目录已经有两份被追踪的原型页
（`sidebar-owner-light-prototype.html`、`sidebar-task-collapse-prototype.html`），把
HTML 当成一类产物来挡会当场红在它们身上，「哪些 HTML 算产物」是另一个决定。

**为什么两件事分开断言**：「现在没有这类文件」和「`.gitignore` 拦得住这类文件」是
两条会各自失效的规则。只留前一条，删掉 `.gitignore` 那两行照样是绿的，下一次有人
`git add` 一份 2.5MB 的 zip 时也没有任何一条测试变红；只留后一条，则一份早就进了
版本库的 zip 谁都看不见。
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

_THIS_FILE = Path(__file__).resolve().relative_to(REPO).as_posix()

#: 根目录下这些后缀是写出来的、不是写进去的：构建包、导出包。
_THROWAWAY_SUFFIXES = (".zip",)

#: 整棵树都不属于版本库的目录。跑出来的东西都写在这里（harness 契约、CI 反馈采集、
#: device-route 迁移、云机预热），所以是按树挡，不是按后缀挡。
_THROWAWAY_DIRS = ("logs/",)

_REPAIR = (
    "修法：这份文件是跑出来的产物，别把它提交进来——它的快照属于产物清单或 CI "
    "artifact，重建它的命令写在生成脚本里。根 .gitignore 里 `/*.zip` 与 `/logs/` "
    "两行是它的守卫，删了这两行这条测试也会红。"
    "复查：cd backend && uv run pytest tests/unit/test_repo_root_hygiene.py -q"
)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=False
    )


def _tracked() -> list[str]:
    """向 git 要被跟踪的路径，不去走工作树。

    工作树上另有一份只属于这台机器的东西（另开的分支、跑出来的日志），拿它判断
    「版本库干不干净」会红在一个与版本库无关的理由上。
    """
    listed = _git("ls-files", "-z")
    # 一个跑不起来的 git 会把 stdout 留空，守卫于是「检查了零个文件」而照样绿——
    # 静默变绿的守卫比没有守卫更坏，所以这里问出退出码，而不是只看输出。
    assert listed.returncode == 0, (
        f"git ls-files 没能跑起来（退出码 {listed.returncode}）："
        f"{listed.stderr.strip()}——这一条什么都没检查到，不算通过。"
    )
    return [name for name in listed.stdout.split("\0") if name]


def _throwaway(name: str) -> bool:
    if name == _THIS_FILE:
        return False
    if name.startswith(_THROWAWAY_DIRS):
        return True
    # 不分大小写：`.gitignore` 的 `/*.zip` 是逐字匹配的，`ARTIFACT.ZIP` 从它下面漏
    # 过去，而这里判的是「它已经在版本库里了没」，一个大小写不该决定谁被抓到。
    return "/" not in name and name.lower().endswith(_THROWAWAY_SUFFIXES)


def _leftovers() -> list[str]:
    return sorted(name for name in _tracked() if _throwaway(name))


def _is_ignored(path: str) -> bool:
    """`--no-index` 让这是纯粹在问规则本身：一条规则对不对，和这份文件此刻是否
    已经被 `git add` 过无关。追踪着的文件不适用忽略规则，不带它问会把「文件在版本
    库里」读成「规则没写对」。"""
    result = _git("check-ignore", "--no-index", "-q", "--", path)
    assert result.returncode in (0, 1), (
        f"git check-ignore 没能判断 {path}（退出码 {result.returncode}）："
        f"{result.stderr.strip()}"
    )
    return result.returncode == 0


def test_no_throwaway_product_is_tracked() -> None:
    """根目录的 `*.zip` 和 `logs/` 下的任何文件都不该在版本库里。"""
    leftovers = _leftovers()
    assert leftovers == [], (
        "一次性产物又进了版本库：\n"
        + "\n".join(f"  {name}" for name in leftovers)
        + "\n"
        + _REPAIR
    )


def test_the_root_gitignore_still_bars_them() -> None:
    """`.gitignore` 那两条规则本身：命中的要命中，正常源码不能被连坐。"""
    assert _is_ignored("foo.zip"), f"根目录的 `*.zip` 不再被忽略。\n{_REPAIR}"
    assert _is_ignored("logs/bar.out"), f"根目录的 `logs/` 不再被忽略。\n{_REPAIR}"
    assert not _is_ignored("README.md"), (
        "`README.md` 被当成忽略对象了：规则写得太宽，会连正常源码一起挡住。\n" + _REPAIR
    )
    assert not _is_ignored("frontend/package.json"), (
        "`frontend/package.json` 被当成忽略对象了：规则没有锚在仓库根目录，"
        "把子目录里的同名文件也挡住了。\n" + _REPAIR
    )


def test_a_leftover_names_itself(monkeypatch) -> None:
    """有一份漏进来时要报出它的路径，而不是只报一个空列表——一份 2.5MB 的二进制
    不会在 diff 里自己说话。用 monkeypatch 喂一份假的追踪列表，让守卫真正走到会红
    的那个分支。"""
    monkeypatch.setattr(
        "tests.unit.test_repo_root_hygiene._tracked",
        lambda: ["README.md", "logs/cutover-bisect.out", "build-output.zip"],
    )
    with pytest.raises(AssertionError) as caught:
        test_no_throwaway_product_is_tracked()
    message = str(caught.value)
    # 这两个只有被守卫的那段代码能产生：`build-output.zip` 不在 _REPAIR 里，换一个
    # 只报「有几份」的实现这条就红。
    assert "logs/cutover-bisect.out" in message
    assert "build-output.zip" in message
    # 非产物不在名单里：把整份 ls-files 原样倒出来的实现在这一条上红。
    assert "README.md" not in message


def test_a_git_that_will_not_answer_is_not_a_pass(monkeypatch) -> None:
    """`git ls-files` 失败时必须是红的，不是「零个产物」的绿：静默变绿的守卫比没有
    守卫更坏，因为它站的位置正好是别人以为有人看着的地方。"""
    monkeypatch.setattr(
        "tests.unit.test_repo_root_hygiene._git",
        lambda *args: subprocess.CompletedProcess(args, 128, "", "fatal: nope"),
    )
    with pytest.raises(AssertionError, match="ls-files"):
        _tracked()
