"""记忆的形态：一条记忆 = 一个 markdown 文件（照搬 Claude Code 的 memory 机制）。

CC 2.1.283 的记忆是一组文件，不是一张表：一条记忆一个 `.md`，frontmatter 写
`name` / `description` / `type`，正文是这条事实本身；同目录下还有一份 `MEMORY.md`
索引，一行一条指针。芝士照搬这套形态，平台数据库只是真相的落脚处（多机、多会话
要读同一份），文件才是 agent 直接读写的东西。

这个模块是纯的：解析、渲染、判据、上限。没有数据库，没有 IO——所以「什么算一条
合法的记忆」「索引超了怎么办」这两个问题可以在单测里直接问，不用先起一个会话。

**索引不是文档。** `MEMORY.md` 只放指针（`- [标题](file.md) — 一句钩子`），正文
永远在它指的那个文件里。串味过一次就再也读不出来哪一行是索引、哪一行是内容。

**单条有上限，总量没有。** 一条记忆的正文和索引里新写的一行各有字数上限，超了
就拒绝（`limit_breach`）：写的人手上就有这一条，当场就改得短。索引总长的 200 行 /
25KB 是注入预算，不是写入闸：超了照样写，注入时截断，由整理决定留哪几条——删哪
一条要看整个作用域，写的人手上没有这份信息。
"""

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

#: 索引文件名。和 CC 一样，两个作用域各有一份同名的索引。
INDEX_NAME = "MEMORY.md"

#: 会话里这棵树的根，相对会话自己的 `$HOME`。会话与平台之间来回搬的路径都从这
#: 里往下数，所以「写哪儿」在两端只有这一个答案。
MEMORY_ROOT = ".cheese/memory"

#: 作用域前缀：project 没有主人，private 的主人在路径里。
PROJECT_PREFIX = "project"
PRIVATE_PREFIX = "private"

#: 注入预算：超过就截断，并明说截断了。
INDEX_MAX_LINES = 200
INDEX_MAX_BYTES = 25 * 1024

#: 索引里一行的上限（字符）。正文很少被读，照着做的就是这一行，所以它要短到能一眼
#: 读完，又要长到能说出一句完整的主张。
INDEX_LINE_MAX = 150

#: 一条记忆正文（frontmatter 之后）的上限（字符）。写不进这么长，多半是把排查经过
#: 当成了结论。
BODY_MAX = 1000

#: 一条记忆的路径上限，等于 `memory_files.path` 那一列的宽度（`String(200)`）。
#: 没有这一条时，一个超长的文件名不是在写入端被拒，而是在 flush 的时候炸成一个
#: 500——写它的 agent 拿不到「该改什么」。
PATH_MAX = 200

