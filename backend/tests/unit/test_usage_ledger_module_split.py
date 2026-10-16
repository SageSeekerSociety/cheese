"""`ledger.py` 拆开之后，它那一面必须和拆之前一样。

拆的是文件，不是行为。这次拆的是账单（credits）那一块，见
`docs/manual/dev/billing.md`。四件事要守住：

**旧路径照旧回答。** 全仓（`app/` 和 `tests/`）一直按
`from app.domain.usage.ledger import ...` 取下面 `IMPORTED` 里的那几个名字；
规矩是能搬走的搬走、`ledger.py` 负责再导出，调用点一行都不改。

**再导出不是再定义。** 名字在新旧两边必须是同一个对象 —— 两份拷贝就是同一份
扣费顺序有两个版本，改一边不报错，只是行为慢慢分叉。

**私有的助手跟着读它的代码走。** `_balance`、`_live`、`_order` 这些拆分前在
`ledger.py` 顶层的名字，现在住在真正调用它们的那个模块里，旧路径不再回答它们。
仓库里没有任何调用点从旧路径取这些名字（`ast` 扫过全仓，见下），所以这是有意的
差别，钉在这里是为了让它可读、可判，而不是等谁以为补丁还打得上。`_TZ` 和
`_window_use` 例外：`usage.report` 取 `_TZ`，`ledger.py` 的写入半自己在用
`_window_use`，所以这两个仍然从旧路径回答。

**读那一半仍然是方法。** `Ledger` 继承只读那半，`self._session` 还是由
`Ledger.__init__` 放进去的；有人「顺手」把这些改成收 session 的模块函数，调用点
就全线 AttributeError。
"""

import ast
import pathlib
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.usage import balance, ledger, ledger_queries, payers, rates, windows
from app.domain.usage.ledger import Ledger, LedgerQueries

#: 全仓按 `from app.domain.usage.ledger import ...` 取的名字（`ast` 扫出来的实际
#: 集合，不是抄一遍 import 块）。
IMPORTED = (
    "Ledger",
    "Payer",
    "RateRow",
    "Rates",
    "Refusal",
    "Terms",
    "WindowUse",
    "_TZ",
    "month_end",
    "month_of",
    "payer_for_person",
    "payer_for_project",
    "team_terms",
    "terms_of",
)

#: 拆分前住在 `ledger.py` 顶层、现在跟着调用它的代码走、旧路径不再回答的私有
#: 助手。`_applies` 和 `_spendable` 不在这个表里：写入半自己要用它们，所以跟
#: 其它再导出一样留在旧路径上。
MOVED_PRIVATE = (
    "_FAR",
    "_balance",
    "_day",
    "_hours",
    "_issued_this_month",
    "_live",
    "_order",
    "_rank",
    "_window_refusal",
)

#: 「读那一半」：拆分前是 `Ledger` 的方法，现在定义在 `LedgerQueries` 上。
#: 判断标准只有一条 —— 只 SELECT，不改行。
READ_METHODS = (
    "_candidates",
    "_window_rows",
    "admit",
    "balance",
    "balances",
    "earmarks",
    "live_packs",
    "team_packs",
    "window_uses",
)

#: `ledger.py` 自己留下的：写、扣、发。名字到这里为止，不许再往下长。
WRITE_METHODS = (
    "_add",
    "_charge_windowed",
    "_deduct",
    "_drain",
    "_fill_windows",
    "_plan_pack",
    "charge",
    "charge_priced",
    "charge_spent",
    "grant",
    "grant_earmark",
    "record",
    "record_project_call",
)

#: 名字 → 现在定义它的模块。
HOME = {
    "_TZ": windows,
    "_window_use": windows,
    "month_end": windows,
    "month_of": windows,
    "week_of": windows,
    "Window": windows,
    "WindowUse": windows,
    "RateRow": rates,
    "Rates": rates,
    "Terms": payers,
    "terms_of": payers,
    "team_terms": payers,
    "Payer": payers,
    "payer_for_person": payers,
    "payer_for_project": payers,
    "payers_for_projects": payers,
    "Refusal": balance,
    "Balance": balance,
    # 写入半自己要用这两个，所以它们跟其它再导出一样留在旧路径上。
    "_applies": balance,
    "_spendable": balance,
    "LedgerQueries": ledger_queries,
    "Ledger": ledger,
}


@pytest.mark.parametrize("name", IMPORTED)
def test_the_old_path_still_answers_every_name_a_caller_takes(name: str) -> None:
    assert hasattr(ledger, name), (
        f"`app.domain.usage.ledger.{name}` 不见了 —— 全仓按这个路径取它，"
        "搬去新模块之后必须在这里再导出一次。"
    )


@pytest.mark.parametrize(
    ("name", "module"), sorted(HOME.items(), key=lambda pair: pair[0])
)
def test_the_reexport_is_the_same_object_not_a_second_copy(name, module) -> None:
    assert getattr(ledger, name) is getattr(module, name), (
        f"`{name}` 在 `ledger.py` 和 `{module.__name__}` 里是两个对象 —— "
        "这是把名字又定义了一遍，不是再导出。"
    )


