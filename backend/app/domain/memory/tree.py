"""会话里那棵树和数据库里的那一份，怎么合成一份（纯函数，两端共用）。

会话里的记忆是真文件：agent 用自己手里的 Write/Edit 改它们，改完就走的。平台这
边同一份记忆在 `memory_files` 里，别的会话也在改。回合结束时两边对一次账，这个
模块就是那次对账的规矩——**跑在会话机上（runner）和跑在后端的是同一段代码**，
所以这里没有数据库、没有 IO，只有「给定三方，谁赢」。

三方是：

- `scopes`：平台这一份（作用域前缀 → 文件名 → 正文），就是数据库现在的样子；
- `disk`：会话目录里现在的样子；
- `baseline`：上一次对账后写下去的那一版（路径 → 指纹）。**没有它就没有三方合
  并**：两个都变了的时候，「谁改的」只能靠「谁和上次不一样」来答。

判据是**指纹**，不是时间戳也不是版本号：两端是不同的钟，而版本号是数据库那一侧
的计数，会话里把一个文件改回原样，指纹能认出来，版本号认不出来。

规矩只有一条，其余的从它推出来：**平台这一份赢了冲突**。会话改过的文件原样留
着（那正是 agent 刚写下的记忆，回写就是为它），平台也改过的那个文件拿平台的版
本盖过去，被盖掉的那一版原样带回去——`refused` 是给房间里那句话用的，agent 读
到「你刚才那版被别人抢先了」，重读再写，比平台悄悄替它合并两段散文安全。

`baseline` 跟着结果一起回去，由调用方存起来（runner 存在自己的 state 里）。
"""

from dataclasses import dataclass, field

from app.domain.memory.files import digest

#: `refused` 里那一条的正文为空串时，被拒的不是一次修改而是一次**删除**：会话把
#: 这个文件删了，而平台这一份在那之后也变了。空串不是「没有正文」的正文，两者在
#: 这里分得开。
REMOVED = ""


@dataclass(frozen=True)
class TreeSync:
    """一次对账的结果。"""

    #: 对账之后这棵树的样子（路径 → 正文）。调用方负责让它成为磁盘/数据库的样子。
    files: dict[str, str]
    #: 会话这一版被平台盖掉的（路径 → 会话的正文，删除是空串）。
    refused: dict[str, str] = field(default_factory=dict)
    #: 新的 baseline（路径 → 指纹），存起来给下一次用。
    baseline: dict[str, str] = field(default_factory=dict)


def prefixes_of(
    scopes: dict[str, dict[str, str]], baseline: dict[str, str]
) -> set[str]:
    """这一次对账管哪些作用域。

    平台点名的那些，加上 baseline 里出现过的那些——上一次铺下去、这一次平台没提
    的（有人把这棵树关了），也要按同一套规矩收回来，否则会话里留下一个平台不再
    认的副本，agent 照着它做事。其余前缀的文件一个都不碰：那不是这棵树的东西。
    """
    managed = set(scopes)
    for path in baseline:
        try:
            prefix, _ = _split(path)
        except ValueError:
            continue
        managed.add(prefix)
    return managed


def _split(path: str) -> tuple[str, str]:
    parts = path.split("/")
    if parts and parts[0] == "team":
        prefix, rest = "team", parts[1:]
    elif len(parts) >= 2 and parts[0] == "private":
        prefix, rest = f"private/{parts[1]}", parts[2:]
    else:
        raise ValueError(path)
    if len(rest) != 1:
        raise ValueError(path)
    return prefix, rest[0]


def sync_tree(
    *,
    scopes: dict[str, dict[str, str]],
    disk: dict[str, str],
    baseline: dict[str, str],
) -> TreeSync:
    """三方合成一份。

    `disk` 只放**受管作用域**里的文件；传进来的其余路径会被忽略（不是这棵树的东
    西，不碰）。返回的 `files` 同样只含受管作用域。
    """
    managed = prefixes_of(scopes, baseline)
    requested: dict[str, str] = {
        f"{prefix}/{name}": content
        for prefix, files in scopes.items()
        for name, content in files.items()
    }
    settled: dict[str, str] = {}
    refused: dict[str, str] = {}
    baseline_after: dict[str, str] = {}
    for path in set(requested) | set(disk) | set(baseline):
        try:
            prefix, _ = _split(path)
        except ValueError:
            continue
        if prefix not in managed:
            continue
        content = _resolve(
            requested=requested.get(path),
            on_disk=disk.get(path),
            last=baseline.get(path),
            refused=refused,
            path=path,
        )
        # 两边都已经没有这一条了（只在 baseline 里），它就不落进对账后的树里：
        # baseline 是「上次写过什么」，不是「这里应该有什么」。
        if content is None and path not in requested and path not in disk:
            continue
        if content is not None:
            settled[path] = content
            baseline_after[path] = digest(content)
    return TreeSync(files=settled, refused=refused, baseline=baseline_after)


def _resolve(
    *,
    requested: str | None,
    on_disk: str | None,
    last: str | None,
    refused: dict[str, str],
    path: str,
) -> str | None:
    """一条记忆：平台这一版、会话这一版、上次那一版，合成哪一版。"""
    here = digest(on_disk) if on_disk is not None else None
    theirs = digest(requested) if requested is not None else None
    if here == theirs:
        # 两边一样：要么都已经没有这条，要么内容相同。没有可写的。
        return on_disk
    if here == last:
        # 会话没动过，平台动过（或者平台刚写了新的）：平台这一版。
        return requested
    if theirs == last:
        # 平台没动过，会话动了（改了，或者删了）：会话这一版原样留着。
        return on_disk
    # 两边都动过：平台这一版赢，会话那一版带回去。
    if on_disk is None:
        refused[path] = REMOVED
    elif theirs != here:
        refused[path] = on_disk
    return requested
