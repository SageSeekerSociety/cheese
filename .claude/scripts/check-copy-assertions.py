#!/usr/bin/env python3
"""check-copy-assertions.py — 断用户可见文案时钉它必须说出的那几件事，
别拿整句去相等比较。

WHY THIS EXISTS. 2026-10-12 审阅 #3498 时提的：「测试别按整句文案断言」。
同一句话那轮改过两次（「4 倍」写死改掉、减少动态效果时慢放档的说明补上），
整句比较的断言每次都跟着红一次，而它想守的东西一次都没变。整句断言挡不住
真错，只挡改文案。

WHAT IT REJECTS. `frontend/` 下的测试文件（`*.spec.ts(x)`、`*.test.ts(x)`，
跳过 `node_modules`）里对用户可见文案的整句相等断言：

    expect(a.notice.value).toBe('本月的芝士额度已用完，11月1日重置。')
    expect(el?.textContent?.trim()).toBe('Save failed: HTTP 500')

两个条件同时成立才报：

  * 左值读的是渲染出来的文案：表达式里有一个属性名（或左值本身）是 text /
    textContent / innerText / innerHTML / label / message / notice / copy /
    hint / placeholder / title / heading / caption / tooltip / banner / toast
    之一；
  * 右值是一句话：中文至少 12 个汉字并且带句读（或至少 20 个汉字）；英文
    至少三个词、20 个字符。路径、URL、标识符都不算一句话。

`toBe` / `toEqual` / `toStrictEqual` 都算，`.not.`、`.resolves.`、
`.rejects.` 也认；引号单双都认，不带 `${}` 的模板字面量同样是整句，也认。
一行里有多条断言时逐条看。注释里的断言不算：`//` 和 `/* */` 里的不报；
落在另一个字符串里的断言文本也不算（比如测试自己拼的一段源码）。

WHAT IT ACCEPTS. 钉部件：

    expect(note).toContain('11月1日')
    expect(note).toContain(`${BASE_MS * MOTION_FACTOR.slow}ms`)
    expect(label.textContent).toBe('保存')

`toContain` / `toMatch` / `toBeGreaterThan` 之类不是相等断言，不报；短标签
（几个字、一个词）不是整句，不报；带 `${}` 的模板字面量本来就从常量取数，
不报。

EXEMPTION. 这句确实每个字都在断言意图里时（例如「服务器原话要原样透传」），
在那一行或它上一行写 `copy-assertions: allow — 理由`。理由写给下一个人看。

LIMITS. JSX 文本里带空格的 `/*`（`<p>先写 /* 才开始</p>`）后面又出现 `*/` 时，
中间的行会被当成注释：文本层面分不开它和真注释。主干现在没有 `.tsx` 测试文件，
出现第一条之前先收口这里。只认单行写法：断言和字面量跨行、或左值嵌了两层以上括号的，不报
（宁可漏，不误报）。左值是 `t('key')` 这类 i18n 调用时，键名不在上面的名单
里，不报。后端测试不在扫描范围：那里的 `content` / `body` 是载荷字段，
「文案」和「测试自己种进去的数据」在文本上分不开，误报会淹掉真命中。放宽
这几条之前先看过滤后的命中是不是还读得下去。

BASELINE. 2026-10-18 主干上已有 11 处，冻结在 `BASELINE` 里，一处一行
（文件、字面量）。集合只许减：树里少了一处而 BASELINE 还留着，是失败而不是
通过，所以还掉一处不会在别处换来一处新的。要重数就 `--dump`，照着结果改
`BASELINE`。基线漂出视线时，报告和干净树一模一样，那是守卫唯一赔不起的失败，
所以读不了的目录和文件、以及遮罩不到底的源码（注释或模板到末尾还没闭合）
一律退 2，绝不当作「没有命中」。

Usage: check-copy-assertions.py [--root DIR] [--json] [--dump]
                                [--baseline PATH]
Exit 0 树与冻结的基线一致，1 有新增或基线过期，2 有目录或文件读不了、判不了。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 只有这些后缀是测试文件。`.mjs` 不在其中：那份测试树还不成规模，等它有了
#: 再按同一判据加，别为一条两处的命中把正则带到另一种语法里。
TEST_SUFFIXES = (".spec.ts", ".spec.tsx", ".test.ts", ".test.tsx")

#: 装了依赖的树、产物目录不是源码；整棵 `node_modules` 不进遍历。
SKIP_DIRS = frozenset({"node_modules", "dist", "coverage", ".git"})

#: 左值里出现这些名字，才认为断言读的是文案而不是数据。小写比较，所以
#: `textContent` 和 `Text` 都算；`plaintext`、`context` 不算。
COPY_NAMES = frozenset(
    {
        "text",
        "textcontent",
        "innertext",
        "innerhtml",
        "label",
        "message",
        "notice",
        "copy",
        "hint",
        "placeholder",
        "title",
        "heading",
        "caption",
        "tooltip",
        "banner",
        "toast",
    }
)

#: 豁免标记。同一行或上一行出现即放行；后面接理由。
EXEMPT_MARKER = "copy-assertions: allow"

#: 一次相等断言。左值容许一层嵌套括号（`find('.x').text()`）；右值是单引号、
#: 双引号或不带 `${}` 的模板字面量。字面量体用惰性量词加反向引用收尾，未闭合
#: 的引号只会线性退化，不会指数回溯。
CALL = re.compile(
    r"expect\((?P<subject>(?:[^()\n]|\([^()\n]*\))*)\)\s*\.\s*(?:not\.)?"
    r"(?:resolves\.|rejects\.)?(?:toBe|toEqual|toStrictEqual)\(\s*"
    r"(?P<quote>['\"`])(?P<literal>(?:\\[^\n]|[^\\\n])*?)(?P=quote)\s*\)"
)

#: 左值里读文案的名词：取属性名（`e.message`、`el?.textContent`），左值只有
#: 一个名字时取它本身（`expect(message)`）。不取函数名和类型参数——那个位置
#: 的 `getText`、`HTMLTextAreaElement` 说的是怎么读，不是读的是什么。
PROPERTY = re.compile(r"(?:\?\.|\.)\s*([A-Za-z_$][A-Za-z0-9_$]*)")
BARE_NAME = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")

#: 汉字区段；拉丁词按空白切，所以一个带连字符的标识符算一个词。
HAN = re.compile(r"[㐀-䶿一-鿿]")
SENTENCE_PUNCT = re.compile(r"[，。！？；：、,.!?;:]")
LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]+")

#: 主干上已存在的整句断言，一行一处（文件、字面量）。只许减，见开头 BASELINE。
BASELINE: tuple[tuple[str, str], ...] = (
    ("frontend/src/apiErrorLanguage.spec.ts", "This person has already been invited and hasn't answered yet"),
    ("frontend/src/apiErrorLanguage.spec.ts", "The environment variable PATH is reserved or isn't a valid name"),
    ("frontend/src/apiErrorLanguage.spec.ts", "Only platform admins can do this"),
    ("frontend/src/apiRateLimit.spec.ts", "still answering the last question"),
    ("frontend/src/components/admin/AdminQuestionTable.spec.ts", "怎么把项目里的成员换成管理员？"),
    ("frontend/src/components/base/SaveStatus.spec.ts", "Save failed: HTTP 500"),
    ("frontend/src/components/panels/PanelSite.expand.spec.ts", "bash: pandoc: command not found"),
    ("frontend/src/components/panels/doc/blocks/reader.spec.ts", "组会报名\\n时间：周四下午三点\\n地点：B201\\n报名找林老师"),
    ("frontend/src/services/ErrorHandler.spec.ts", "Request failed with status 500"),
    ("frontend/src/views/tasks/composables/useAssistant.spec.ts", "本月的芝士额度已用完，11月1日重置。"),
    ("frontend/src/views/tasks/composables/useAssistant.spec.ts", "Cheese can't answer right now. Try again later."),
)


class CannotJudge(Exception):
    """这棵树不是要判的那棵。"""


class Unreadable(Exception):
    """这个文件遮罩不了，看不见的部分不能当作没有命中。"""


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    subject: str
    literal: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.path, self.literal)


def is_sentence(literal: str) -> bool:
    """这串字面量是不是一句给人看的话，而不是路径、URL 或标识符。"""
    text = literal.replace("\\n", " ").replace("\\'", "'").replace('\\"', '"')
    if "://" in text or text.startswith(("/", "./", "../")):
        return False
    han = len(HAN.findall(text))
    if han:
        return han >= 12 and (SENTENCE_PUNCT.search(text) is not None or han >= 20)
    words = LATIN_WORD.findall(text)
    return len(words) >= 3 and len(text) >= 20 and " " in text


def read_names(subject: str) -> set[str]:
    """左值里的属性名（小写）；左值只有一个名字时也取它本身。"""
    names = {name.lower() for name in PROPERTY.findall(subject)}
    bare = subject.strip()
    if BARE_NAME.match(bare):
        names.add(bare.lower())
    return names


def reads_copy(subject: str) -> bool:
    """左值里有没有一个读文案的名字。"""
    return bool(read_names(subject) & COPY_NAMES)


#: 引号前一格是这些符号时，它多半真的在开一个字符串。
STRING_START = frozenset("=([{,:?+-!&|;*%^~<>")

#: 引号前一个词是这些关键字时也一样（`return 'x'`、`do 'x'`）。
STRING_KEYWORDS = frozenset(
    {
        "return",
        "case",
        "throw",
        "yield",
        "await",
        "typeof",
        "instanceof",
        "in",
        "of",
        "do",
        "else",
    }
)


#: `/*` 前面是这些字符（或空白）时，它才真的在开一段注释。
COMMENT_START = frozenset("=([{,:;)]}/'\"`")


def opens_block_comment(line: str, index: int) -> bool:
    """index 上的 `/*` 是不是真在开一段注释。

    JSX 文本里的路径（`<code>src/*.ts</code>`）和 glob（`**/*.ts`）前面是
    字母或 `*`，不是注释；把它们当成注释开头会遮掉后面真正的代码。
    """
    if index == 0:
        return True
    before = line[index - 1]
    return before in " \t" or before in COMMENT_START


def opens_string(line: str, index: int) -> bool:
    """index 上的引号是不是在开一个字符串。

    JSX 文本里的撇号（`Don't`）和正则里的引号（`/it's/`）前面是字母，不能
    当作字符串开头——否则同一行后面的代码会被吞掉。这一行有没有配对的右引号
    说明不了问题：那种判断会把撇号和后面断言的字面量配成一对。
    """
    before = index - 1
    while before >= 0 and line[before] in " \t":
        before -= 1
    if before < 0:
        return True
    if line[before] in STRING_START:
        return True
    word_end = before
    while before >= 0 and (line[before].isalnum() or line[before] in "_$"):
        before -= 1
    return line[before + 1 : word_end + 1] in STRING_KEYWORDS


def mask_comments(
    line: str, in_block: bool, in_template: bool
) -> tuple[str, bool, bool, list[bool]]:
    """遮罩注释和字符串。

    返回（遮罩后的行、是否还在块注释里、是否还在多行模板里、每个字符是否在
    字符串里）。注释换成等长空格，所以位置与原文对齐；字符串本身保留，只在
    `in_string` 里标出来——断言必须落在代码上，不能落在另一个字符串里。
    """
    masked = list(line)
    in_string = [False] * len(line)
    index = 0
    quote: str | None = None
    while index < len(line):
        char = line[index]
        if in_block:
            if line.startswith("*/", index):
                masked[index] = masked[index + 1] = " "
                index += 2
                in_block = False
                continue
            masked[index] = " "
            index += 1
            continue
        if in_template or quote is not None:
            if char == "\\":
                in_string[index] = True
                if index + 1 < len(line):
                    in_string[index + 1] = True
                index += 2
                continue
            in_string[index] = True
            if in_template:
                if char == "`":
                    in_template = False
            elif char == quote:
                quote = None
            index += 1
            continue
        if char == "`":
            # 模板可以跨行，状态带在调用方那里。
            in_template = True
            in_string[index] = True
            index += 1
            continue
        if char in "'\"":
            if opens_string(line, index):
                quote = char
            in_string[index] = bool(quote)
            index += 1
            continue
        if line.startswith("//", index):
            for rest in range(index, len(masked)):
                masked[rest] = " "
            break
        if line.startswith("/*", index) and opens_block_comment(line, index):
            in_block = True
            masked[index] = masked[index + 1] = " "
            index += 2
            continue
        index += 1
    return "".join(masked), in_block, in_template, in_string


def exempted(lines: list[str], index: int) -> bool:
    """这一行或上一行带豁免标记。"""
    return EXEMPT_MARKER in lines[index] or (
        index > 0 and EXEMPT_MARKER in lines[index - 1]
    )


def scan_file(path: Path, root: Path) -> list[Finding]:
    text = path.read_bytes().decode("utf-8")
    lines = text.splitlines()
    relative = path.relative_to(root).as_posix()
    found: list[Finding] = []
    in_block = False
    in_template = False
    for index, line in enumerate(lines):
        code, in_block, in_template, in_string = mask_comments(
            line, in_block, in_template
        )
        for match in CALL.finditer(code):
            # 整条断言都在某个字符串里时，那是测试自己拼的源码，不是断言；
            # 只有开头落在字符串里不算——JSX 文本里的撇号会开出一个假字符串，
            # 它后面的真断言不能被连坐。
            if all(in_string[match.start() : match.end()]):
                continue
            literal = match.group("literal")
            if match.group("quote") == "`" and "${" in literal:
                continue
            if not reads_copy(match.group("subject")):
                continue
            if not is_sentence(literal):
                continue
            if exempted(lines, index):
                continue
            found.append(
                Finding(
                    path=relative,
                    line=index + 1,
                    subject=" ".join(match.group("subject").split()),
                    literal=literal,
                )
            )
    if in_block or in_template:
        # 遮罩器到最后还停在注释或模板里，说明这行源码它读不懂（例如 JSX
        # 文本里的 `/*`）。看不见的部分不能当作没有命中。
        opened = "块注释" if in_block else "模板字面量"
        raise Unreadable(f"第 {len(lines)} 行末尾还停在{opened}里，判断不了这一文件")
    return found


def test_files(root: Path) -> tuple[list[Path], list[str]]:
    """要扫的文件，以及遍历时读不了的目录。"""
    frontend = root / "frontend"
    if not frontend.is_dir():
        raise CannotJudge(f"{frontend} 不是目录")
    found: list[Path] = []
    unreadable: list[str] = []

    def remember(error: OSError) -> None:
        unreadable.append(f"{error.filename}: {error.strerror or error}")

    for directory, subdirectories, names in os.walk(frontend, onerror=remember):
        subdirectories[:] = sorted(
            name for name in subdirectories if name not in SKIP_DIRS
        )
        for name in sorted(names):
            if name.endswith(TEST_SUFFIXES):
                found.append(Path(directory) / name)
    return found, unreadable


def scan(root: Path) -> tuple[list[Finding], list[str]]:
    paths, unreadable = test_files(root)
    findings: list[Finding] = []
    for path in paths:
        try:
            findings.extend(scan_file(path, root))
        except (OSError, UnicodeDecodeError, Unreadable) as exc:
            unreadable.append(f"{path.relative_to(root).as_posix()}: {exc}")
    return findings, unreadable


def load_baseline(path: Path | None) -> tuple[tuple[str, str], ...]:
    """`--baseline` 给了就读它；默认只在扫仓库自己时用内置的那份。"""
    if path is None:
        return BASELINE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CannotJudge(f"{path}: 读不了基线: {exc}") from exc
    if not isinstance(data, list):
        raise CannotJudge(f"{path}: 基线应是一个 [文件, 字面量] 的列表")
    entries: list[tuple[str, str]] = []
    for item in data:
        if (
            not isinstance(item, (list, tuple))
            or len(item) != 2
            or not all(isinstance(part, str) for part in item)
        ):
            raise CannotJudge(f"{path}: 基线条目应形如 [文件, 字面量]: {item!r}")
        entries.append((item[0], item[1]))
    return tuple(entries)


def compare(
    findings: list[Finding], baseline: tuple[tuple[str, str], ...]
) -> tuple[list[Finding], list[tuple[str, str]]]:
    """按多重集比：返回（基线之外的新命中，树里已经没有的基线条目）。"""
    remaining = Counter(baseline)
    new: list[Finding] = []
    for finding in findings:
        if remaining[finding.key] > 0:
            remaining[finding.key] -= 1
        else:
            new.append(finding)
    stale = [key for key, count in sorted(remaining.items()) for _ in range(count)]
    return new, stale


def report_unreadable(unreadable: list[str]) -> None:
    for line in unreadable:
        print(f"读不了，判断不了: {line}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=REPO_ROOT, help="要扫的树，默认仓库根"
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--dump", action="store_true", help="列出全部命中，不看基线")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="基线 JSON（[文件, 字面量] 的列表）；默认只在扫仓库自己时用内置基线",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        findings, unreadable = scan(root)
        if args.dump:
            if args.json:
                print(
                    json.dumps(
                        {
                            "findings": [finding.__dict__ for finding in findings],
                            "unreadable": unreadable,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                for finding in findings:
                    print(
                        f"{finding.path}:{finding.line}: {finding.subject} — "
                        f"{finding.literal}"
                    )
                print(f"共 {len(findings)} 处")
            report_unreadable(unreadable)
            return 2 if unreadable else 0
        use_baseline = args.baseline is not None or root == REPO_ROOT
        baseline = load_baseline(args.baseline) if use_baseline else ()
    except CannotJudge as exc:
        print(f"文案断言守卫判不了这棵树: {exc}", file=sys.stderr)
        return 2
    report_unreadable(unreadable)

    new, stale = compare(findings, baseline)
    if unreadable:
        # 读不了的树不是「少了几处」，别让人照着把基线删掉。
        stale = []
    if args.json:
        print(
            json.dumps(
                {
                    "new": [finding.__dict__ for finding in new],
                    "stale": [list(key) for key in stale],
                    "unreadable": unreadable,
                    "frozen": len(baseline),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for finding in new:
            print(
                f"{finding.path}:{finding.line}: 整句断言「{finding.literal}」"
                f"（{finding.subject}）—— 钉它必须说出的那几件事，"
                "数字和倍数从代码里的常量取",
                file=sys.stderr,
            )
        for path, literal in stale:
            print(
                f"{path}: 「{literal}」已经不在树里了，从 BASELINE 删掉这一行",
                file=sys.stderr,
            )
        if unreadable and (new or baseline):
            print(
                "有目录或文件读不了，这次不判基线是否过期；先让它们可读再跑。",
                file=sys.stderr,
            )
        if not new and not stale and not unreadable:
            print(f"文案断言守卫通过（冻结 {len(baseline)} 处已记录的整句断言）")

    if unreadable:
        return 2
    if new or stale:
        print(
            f"文案断言守卫：新增 {len(new)} 处，基线过期 {len(stale)} 处。"
            f"要保留整句时，在那一行或上一行写 `{EXEMPT_MARKER} — 理由`",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