@pytest.mark.parametrize("name", MOVED_PRIVATE)
def test_the_private_helpers_are_not_answered_here_any_more(name: str) -> None:
    """有意的差别：这些跟着读它们的代码搬走了，旧路径不再回答。

    想让它重新可补丁，得把调用方也搬回 `ledger.py`；全仓没有这样的调用点，
    所以没这么做。`_TZ` / `_window_use` 不在这个表里，它们仍然从这里回答。
    """
    assert not hasattr(ledger, name), (
        f"`app.domain.usage.ledger.{name}` 又出现了 —— 它是私有助手，"
        "搬去新模块之后不该在这里再留一份。"
    )
    assert name in vars(balance), (
        f"`{name}` 也没在 `balance.py` 里 —— 它不该就这样消失。"
    )


def test_no_caller_takes_a_moved_private_helper_from_the_old_path() -> None:
    """上一条的前提：全仓真的没人从旧路径取这些私有名字。

    这条扫源码而不是问运行时 —— 有了这个前提，上一条才是在钉「有意留下的差别」，
    而不是在钉一个会炸掉调用点的 bug。
    """
    root = pathlib.Path(ledger_queries.__file__).resolve().parents[3]
    taken: list[tuple[str, str]] = []
    marker = b"app.domain.usage.ledger"
    for path in list((root / "app").rglob("*.py")) + list(
        (root / "tests").rglob("*.py")
    ):
        # 先按字节粗筛：全仓两千多个文件挨个 parse 要五秒，撞上纯层的耗时上限。
        source = path.read_bytes()
        if marker not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "app.domain.usage.ledger":
                continue
            taken += [
                (str(path.relative_to(root)), alias.name)
                for alias in node.names
                if alias.name in MOVED_PRIVATE
            ]
    assert not taken, f"这些调用点仍从旧路径取搬走的私有助手：{taken}"


def test_none_of_the_read_half_is_defined_here() -> None:
    """读那一半不许在 `ledger.py` 里再长出一份。

    `is` 挡不住「又写了一份」—— 方法名是字符串，抄一遍两份都在、互不相等。
    所以这里读源码：模块级定义、以及 `Ledger` 这个类里定义的方法名，都不许和
    搬走的名字撞上。
    """
    tree = ast.parse(pathlib.Path(ledger.__file__).read_text())
    defined: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
        elif isinstance(node, ast.ClassDef):
            defined.add(node.name)
            if node.name == "Ledger":
                defined.update(
                    member.name
                    for member in node.body
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
        elif isinstance(node, ast.Assign):
            defined.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
    clash = defined & (set(MOVED_PRIVATE) | set(READ_METHODS))
    assert not clash, (
        f"这些名字在 `ledger.py` 里被又定义了一份，而不是继承/再导出：{sorted(clash)}"
    )


def test_the_seam_is_where_the_docstrings_say_it_is() -> None:
    """两半的方法名各归各的，多一个少一个都算边界动了。

    只读那一半在 `LedgerQueries`，写和发在 `Ledger`；`Ledger.__init__` 是唯一
    把 session 放进 `self._session` 的地方。

    `vars(Ledger)` 只列 `Ledger` 自己写下的名字（继承来的不在里面），所以这里
    不再按「和读半重名的就拿掉」过滤 —— 那样会把一个重写只读方法的 `Ledger` 从
    两份名单里同时抹掉，两边对得上、遮蔽却看不见。
    """
    read = {name for name in vars(LedgerQueries) if not name.startswith("__")}
    own = {name for name in vars(Ledger) if not name.startswith("__")}
    assert read == set(READ_METHODS), (
        "只读那一半的名单变了 —— 搬走或搬回一个方法都要同步 `READ_METHODS`："
        f"多出 {sorted(read - set(READ_METHODS))}，"
        f"少了 {sorted(set(READ_METHODS) - read)}"
    )
    assert own == set(WRITE_METHODS), (
        "写入那一半的名单变了："
        f"多出 {sorted(own - set(WRITE_METHODS))}，"
        f"少了 {sorted(set(WRITE_METHODS) - own)}"
    )
    assert read.isdisjoint(own)


@pytest.mark.parametrize("name", READ_METHODS)
def test_the_read_half_is_still_a_method_of_the_ledger(name: str) -> None:
    assert name in vars(LedgerQueries), (
        f"`{name}` 不再定义在 `LedgerQueries` 上 —— 它属于读那一半。"
    )
    assert issubclass(Ledger, LedgerQueries)
    assert callable(getattr(Ledger(cast(AsyncSession, None)), name)), (
        f"`Ledger.{name}` 取不到：调用点一直是按实例调它的。"
    )


def test_a_name_is_resolved_in_the_module_that_reads_it() -> None:
    """补丁要打在**读那个名字的那份模块**上，这是这次拆分实际留下的差别。

    写入半在 `ledger.py` 里解析自己的全局名（`_spendable` 就在这里），读那一半
    在 `ledger_queries` 里解析；所以打在旧路径上的补丁只影响写入半，影响不到
    读取半。拆之前两者是同一份模块，打哪儿都一样。
    """
    assert Ledger.charge.__globals__ is vars(ledger)
    assert LedgerQueries.admit.__globals__ is vars(ledger_queries)
    assert LedgerQueries.balance.__globals__ is vars(ledger_queries)
    # 写入半真的用 `_spendable`，所以它跟着再导出了；读那一半用的 `_balance`
    # 没人从旧路径读，就没再导出。
    assert "_spendable" in vars(ledger)
    assert "_balance" not in vars(ledger)
