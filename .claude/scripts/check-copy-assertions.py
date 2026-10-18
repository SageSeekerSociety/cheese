#!/usr/bin/env python3
"""check-copy-assertions.py — 断用户可见文案时钉它必须说出的那几件事，
别拿整句去相等比较。

WHY THIS EXISTS. 2026-10-12 审阅 #3498 时提的：「测试别按整句文案断言」。
同一句话那轮改过两次（「4 倍」写死改掉、减少动态效果时慢放档的说明补上），
整句比较的断言每次都跟着红一次，而它想守的东西一次都没变。整句断言挡不住
真错，只挡改文案。

WHAT IT REJECTS. `frontend/` 下的测试文件（`*.spec.ts(x)`、`*.test.ts(x)`，
跳过 `node_modules`）里对用户可见文案的整句相等断言：

    expect(note).toBe('本月的芝士额度已用完，11月1日重置。')
    expect(el?.textContent?.trim()).toBe('Save failed: HTTP 500')

两个条件同时成立才报：

  * 左值读的是渲染出来的文案：表达式里有一个属性名（或左值本身）是 text /
    textContent / innerText / innerHTML / label / message / notice / copy /
    hint / placeholder / title / heading / caption / tooltip / banner / toast
    之一；
  * 右值是一句话：中文至少 12 个汉字并且带句读（或至少 20 个汉字）；英文
    至少三个词、20 个字符。路径、URL、标识符都不算一句话。

WHAT IT ACCEPTS. 钉部件：

    expect(note).toContain('11月1日')
    expect(note).toContain(`${BASE_MS * MOTION_FACTOR.slow}ms`)
    expect(label.textContent).toBe('保存')

`toContain` / `toMatch` / `toBeGreaterThan` 之类不是相等断言，不报；短标签
（几个字、一个词）不是整句，不报。

EXEMPTION. 这句确实每个字都在断言意图里时（例如「服务器原话要原样透传」），
在那一行或它上一行写 `copy-assertions: allow — 理由`。理由写给下一个人看。

LIMITS. 只认单行写法：断言和字面量跨行、或左值嵌了两层以上括号的，不报
（宁可漏，不误报）。左值是 `t('key')` 这类 i18n 调用时，键名不在上面的名单
里，不报。后端测试不在扫描范围：那里的 `content` / `body` 是载荷字段，
「文案」和「测试自己种进去的数据」在文本上分不开，误报会淹掉真命中。放宽
这几条之前先看过滤后的命中是不是还读得下去。

BASELINE. 2026-10-18 主干上已有 11 处，冻结在 `BASELINE` 里，一处一行
（文件、字面量）。集合只许减：树里少了一处而 BASELINE 还留着，是失败而不是
通过，所以还掉一处不会在别处换来一处新的。要重数就 `--dump`，照着结果改
`BASELINE`。基线漂出视线时，报告和干净树一模一样，那是守卫唯一赔不起的失败。

Usage: check-copy-assertions.py [--root DIR] [--json] [--dump]
                                [--baseline PATH]
Exit 0 树与冻结的基线一致，1 有新增或基线过期，2 有文件读不了。
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

#: 左值里出现这些词，才认为断言读的是文案而不是数据。按 camelCase /
#: snake_case 拆词后小写比较，所以 `textContent`、`text_content` 都算，
#: 而 `plaintext`、`context` 不算。
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

#: 一次相等断言。左值容许一层嵌套括号（`find('.x').text()`），右值只认单引号
#: 或双引号字符串——模板字面量天然就是钉部件，不在拒绝范围里。
CALL = re.compile(
    r"expect\((?P<subject>(?:[^()\n]|\([^()\n]*\))*)\)\s*\.\s*(?:not\.)?"
    r"(?:toBe|toEqual|toStrictEqual)\(\s*(?P<quote>['\"])"
    r"(?P<literal>(?:\\.|(?!(?P=quote)).)*)(?P=quote)\s*\)"
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


def exempted(lines: list[str], index: int) -> bool:
    """这一行或上一行带豁免标记。"""
    return EXEMPT_MARKER in lines[index] or (
        index > 0 and EXEMPT_MARKER in lines[index - 1]
    )


def scan_file(path: Path, root: Path) -> list[Finding]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    relative = path.relative_to(root).as_posix()
    found: list[Finding] = []
    for index, line in enumerate(lines):
        match = CALL.search(line)
        if match is None:
            continue
        if not reads_copy(match.group("subject")):
            continue
        if not is_sentence(match.group("literal")):
            continue
        if exempted(lines, index):
            continue
        found.append(
            Finding(
                path=relative,
                line=index + 1,
                subject=" ".join(match.group("subject").split()),
                literal=match.group("literal"),
            )
        )
    return found


def test_files(root: Path) -> list[Path]:
    frontend = root / "frontend"
    if not frontend.is_dir():
        raise CannotJudge(f"{frontend} 不是目录")
    found: list[Path] = []
    for directory, subdirectories, names in os.walk(frontend):
        subdirectories[:] = sorted(
            name for name in subdirectories if name not in SKIP_DIRS
        )
        for name in sorted(names):
            if name.endswith(TEST_SUFFIXES):
                found.append(Path(directory) / name)
    return found


def scan(root: Path) -> tuple[list[Finding], list[str]]:
    findings: list[Finding] = []
    unreadable: list[str] = []
    for path in test_files(root):
        try:
            findings.extend(scan_file(path, root))
        except OSError as exc:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=REPO_ROOT, help="要扫的树，默认仓库根"
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument(
        "--dump", action="store_true", help="列出全部命中，不看基线"
    )
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
                        [finding.__dict__ for finding in findings],
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
            return 2 if unreadable else 0
        use_baseline = args.baseline is not None or root == REPO_ROOT
        baseline = load_baseline(args.baseline) if use_baseline else ()
    except CannotJudge as exc:
        print(f"文案断言守卫判不了这棵树: {exc}", file=sys.stderr)
        return 2
    for line in unreadable:
        print(f"读不了这个测试文件，判断不了: {line}", file=sys.stderr)

    new, stale = compare(findings, baseline)
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
        if not new and not stale:
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
