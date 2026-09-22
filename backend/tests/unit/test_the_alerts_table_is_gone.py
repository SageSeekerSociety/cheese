"""守卫：`app/` 里没有一句 SQL、没有一个模型认 `alerts` 这张表。

表已经删了（迁移 `d3f0a91c7b45`），所以写回去的代码不是「读到旧数据」，是启动之
后第一次执行就炸。这道守卫要的是**在改的时候就红**，而不是等某条路由在线上抛
`UndefinedTable`。

**不按「`alerts` 这五个字出现过没有」判。** 对外的 URL 仍然是
`/projects/{id}/alerts`（`app/api/routes/alerts.py` 的模块说明：URL 是契约，改的
只是它底下读写哪张表），`/spaces/{id}/analytics/alerts` 是另一回事，还有一批散文
里的 "alerts" 说的是告警。按字面数，这道守卫从第一天起就是红的，也就没人会再看
它。判据只认两样东西：把 `alerts` 当表名用的 SQL，和把 `__tablename__` 指过去的
模型。
"""

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"

#: `FROM alerts`、`JOIN alerts`、`INTO alerts`、`UPDATE alerts`、`TABLE alerts`，
#: 带不带引号都算。
AS_A_TABLE = re.compile(
    r"\b(from|join|into|update|table)\s+[\"']?alerts[\"']?\b", re.IGNORECASE
)

#: ORM 那一侧的同一件事。
AS_A_MODEL = re.compile(r"__tablename__\s*=\s*[\"']alerts[\"']")


def test_no_code_in_app_reads_or_writes_the_alerts_table() -> None:
    named: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for pattern in (AS_A_TABLE, AS_A_MODEL):
            for match in pattern.finditer(source):
                line = source.count("\n", 0, match.start()) + 1
                named.append(f"{path.relative_to(APP.parent)}:{line}: {match.group(0)}")

    assert not named, (
        "`alerts` 这张表已经不在库里了（d3f0a91c7b45），这几处还在认它：\n"
        + "\n".join(named)
        + "\n收件箱只有 `notification` 一张表（结论 58）。"
    )
