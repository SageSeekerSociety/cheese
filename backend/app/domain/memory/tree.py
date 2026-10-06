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

**索引例外：按行合并。** `MEMORY.md` 不是散文，是一行一条的目录，而几乎每一条新
记忆都要在里面加一行——两间房各自记下一条，按上面那条规矩就总有一间被盖回去重写。
所以索引两边都动过时，拿上次铺下去的那一版作底，按行做一次三方合并
（`merge_lines`，和 git 给 changelog 配的 `merge=union` 同一个意思）：两边加的行
都留下，两边删的行都删掉。只有两边把同一处改成了不同的样子，才回到上面那条规
矩。合并要的是底稿的**正文**，指纹不够，所以索引的底稿另存一份（`bases`）。

`baseline` 跟着结果一起回去，由调用方存起来——**和那棵树放在一起，同生同死**
（见 `claude_code/runner.py` 的 `_recall_baseline`）。放别处就会出现「树没了、表
还在」：会话的家被重建过一次，磁盘是空的，而表里记着满满一树，于是每一次对账都把
空磁盘读成「会话把整棵树删了」，平台上的记忆跟着整批消失，而删除没有历史可恢复。

**超了单条上限的，这一版不收。** 会话写的那一版超了 `files.limit_breach` 的上限，
就当它没写：平台那一版留着（新建的就没有这一条），会话那一版和原因放进
`rejected`。这件事必须在这里判，不能只在数据库那一侧拒：这里一旦收下，基线就记成
了会话那一版，下一次对账会把平台的旧版静默地铺回磁盘，agent 写的东西不留痕迹。