#: 一条记忆的文件名（不含扩展名）：kebab-case slug。
_NAME_RE = re.compile(r"\A[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
#: `- [标题](file.md) — 钩子`。分隔符是 em dash，也接受 `-` 与 `–`：这条格式由
#: agent 自己写，逗号宽一点不会让索引读不出来，但路径和标题必须都是可解析的。
_INDEX_LINE_RE = re.compile(
    r"\A\s*-\s*\[(?P<title>[^\]]*)\]\((?P<path>[^)]+)\)"
    r"\s*[—–-]?\s*(?P<hook>.*)\Z"
)


class MemoryType(StrEnum):
    """四种类型，照搬 CC。

    `user` 是「这个人是谁」，永远是 private；`feedback` 是「你该怎么做」——纠正
    和**被认可的做法**都算，只记纠正会让 agent 越来越保守；`project` 是代码和
    git 读不出的进行中工作与约束；`reference` 是外部系统的入口。
    """

    user = "user"
    feedback = "feedback"
    project = "project"
    reference = "reference"


class MemoryFileScope(StrEnum):
    """两级作用域。

    `project` 是这个项目所有人和所有芝士共看的一份；`private` 是「这个人 × 这个
    项目」——同一个人换一个项目读不到，同一个项目换一个人也读不到。
    """

    project = "project"
    private = "private"


class MemoryFileError(ValueError):
    """这个文件不是一条合法的记忆。写入端据此拒绝，且理由里带上哪里不对。"""


def valid_name(name: str) -> bool:
    """`name` 是不是一个 kebab-case slug。"""
    return bool(_NAME_RE.match(name))


def check_name(name: str) -> str:
    if not valid_name(name):
        # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
        raise MemoryFileError(
            "name 必须是 kebab-case（小写字母、数字、连字符，如 "
            "integration-tests-hit-a-real-db）"
        )
    return name


@dataclass(frozen=True)
class MemoryFile:
    """一条记忆。`description` 是召回器挑它时唯一读得到的东西。"""

    name: str
    description: str
    type: MemoryType
    body: str

    def text(self) -> str:
        """整个文件，含 frontmatter。"""
        return (
            "---\n"
            f"name: {self.name}\n"
            f"description: {self.description}\n"
            f"type: {self.type.value}\n"
            "---\n\n"
            f"{self.body.strip()}\n"
        )

    def index_line(self, path: str) -> str:
        """索引里指向它的一行。标题取 `name`，钩子取 `description`。"""
        return f"- [{self.name}]({path}) — {self.description}"


def parse_memory_file(text: str) -> MemoryFile:
    """把一个记忆文件的正文读成一条记忆。

    只认 `key: value` 这几行——YAML 的其余部分这里用不上，而一个半吊子的 YAML
    解析器会把「解析失败」变成「静默读成空」，那比拒绝更糟。
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
        raise MemoryFileError("记忆文件必须以 frontmatter（首尾各一行 `---`）开头")
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        if not separator:
            # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
            raise MemoryFileError(f"frontmatter 里的这一行不是 `key: value`：{line}")
        fields[key.strip().lower()] = value.strip()
    for required in ("name", "description", "type"):
        if not fields.get(required):
            # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
            raise MemoryFileError(f"frontmatter 缺少 {required}")
    try:
        kind = MemoryType(fields["type"])
    except ValueError as exc:
        # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
        raise MemoryFileError(
            "type 只能是 user / feedback / project / reference，"
            f"拿到的是 {fields['type']}"
        ) from exc
    body = text[match.end() :].strip()
    if not body:
        # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
        raise MemoryFileError("记忆文件没有正文——一条记忆一件事，正文才是那件事")
    return MemoryFile(
        name=check_name(fields["name"]),
        description=fields["description"],
        type=kind,
        body=body,
    )


@dataclass(frozen=True)
class IndexEntry:
    """索引里的一行。`path` 相对本作用域的目录。"""

    title: str
    path: str
    hook: str


def parse_index(text: str) -> list[IndexEntry]:
    """把 `MEMORY.md` 读成条目列表；读不成行的原样留在文件里，不进这个列表。"""
    entries = []
    for line in text.splitlines():
        match = _INDEX_LINE_RE.match(line)
        if match is None:
            continue
        entries.append(
            IndexEntry(
                title=match.group("title").strip(),
                path=match.group("path").strip(),
                hook=match.group("hook").strip(),
            )
        )
    return entries


def without_entries(text: str, paths: set[str]) -> str:
    """`MEMORY.md` 去掉指向 ``paths`` 的那几行，其余原样。"""
    kept = [
        line
        for line in text.splitlines()
        if (match := _INDEX_LINE_RE.match(line)) is None
        or match.group("path").strip() not in paths
    ]
    return "\n".join(kept) + ("\n" if text.endswith("\n") else "")


def fit_index(text: str) -> tuple[str, str | None]:
    """把一份索引压进注入预算；没超就一个字节都不动。

    返回 `(注入用的文本, 警告)`，警告是给**写入端**看的那一条（写入照样成功）。
    截断按行，不按字符：索引的半行什么也不是，而半行正是「读不到」和「读到一句
    被腰斩的话」的区别。
    """
    original_lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    over_bytes = len(text.encode()) > INDEX_MAX_BYTES
    over_lines = original_lines > INDEX_MAX_LINES
    if not over_bytes and not over_lines:
        return text, None
    kept: list[str] = []
    used = 0
    for line in text.splitlines():
        cost = len(line.encode()) + 1
        if len(kept) >= INDEX_MAX_LINES or used + cost > INDEX_MAX_BYTES:
            break
        kept.append(line)
        used += cost
    warning = (
        f"索引超出上限（{INDEX_MAX_LINES} 行 / {INDEX_MAX_BYTES // 1024}KB）："
        f"现在 {original_lines} 行 / {len(text.encode())} 字节，超出的部分**读不到**，"
        "请把长条目搬进它指的那个文件、或合并重复的一条，把索引压回上限以内。"
    )
    return "\n".join(kept) + ("\n" if kept else ""), warning


def limit_breach(name: str, content: str, previous: str | None) -> str | None:
    """这一版超了单条上限就说为什么；没超是 None。

    ``name`` 是作用域里的文件名，``previous`` 是它现在的那一版（新建时 None）。

    索引只看**这一版新写的行**：别人早先写下的一行长的，不该挡住这次加的另一行。
    正文看整条：改一条已经超长的记忆，就要把它改到上限以内。读不成记忆文件的正文
    按整份算，frontmatter 写错不是绕过上限的办法。
    """
    if name == INDEX_NAME:
        old = set((previous or "").splitlines())
        long_lines = [
            line
            for line in content.splitlines()
            if line not in old and len(line) > INDEX_LINE_MAX
        ]
        if not long_lines:
            return None
        shown = "\n".join(f"  {line}" for line in long_lines[:5])
        return (
            f"索引有 {len(long_lines)} 行超过 {INDEX_LINE_MAX} 字符：\n{shown}\n"
            "一行写一句能照着做的主张，细节放进它指的那个文件。"
        )
    try:
        body = parse_memory_file(content).body
    except MemoryFileError:
        body = content.strip()
    if len(body) <= BODY_MAX:
        return None
    return (
        f"正文 {len(body)} 字，上限 {BODY_MAX} 字。只写结论和它为什么成立，"
        "不写排查经过；说的是几件事就拆成几条。"
    )


def rejected_path(path: str) -> str:
    """没收的那一版留在哪儿：同一个目录，`<名字>.rejected.md`。

    名字里带点，过不了 `check_path`，所以对账从不把它当成一条记忆收回去。
    """
    return f"{path[: -len('.md')]}.rejected.md"


def conflict_path(path: str) -> str:
    """被平台盖回去的那一版留在哪儿：同一个目录，`<名字>.conflict.md`。

    也是带点的名字，同样过不了 `check_path`。写它的是会话机上的对账
    （`claude_code/runner.py` 的 `_keep_refused`），叫 agent 去读它的是
    `platform_notices.memory_conflict_notice`——同一个文件名两处要说对，所以
    在这里只定义一次。
    """
    return f"{path[: -len('.md')]}.conflict.md"


def prompt_path(path: str) -> str:
    """这条记忆在 agent 手里怎么拼：`~/.cheese/memory/project/x.md`。

    agent 的文件工具按路径里有没有 `.cheese/memory/` 这一段决定这次读写发给
    会话机还是工作机（`remote_execution/proxy.js` 的 `memoryPath`），它的系统
    提示词也是这么写的（`instructions.MEMORY_DIR`）。只说一个 `project/x.md`，
    那次读写就在工作机上找一个相对路径——记忆树在会话机上，读回来是「文件不
    存在」。
    """
    return f"~/{MEMORY_ROOT}/{path}"


def prefix_of(scope: MemoryFileScope, owner_handle: str | None) -> str:
    """会话目录里，这个作用域的前缀（`project` 或 `private/<handle>`）。"""
    if scope is MemoryFileScope.project:
        return PROJECT_PREFIX
    return f"private/{owner_handle or ''}"


def check_path(path: str) -> str:
    """一条记忆的路径必须是本目录内的一个 `.md`，不是一段能走出目录的东西。

    数据库是真相，但会话里铺下来的是真文件，而 agent 手里的 Write 能写任何路径。
    回写时按路径对号入座，所以路径先得过这一关：绝对路径、`..`、反斜杠、子目录
    一律拒绝——拒绝的理由里说得清是哪一条，比事后再去猜一个串了门的文件强。

    长度也在这里挡：`memory_files.path` 是 ``String(200)``，比它长的一句在数据库
    那一侧是一个 500（值太长放不进去），在这里是一个说得清的 422。数的是**这一层
    的名字**（`MEMORY.md` 或 `<slug>.md`），和那一列存的是同一个东西；作用域前缀
    是另一个字段，不在这里。
    """
    if not path or path.startswith("/") or "\\" in path or "\x00" in path:
        # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
        raise MemoryFileError(f"记忆文件的路径不能是 {path!r}")
    if len(path) > PATH_MAX:
        # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
        raise MemoryFileError(
            f"记忆文件的路径太长（最多 {PATH_MAX} 个字符，这条 {len(path)} 个）："
            "文件名短一点，长的那部分写进正文"
        )
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
        raise MemoryFileError(f"记忆文件的路径不能越出本目录：{path!r}")
    if len(parts) != 1:
        # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
        raise MemoryFileError(f"一条记忆就一个文件，不放在子目录里：{path!r}")
    if not path.endswith(".md"):
        # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
        raise MemoryFileError(f"记忆文件必须是 .md：{path!r}")
    if path != INDEX_NAME and not valid_name(path[: -len(".md")]):
        # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
        raise MemoryFileError(f"记忆文件名必须是 kebab-case：{path!r}")
    return path


def digest(text: str) -> str:
    """一份内容的指纹。回写靠它判「这一版是谁改的」——mtime 不行，两端是不同的
    钟；版本号也不行，它是数据库那一侧的计数，文件在会话里可以被改回原样。"""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def scoped_prefix(scope: MemoryFileScope, owner_handle: str | None) -> str:
    """一个作用域在会话目录里的前缀。空 handle 的 private 是拼不出路径的，所以
    它在这里就被拒——`private/` 后面什么都没有，读起来像一个作用域，其实是一层
    空目录，谁都对不上。"""
    if scope is MemoryFileScope.project:
        return PROJECT_PREFIX
    if not owner_handle:
        # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
        raise MemoryFileError("private 记忆必须带 owner_handle")
    return f"{PRIVATE_PREFIX}/{owner_handle}"


def check_scoped_path(path: str) -> tuple[str, str]:
    """一棵树上的一条路径 → `(作用域前缀, 文件名)`。

    作用域前缀自己也要过关：`private/alice` 是可以的，`private/alice/bob` 不是
    ——两层主人意味着这条路径对不上任何一个作用域。
    """
    parts = path.split("/")
    if parts and parts[0] == PROJECT_PREFIX:
        prefix, rest = PROJECT_PREFIX, parts[1:]
    elif len(parts) >= 2 and parts[0] == PRIVATE_PREFIX:
        prefix, rest = f"{PRIVATE_PREFIX}/{parts[1]}", parts[2:]
    else:
        # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
        raise MemoryFileError(
            f"记忆文件的路径必须以 project/ 或 private/<handle>/ 开头：{path!r}"
        )
    if len(rest) != 1:
        # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
        raise MemoryFileError(f"一条记忆就一个文件，路径不对：{path!r}")
    check_path(rest[0])
    return prefix, rest[0]