**一次删掉半棵树，先当它没删。** 一条一条删记忆是 agent 想明白了；一个作用域同时
少掉一大半，更像是那棵树本身出了事（家被重建、磁盘没挂上）。这是上一条的兜底：
上一条堵的是根因，这一条保证根因万一再出现也删不掉东西（`BULK_DELETE_*`）。
"""

from dataclasses import dataclass, field
from difflib import SequenceMatcher

from app.domain.memory.files import INDEX_NAME, digest, limit_breach

#: `refused` 里那一条的正文为空串时，被拒的不是一次修改而是一次**删除**：会话把
#: 这个文件删了，而平台这一份在那之后也变了。空串不是「没有正文」的正文，两者在
#: 这里分得开。
REMOVED = ""

#: 一个作用域一次对账里被删掉多少条才算「这不像是人删的」：比例与条数都过了才拦
#: （见 `_too_many_to_delete`）。要比例，是因为小树里删一条不该触发；要条数，是
#: 因为一棵大树里删掉一半也可能只是几十个文件里挑了十来个，而几棵树加起来才像样
#: 的一次整理不该被拦。拦下来的代价是某个人真的清空了它自己那棵 private 树时要
#: 再删一遍（文件还在，删了就删了）；没拦住，是平台上的记忆没了、且没有历史可恢复。
BULK_DELETE_RATIO = 0.5
BULK_DELETE_MIN = 3


@dataclass(frozen=True)
class TreeSync:
    """一次对账的结果。"""

    #: 对账之后这棵树的样子（路径 → 正文）。调用方负责让它成为磁盘/数据库的样子。
    files: dict[str, str]
    #: 会话这一版被平台盖掉的（路径 → 会话的正文，删除是空串）。
    refused: dict[str, str] = field(default_factory=dict)
    #: 新的 baseline（路径 → 指纹），存起来给下一次用。
    baseline: dict[str, str] = field(default_factory=dict)
    #: 会话写的、超了单条上限没收的（路径 → 原因）。会话那一版还在磁盘上，调用方
    #: 在它被清掉之前把它留到旁边。
    rejected: dict[str, str] = field(default_factory=dict)
    #: 这一次对账里被保险拦下的删除（路径），按路径序。平台上它们一条都没少，会话
    #: 里下一轮会被重新铺回去。调用方拿它记一条日志：拦下来是「这一次没照做」，不是
    #: 「这件事没发生过」。
    held: tuple[str, ...] = ()
    #: 新的索引底稿（路径 → 正文），和 `baseline` 一起存起来给下一次合并用。
    bases: dict[str, str] = field(default_factory=dict)


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
    bases: dict[str, str] | None = None,
) -> TreeSync:
    """三方合成一份。

    `disk` 只放**受管作用域**里的文件；传进来的其余路径会被忽略（不是这棵树的东
    西，不碰）。返回的 `files` 同样只含受管作用域。`bases` 是上一次留下的索引底
    稿（`TreeSync.bases`）；没有它，索引两边都动过时照普通文件处理。
    """
    managed = prefixes_of(scopes, baseline)
    requested: dict[str, str] = {
        f"{prefix}/{name}": content
        for prefix, files in scopes.items()
        for name, content in files.items()
    }
    settled: dict[str, str] = {}
    refused: dict[str, str] = {}
    rejected: dict[str, str] = {}
    baseline_after: dict[str, str] = {}
    #: 平台有、会话里没有、而平台自上次以来没动过的那几条：会话这一版不见了，接
    #: 下来就是把平台那一版删掉。这一支分不出「agent 删了」和「那棵树没了」，所以
    #: 它由 `_too_many_to_delete` 兜着。
    dropped: list[str] = []
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
            base=(bases or {}).get(path) if _split(path)[1] == INDEX_NAME else None,
            refused=refused,
            path=path,
        )
        # 两边都已经没有这一条了（只在 baseline 里），它就不落进对账后的树里：
        # baseline 是「上次写过什么」，不是「这里应该有什么」。
        if content is None and path not in requested and path not in disk:
            continue
        # 收下的是会话写的（它那一版，或者索引合并出来的那一版）：量它新写的行。
        previous = requested.get(path)
        if content is not None and content != previous:
            breach = limit_breach(_split(path)[1], content, previous)
            if breach:
                rejected[path] = breach
                content = previous
                if content is None:
                    continue
        if content is None and path in requested:
            dropped.append(path)
        if content is not None:
            settled[path] = content
            baseline_after[path] = digest(content)
    held = _too_many_to_delete(requested, dropped)
    for path in held:
        # 拦下来的按「平台那一版原样留着」处理：会话里少掉的那几个文件下一轮会被
        # 重新铺回去，而平台上一条都不少。要是它同时还是一次冲突（两边都动过），
        # 那条 refusal 也一并撤掉——文件马上要回到磁盘上，那句话就没有对象了。
        settled[path] = requested[path]
        baseline_after[path] = digest(requested[path])
        refused.pop(path, None)
    return TreeSync(
        files=settled,
        refused=refused,
        baseline=baseline_after,
        rejected=rejected,
        held=tuple(sorted(held)),
        bases={
            path: content
            for path, content in settled.items()
            if _split(path)[1] == INDEX_NAME
        },
    )


def _too_many_to_delete(requested: dict[str, str], dropped: list[str]) -> list[str]:
    """这次要删的是不是多到不像人干的（`BULK_DELETE_RATIO` / `BULK_DELETE_MIN`）。

    按作用域分开数：team 和某个人的 private 是两棵树，一个人把它那棵清空是一回事，
    拿它去替项目那一棵作数就成了「谁都别删」。命中就返回那个作用域里全部要被删掉
    的路径——只拦一半更糟，剩下的照样删，而人看到的是一棵被啃过的树。
    """
    by_prefix: dict[str, list[str]] = {}
    for path in dropped:
        try:
            prefix, _ = _split(path)
        except ValueError:
            continue
        by_prefix.setdefault(prefix, []).append(path)
    held: list[str] = []
    for prefix, paths in by_prefix.items():
        total = sum(1 for path in requested if path.rsplit("/", 1)[0] == prefix)
        if len(paths) > BULK_DELETE_MIN and len(paths) > total * BULK_DELETE_RATIO:
            held.extend(paths)
    return held


def _resolve(
    *,
    requested: str | None,
    on_disk: str | None,
    last: str | None,
    base: str | None,
    refused: dict[str, str],
    path: str,
) -> str | None:
    """一条记忆：平台这一版、会话这一版、上次那一版，合成哪一版。

    `base` 是上次那一版的正文，只有索引有；它的指纹对不上 `last` 就不作数——拿
    一份不是上次铺下去的底稿去合并，会把别人删掉的行当成会话新加的。
    """
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
    # 两边都动过。索引先按行合并，合得上就是合并的那一版。
    if (
        base is not None
        and on_disk is not None
        and requested is not None
        and digest(base) == last
        and (merged := merge_lines(base, on_disk, requested)) is not None
    ):
        return merged
    # 合不上（或者不是索引）：平台这一版赢，会话那一版带回去。
    if on_disk is None:
        refused[path] = REMOVED
    elif theirs != here:
        refused[path] = on_disk
    return requested


def merge_lines(base: str, ours: str, theirs: str) -> str | None:
    """按行的三方合并；两边在同一处改得不一样时是 None。

    两边各自和 `base` 比一次，得到各自改了哪几段（`base` 里的行区间 → 换成的
    行）。互不相碰的段各自照做：一边加的行留下，一边删的行删掉。碰在一起的那一处
    只有三种合得上——两边改得一模一样；两边都是在同一个位置加行（两间房各记了一
    条新记忆，最常见的那一种），这时先放 `ours` 的、再放 `theirs` 里 `ours` 没有
    的；其余（同一行一边改一边删、两边改成不一样的）都是真冲突。

    比的是去掉行尾的行，结尾的换行跟着两边：任何一边以换行结尾，结果也是。
    """
    old = base.splitlines()
    sides = [_hunks(old, ours.splitlines()), _hunks(old, theirs.splitlines())]
    hunks = sorted(
        (start, end, lines, side)
        for side, found in enumerate(sides)
        for start, end, lines in found
    )
    out: list[str] = []
    at = 0
    i = 0
    while i < len(hunks):
        cluster = [hunks[i]]
        i += 1
        while i < len(hunks) and any(_touch(hunks[i], h) for h in cluster):
            cluster.append(hunks[i])
            i += 1
        start = min(h[0] for h in cluster)
        end = max(h[1] for h in cluster)
        ours_part = [h[:3] for h in cluster if h[3] == 0]
        theirs_part = [h[:3] for h in cluster if h[3] == 1]
        if not ours_part or not theirs_part or ours_part == theirs_part:
            # 只有一边动了这一处（同一边的段互不相碰，所以只有一段），或者两边
            # 动得一模一样。
            lines = (ours_part or theirs_part)[0][2]
        elif all(h[0] == h[1] == start for h in cluster):
            added = [line for h in ours_part for line in h[2]]
            lines = added + [
                line for h in theirs_part for line in h[2] if line not in added
            ]
        else:
            return None
        out.extend(old[at:start])
        out.extend(lines)
        at = end
    out.extend(old[at:])
    if not out:
        return ""
    ending = "\n" if ours.endswith("\n") or theirs.endswith("\n") else ""
    return "\n".join(out) + ending


def _hunks(old: list[str], new: list[str]) -> list[tuple[int, int, list[str]]]:
    """`new` 相对 `old` 改了哪几段：(`old` 里的起, 止, 换成的行)。"""
    matcher = SequenceMatcher(None, old, new, autojunk=False)
    return [
        (i1, i2, new[j1:j2])
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]


def _touch(a: tuple, b: tuple) -> bool:
    """两段改动碰不碰在一起。

    两段都是替换/删除：区间相交才算。一段是插入（空区间）：插在另一段**里面**才
    算，插在它的头上或尾上不算——那一行在它前面或后面，两边的意思都不变。两段都
    是插入：插在同一个位置才算，先后由合并决定。
    """
    a_start, a_end = a[0], a[1]
    b_start, b_end = b[0], b[1]
    if a_start == a_end and b_start == b_end:
        return a_start == b_start
    if a_start == a_end:
        return b_start < a_start < b_end
    if b_start == b_end:
        return a_start < b_start < a_end
    return a_start < b_end and b_start < a_end
