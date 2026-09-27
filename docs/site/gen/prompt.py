"""Dump the blocks of the agent system prompt as JSON — as it is assembled.

The page this feeds (``docs/manual/dev/ref-prompt.md``) has to say what the
system prompt is made of without anyone reading the code, so nothing here is a
hand-written list of blocks: the generator **runs** ``build_system_prompt`` over
a matrix of sample arguments and splits every result at the line-start ``## ``
that marks a block. A block that is added, renamed or reordered shows up on the
page by itself, and a parameter that is added — even one this file has never
heard of — gets its own sample, so the block it opens is not missing either.
That sample failing (a parameter that wants a real object, not a string) costs
that one sample and is reported on the page; the rest still run.

Why the first path is not a plain import: the docs build machine has no backend
dependencies (the docs job installs node; ``python3`` is the system one), so
``import app.…`` cannot resolve. Instead of giving up, ``app`` is stubbed out
(``_StubFinder``) and the real function is called — the text on the page is the
text the module produces. A leak from a stub is recognisable (``<stub>``) and
unpublishes the run.

If even that fails, the blocks are read out of the source with ``ast``: every
``parts.append(...)`` becomes a block, the enclosing ``if`` becomes its
condition, and anything that is not a literal is marked 动态生成. Both paths
report the same shape, so the page renders either way.

Known limitation: a block is split at *any* line that starts with ``## ``,
including one inside injected content (a skill body, a living doc). The sample
arguments avoid it — sample values are one-liners — and a stage guide that
would split is skipped in favour of one that does not.
"""

import ast
import difflib
import importlib.abc
import importlib.util
import json
import os
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
AGENT = ROOT / "backend" / "app" / "domain" / "agent"
PROMPT_PY = AGENT / "harness" / "prompt.py"
STAGES_PY = AGENT / "stages.py"
SKILLS_PY = AGENT / "skills.py"
SKILL_DIR = AGENT / "skill_library"

#: ``build_system_prompt``'s first three arguments are positional; the samples
#: below give the rest by keyword.
BASE = "（底稿：`settings.agent_system_prompt`，部署时配置的那一段开头）"


class _Fallback(Exception):
    """Raised when the real run cannot be trusted; the caller parses instead."""


# --------------------------------------------------------------------------
# loading prompt.py on a machine that cannot import the backend
# --------------------------------------------------------------------------
class _Any:
    """Absorbs any use of a stubbed module: attribute access, a call, ``|`` in
    an annotation, iteration. Renders as ``<stub>`` so a value that leaks into
    the generated text is recognisable rather than published quietly."""

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _Any()

    def __call__(self, *args, **kwargs):
        return _Any()

    def __getitem__(self, key):
        return _Any()

    def __or__(self, other):
        return self

    def __ror__(self, other):
        return self

    def __iter__(self):
        return iter(())

    def __repr__(self):
        return "<stub>"

    def __format__(self, spec):
        return "<stub>"


class _StubModule(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _Any()


class _StubLoader(importlib.abc.Loader):
    def create_module(self, spec):
        return _StubModule(spec.name)

    def exec_module(self, module):
        pass


class _StubFinder(importlib.abc.MetaPathFinder):
    """Hand out empty modules for ``app.*`` — the backend, which the docs build
    machine has no dependencies for. Installed first so the result is the same
    whether or not a backend virtualenv happens to be active."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "app" or fullname.startswith("app."):
            # is_package: a submodule is imported through its parent, which
            # needs a `__path__` even when everything under it is a stub.
            return importlib.util.spec_from_loader(fullname, _StubLoader(), is_package=True)
        return None


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_prompt_module():
    finder = _StubFinder()
    sys.meta_path.insert(0, finder)
    try:
        return load_module(PROMPT_PY, "cheese_prompt_reference")
    finally:
        sys.meta_path.remove(finder)


def load_skills_module():
    """``skills.py`` is stdlib-only, so it loads for real — the sample stage
    guide is then the very text ``build_system_prompt`` receives."""
    return load_module(SKILLS_PY, "cheese_skill_reference")


# --------------------------------------------------------------------------
# sample arguments: one switch per optional parameter, so every block's
# condition is read off which sample produced it
# --------------------------------------------------------------------------
class _Week:
    """Stand-in for ``protocol.Teaching`` — only what ``teaching_section``
    reads, with the token substitution its ``fill`` documents."""

    system_prompt = "本周是第 {current_week} 周，重点是 {allowed_topics}。"
    current_week = 6
    allowed_topics = ["循环", "数组"]
    avoid_in_code = ["递归"]

    @property
    def is_empty(self) -> bool:
        return not (
            self.system_prompt
            or self.current_week is not None
            or self.allowed_topics
            or self.avoid_in_code
        )

    def fill(self, template: str) -> str:
        for token, value in (
            ("{current_week}", str(self.current_week)),
            ("{allowed_topics}", "、".join(self.allowed_topics)),
            ("{avoid_in_code}", "、".join(self.avoid_in_code)),
        ):
            template = template.replace(token, value)
        return template


#: A stand-in for ``task.teaching.TeachingContext``: a resolved week, the two
#: kinds of material it points at.
TEACHING = types.SimpleNamespace(
    course="创研课 2026 秋",
    teaching=_Week(),
    materials=[{"id": 7, "name": "第 6 周讲义", "url": "https://example.com/week6.pdf"}],
    knowledge=[{"id": 11, "name": "推荐系统入门", "description": "课程上传的参考材料"}],
)

ROSTER = [
    {"name": "张衡", "handle": "zhangheng", "source": "owner", "agent": False, "active": True},
    {"name": "李工", "handle": "ligong", "source": "team", "agent": False, "active": True},
    {"name": "芝士", "handle": "cheese", "source": "team", "agent": True, "active": True},
]

TOPICS = [{"title": "搭建推荐算法原型"}, {"title": "接口联调"}]

ARTIFACTS = [
    {"name": "实验报告", "version": 3, "about": "交给老师的那份结题报告", "id": "a1b2c3"},
    {"name": "演示网站", "version": 0, "about": "给评审看的可点开的站点", "id": "d4e5f6"},
]

MEMORIES = ["构建前端用 `npm ci`，不要用 `npm install`。", "配置在 backend/app/core/db.py 里。"]

SESSION_OPENING = [
    "- 这台机器：内存 4GB、2 核。吃内存的命令（前端 build/typecheck、大型编译）可能被内核 OOM 杀掉。",
    "- 你不在场时这个话题又说了 12 条，用 `cheese_chat_list` 读。",
]

#: Sample texts for injected content are one-liners on purpose: the page splits
#: blocks at ``## `` and an injected document has headings of its own.
DOC = "目标：本周内跑通推荐流程。当前：数据已就位，模型还在调。"
OVERVIEW_DOC = "项目目标：把推荐算法做成一个能演示的原型。当前：三个人在做，接口这周联调。"

#: Long enough to blow both 6000-character budgets, so the compressed form and
#: its note are on the page too.
OVERSIZE = "\n\n".join(
    ["### 目标\n" + "把推荐算法做成一个能演示的原型。" * 40]
    + ["### 临时笔记\n" + "随手记的东西，丢了也不可惜。" * 200]
    + ["### 进展记录\n" + "这一周做了什么，按天记。" * 400]
    + ["### 决策与约定\n" + "定下来的事，谁都不能悄悄改。" * 300]
)

#: (id, kwargs, what the reader should read the switch as, parameter spellings)
TOGGLES = [
    {
        "id": "untitled",
        "kwargs": {"untitled": True},
        "label": "`untitled=True`：话题还没有名字，本轮第一个动作是先起名",
        "params": ["untitled=True"],
    },
    {
        "id": "role",
        "kwargs": {"role": "你是一位资深的全栈工程师，负责把这个项目的界面做出来。"},
        "label": "`role` 非空：项目给了这个 AI 队友一个专家角色",
        "params": ["role=…"],
    },
    {
        "id": "teaching",
        "kwargs": {"teaching": TEACHING},
        "label": "`teaching` 非空，且这一周的教学安排不是空的（`teaching_section` 说了话）",
        "params": ["teaching=…"],
    },
    {
        "id": "skills",
        "kwargs": {},  # filled from the skill library
        "label": "`skills` 非空：本场景选出了技能",
        "params": ["skills=…"],
    },
    {
        "id": "stage_guide",
        "kwargs": {"stage_guide": None},  # filled in from the skill library
        "label": "`stage_guide` 非空：当前阶段有一段说明",
        "params": ["stage_guide=…"],
    },
    {
        "id": "topics",
        "kwargs": {"topics": TOPICS},
        "label": "`topics` 非空：项目里有活跃话题",
        "params": ["topics=…"],
    },
    {
        "id": "artifacts",
        "kwargs": {"artifacts": ARTIFACTS},
        "label": "`artifacts is not None`：传了清单（空清单是另一种写法，见变体）",
        "params": ["artifacts=…"],
    },
    {
        "id": "roster",
        "kwargs": {"roster": ROSTER},
        "label": "`roster` 非空：项目有成员名册",
        "params": ["roster=…"],
    },
    {
        "id": "overview_doc",
        "kwargs": {"overview_doc": OVERVIEW_DOC},
        "label": "`overview_doc` 非空：项目总览文档有正文",
        "params": ["overview_doc=…"],
    },
    {
        "id": "topic_doc",
        "kwargs": {"doc": DOC},
        "label": "`doc` 非空：本话题的实况文档有正文",
        "params": ["doc=…"],
    },
    {
        "id": "memories",
        "kwargs": {"memories": MEMORIES},
        "label": "`memories` 非空",
        "params": ["memories=…"],
    },
    {
        "id": "memories_omitted",
        "kwargs": {"memories_omitted": 9},
        "label": "`memories_omitted` 非零",
        "params": ["memories_omitted=9"],
    },
    {
        "id": "memories_core_omitted",
        "kwargs": {"memories": MEMORIES, "memories_core_omitted": 2},
        "label": "`memories_core_omitted` 非零",
        "params": ["memories=…", "memories_core_omitted=2"],
    },
    {
        "id": "session_opening",
        "kwargs": {"session_opening": SESSION_OPENING},
        "label": "`session_opening` 非空：会话开场的那两条运行环境",
        "params": ["session_opening=…"],
    },
]

#: Switches that turn on one block together: the block is there when any of
#: them is, so the page names the group rather than three near-synonyms.
GROUPS = [
    (
        ["memories", "memories_omitted", "memories_core_omitted"],
        "项目记忆相关参数任一非空（`memories` / `memories_omitted` / `memories_core_omitted`）",
    ),
]

#: Parameters the samples above speak for. Anything else the signature asks for
#: is filled from its annotation, so a new parameter degrades the page instead
#: of breaking the build.
SPOKEN_FOR = {"base", "skills", "doc", "memories"} | {
    key for toggle in TOGGLES for key in toggle["kwargs"]
}

#: Switch id for one of those unknown parameters: the block it opens reads its
#: condition back as "this parameter", instead of "some switch not in the list".
UNKNOWN = "unknown:"


def fill_unknown(signature) -> tuple[dict, list[str]]:
    """Values for parameters this generator does not know about yet.

    Defaulted parameters are filled too: a switch whose value nobody set is a
    switch nobody has seen, and its block would go missing from the page. The
    page names these parameters as ones the generator has not caught up with."""
    import inspect

    filled: dict = {}
    names: list[str] = []
    for name, parameter in signature.parameters.items():
        if name in SPOKEN_FOR or parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        annotation = str(parameter.annotation)
        if "bool" in annotation:
            filled[name] = True
        elif "list" in annotation:
            filled[name] = ["（生成器按参数类型补的示例值）"]
        elif "int" in annotation:
            filled[name] = 1
        else:
            filled[name] = "（生成器按参数类型补的示例值）"
        names.append(name)
    return filled, names


def samples_for(module, stage_guide: str, skills_text: str) -> list[dict]:
    toggles = []
    for toggle in TOGGLES:
        kwargs = dict(toggle["kwargs"])
        if toggle["id"] == "skills":
            kwargs["skills"] = skills_text
        if kwargs.get("stage_guide", "missing") is None:
            kwargs["stage_guide"] = stage_guide
        toggles.append({**toggle, "kwargs": kwargs})

    import inspect

    extra, unknown = fill_unknown(inspect.signature(module.build_system_prompt))
    full = {"skills": skills_text, "doc": DOC, "memories": MEMORIES, **extra}
    for toggle in toggles:
        full.update(toggle["kwargs"])

    samples = [
        {
            "name": "最小",
            "about": "除底稿外的参数一律不传（`skills=\"\"`、`doc=None`、`memories=[]`）",
            "kwargs": {"skills": "", "doc": None, "memories": []},
        },
        *[
            {
                "name": toggle["id"],
                "about": toggle["label"],
                "toggle": toggle["id"],
                "kwargs": {
                    "skills": "",
                    "doc": None,
                    "memories": [],
                    **toggle["kwargs"],
                },
            }
            for toggle in toggles
        ],
        # One sample per parameter this file has not caught up with, alone, so the
        # block it opens can say which parameter opened it.
        *[
            {
                "name": f"参数 {name}",
                "about": f"`{name}`：生成器还不认识的参数，值是按类型补的",
                "toggle": f"{UNKNOWN}{name}",
                "kwargs": {"skills": "", "doc": None, "memories": [], name: extra[name]},
            }
            for name in unknown
        ],
        {"name": "全部打开", "about": "上面每个开关都给值", "kwargs": full},
        {"name": "总览文档超预算", "about": f"`overview_doc` 给了 {len(OVERSIZE)} 字符，超过 6000 字上限，看压缩后的样子", "kwargs": {"skills": "", "doc": None, "memories": [], "overview_doc": OVERSIZE}},
        {"name": "产物清单空着", "about": "`artifacts=[]`：清单空着时那一段是短指针", "kwargs": {"skills": "", "doc": None, "memories": [], "artifacts": []}},
    ]
    return samples, unknown


# --------------------------------------------------------------------------
# the real run
# --------------------------------------------------------------------------
def split_blocks(text: str) -> list[str]:
    """Split one assembled prompt into blocks: a block starts at a line whose
    start is ``## ``. Nothing here knows a block's name."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.split("\n"):
        if line.startswith("## "):
            if current:
                blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)
    return ["\n".join(block).strip("\n") for block in blocks if "\n".join(block).strip()]


def segments(text: str) -> list[str]:
    """A prompt split at blank lines — the granularity the diff works at."""
    return [part for part in (p.strip("\n") for p in text.split("\n\n")) if part]


def heading_key(text: str) -> str:
    """What makes two blocks the same block: the first line."""
    return text.split("\n", 1)[0]


def title_of(key: str) -> str:
    """The block's name for the page: its first line, without the markdown
    marker. An injected body brings its own `# ` heading, hence the wider strip."""
    return re.sub(r"^#{1,6} ", "", key)


def contribution(bare: str, text: str) -> str:
    """What one sample added or changed, relative to the 「最小」 sample.

    Read as a run of blank-line-separated segments, because that is the one
    boundary the prompt itself guarantees (`"\n\n".join(parts)`). It is how a
    part that carries no `## ` heading of its own — the `skills` argument, whose
    value starts with the skill's own `# ` — is told apart from the part before
    it instead of being glued onto it."""
    before, after = segments(bare), segments(text)
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    changed = [j for tag, _i1, _i2, j1, j2 in matcher.get_opcodes() if tag != "equal" for j in range(j1, j2)]
    if not changed:
        return ""
    return "\n\n".join(after[min(changed) : max(changed) + 1])


def contribution_blocks(text: str) -> list[str]:
    """One contribution is one block — unless it is made of standard `## `
    parts, in which case each of those is. An injected body keeps its own
    headings and stays whole: that body is one thing the caller passed."""
    if text.startswith("## "):
        return split_blocks(text)
    return [text]


def constants_of(module) -> list[dict]:
    """Module-level constants of prompt.py: name, kind, line, value."""
    source = PROMPT_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()
    out = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or not target.id.isupper():
            continue
        try:
            value = getattr(module, target.id)
        except AttributeError:
            value = _literal(node.value)
        kind = "int" if isinstance(value, int) and not isinstance(value, bool) else "str" if isinstance(value, str) else None
        if kind is None:
            continue
        out.append(
            {
                "name": target.id,
                "kind": kind,
                "line": target.lineno,
                "value": value,
                "comment": comment_above(lines, target.lineno),
            }
        )
    return out


def comment_above(lines: list[str], lineno: int) -> str:
    """The contiguous comment block above a module-level constant."""
    out = []
    i = lineno - 2
    while i >= 0 and lines[i].strip().startswith("#"):
        # `#: text` is the sphinx-style comment the constants use; the inner `#`
        # of a reference like `#1535` is not a comment marker and stays.
        out.append(lines[i].strip().lstrip("#").lstrip(":").strip())
        i -= 1
    return " ".join(reversed(out)).strip()


def run_exec(module, samples) -> dict:
    results, failed = {}, []
    for sample in samples:
        calls: list = []
        real = module.fit_doc_to_budget

        def spy(text, budget, *, full_read_hint, _real=real, _calls=calls):
            out = _real(text, budget, full_read_hint=full_read_hint)
            _calls.append((budget, out))
            return out

        module.fit_doc_to_budget = spy
        try:
            text = module.build_system_prompt(BASE, **sample["kwargs"])
        except Exception as exc:  # noqa: BLE001 — one sample failing costs that sample
            failed.append({"name": sample["name"], "error": f"{type(exc).__name__}: {exc}"})
            continue
        finally:
            module.fit_doc_to_budget = real
        results[sample["name"]] = {"text": text, "calls": calls}

    if "最小" not in results:
        raise _Fallback("连「最小」那个样本都跑不出来：" + "; ".join(f["error"] for f in failed))
    if "<stub>" in "".join(result["text"] for result in results.values()):
        raise _Fallback("样板输出里出现了替身对象（<stub>），真跑这条路不可信")

    bare = results["最小"]["text"]
    full_name = "全部打开"
    full = results[full_name]["text"] if full_name in results else bare

    constants = constants_of(module)
    by_value: dict = {}
    for constant in constants:
        by_value.setdefault(constant["value"], []).append(constant["name"])

    # Every block: the ones 「最小」 already has, plus whatever each sample adds.
    additions: list[tuple] = [("最小", "", block) for block in split_blocks(bare)]
    for sample in samples:
        if sample["name"] in ("最小", full_name) or sample["name"] not in results:
            continue
        changed = contribution(bare, results[sample["name"]]["text"])
        if not changed:
            continue
        for block in contribution_blocks(changed):
            additions.append((sample["name"], sample.get("toggle", ""), block))

    entries: dict[str, dict] = {}
    for sample_name, toggle, block in additions:
        key = heading_key(block)
        entry = entries.setdefault(
            key,
            {
                "key": key,
                "title": title_of(key),
                "heading": key.startswith("## "),
                "text": None,
                "at": None,
                "samples": [],
                "toggles": [],
                "variants": [],
            },
        )
        entry["samples"].append(sample_name)
        if toggle:
            entry["toggles"].append(toggle)
        position = full.find(block)
        if position >= 0 and entry["at"] is None:
            # Where the block sits in the fully-opened prompt is its place in
            # the order — no order list to keep up to date.
            entry["at"] = position
            entry["text"] = block
        elif (
            entry["text"] is not None
            and block != entry["text"]
            and len(entry["variants"]) < 5
            and not any(variant[1] == block for variant in entry["variants"])
        ):
            entry["variants"].append([sample_name, block])

    blocks = []
    for entry in sorted(entries.values(), key=lambda e: (e["at"] is None, e["at"] or 0)):
        texts = [entry["text"] or "", *[variant[1] for variant in entry["variants"]]]
        condition, params = condition_of(entry["toggles"])
        blocks.append(
            {
                "heading": entry["key"],
                "title": entry["title"],
                "headline": entry["heading"],
                "text": entry["text"] or "",
                "chars": len(entry["text"] or ""),
                "condition": condition,
                "params": params,
                "always": not entry["toggles"],
                "base": not entry["toggles"] and not entry["heading"] and entry["at"] == 0,
                "budget": budget_of(texts, results, by_value),
                "variants": entry["variants"],
                "said_in": entry["samples"],
                "origin": "literal",
            }
        )
    return {
        "mode": "exec",
        "note": "",
        "samples": [
            {"name": sample["name"], "about": sample["about"], "params": sample_params(sample["kwargs"])}
            for sample in samples
            if sample["name"] in results
        ],
        "failed_samples": failed,
        "blocks": blocks,
        "constants": [finish_constant(constant, blocks) for constant in constants],
    }


def sample_params(kwargs: dict) -> list[str]:
    """The sample's arguments as the page's table spells them."""
    out = []
    for key, value in kwargs.items():
        if isinstance(value, bool):
            shown = repr(value)
        elif value is None:
            shown = "None"
        elif isinstance(value, int):
            shown = repr(value)
        elif isinstance(value, list):
            shown = f"（{len(value)} 项）" if value else "[]"
        elif isinstance(value, dict):
            shown = "（一项对象）"
        elif value == "":
            shown = '""'
        elif key == "teaching":
            shown = "（一个解析好的 TeachingContext）"
        elif key == "stage_guide":
            shown = "（当前阶段的说明原文，见「阶段的操作说明」）"
        elif key == "skills":
            shown = "（技能库里 chat 场景的原文，见「技能库」）"
        else:
            shown = "（一段样本文本）"
        out.append(f"{key}={shown}")
    return out


def budget_of(texts: list[str], results: dict, by_value: dict):
    """The budget constant a block was compressed with, when that is clear: the
    call's own result has to appear inside that block, and of the constants
    holding that value only one may fit the sample the call happened in."""
    for sample_name, result in results.items():
        for budget, out in result["calls"]:
            if not out or not any(out in text for text in texts):
                continue
            names = by_value.get(budget, [])
            hint = "OVERVIEW" if "overview" in sample_name else "TOPIC" if "topic" in sample_name else ""
            chosen = [name for name in names if hint and hint in name] or names
            if len(chosen) == 1:
                return {"value": budget, "const": chosen[0], "sample": sample_name}
    return None


def condition_of(toggles: list[str]):
    """Which sample switches produce this block."""
    if not toggles:
        return "每一轮都有（不依赖任何可选参数）", []
    labels, params = [], []
    for ids, label in GROUPS:
        if all(i in toggles for i in ids):
            labels.append(label)
            params.extend(f"{i}=…" for i in ids)
    for toggle in TOGGLES:
        if toggle["id"] not in toggles or any(toggle["id"] in ids for ids, _ in GROUPS):
            continue
        labels.append(toggle["label"])
        params.extend(toggle["params"])
    for name in [t[len(UNKNOWN) :] for t in toggles if t.startswith(UNKNOWN)]:
        labels.append(f"`{name}`：生成器还不认识的参数，样本值是按类型补的")
        params.append(f"{name}=…")
    if not labels:
        return "触发它的参数不在本文列出的开关里——见「样本参数」", []
    return " 或 ".join(labels), params


def finish_constant(constant: dict, blocks: list[dict]) -> dict:
    """A constant plus the blocks whose text contains it (a long text constant
    is a block in a name; a budget constant is not, and gets none)."""
    value = constant["value"]
    hits = []
    if isinstance(value, str) and len(value) >= 20:
        for block in blocks:
            if value in block["text"]:
                hits.append(block["title"])
    return {**constant, "in_blocks": hits}


# --------------------------------------------------------------------------
# the static run
# --------------------------------------------------------------------------
def _literal(node):
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _render(node) -> tuple[str, bool]:
    """A literal text for one `parts.append` argument, with `{expr}` for the
    parts that only exist at run time. Second value: was anything dropped."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, False
    if isinstance(node, ast.JoinedStr):
        out, dynamic = [], False
        for value in node.values:
            if isinstance(value, ast.Constant):
                out.append(str(value.value))
            else:
                out.append("{" + ast.unparse(value.value) + "}")
                dynamic = True
        return "".join(out), dynamic
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, ld = _render(node.left)
        right, rd = _render(node.right)
        return left + right, ld or rd
    return "{" + ast.unparse(node) + "}", True


def _walk_parts(stmts, conditions, out):
    for statement in stmts:
        if isinstance(statement, ast.If):
            _walk_parts(statement.body, conditions + [ast.unparse(statement.test)], out)
            _walk_parts(statement.orelse, conditions + ["not " + ast.unparse(statement.test)], out)
        elif isinstance(statement, (ast.For, ast.While)):
            _walk_parts(statement.body, conditions + ["循环里逐个拼"], out)
        elif isinstance(statement, (ast.With, ast.Try)):
            _walk_parts(statement.body, conditions, out)
        elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            call = statement.value
            func = call.func
            if isinstance(func, ast.Attribute) and func.attr in ("append", "extend") and isinstance(func.value, ast.Name) and func.value.id == "parts":
                out.append((call.args[0] if call.args else call, conditions))


def run_ast(reason: str) -> dict:
    source = PROMPT_PY.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        # Neither path can say anything true about the prompt now. Fail the docs
        # build with one line instead of a traceback from inside the fallback.
        raise SystemExit(f"读不出 prompt.py：{reason}；静态解析也不行（{exc}）") from exc
    lines = source.splitlines()
    function = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build_system_prompt"),
        None,
    )
    if function is None:
        raise SystemExit(f"prompt.py 里没有 build_system_prompt：{reason}")

    appends: list[tuple] = []
    _walk_parts(function.body, [], appends)

    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id.isupper():
            value = _literal(node.value)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                constants[node.targets[0].id] = {"name": node.targets[0].id, "kind": "int" if isinstance(value, int) else "str", "line": node.targets[0].lineno, "value": value, "comment": comment_above(lines, node.targets[0].lineno)}

    blocks = []
    for node, conditions in appends:
        # `parts.append(CONSTANT)` is a whole block in a name: read the name's
        # text out of the module, so the always-on blocks are not `{ALWAYS_PUSH}`.
        if isinstance(node, ast.Name) and isinstance(constants.get(node.id, {}).get("value"), str):
            text, dynamic = constants[node.id]["value"], False
        else:
            text, dynamic = _render(node)
        used = sorted({n.id for n in ast.walk(node) if isinstance(n, ast.Name) and n.id in constants and "BUDGET" in n.id})
        budget = {"value": constants[used[0]]["value"], "const": used[0]} if len(used) == 1 else None
        for block in split_blocks(text):
            heading = block.split("\n", 1)[0]
            blocks.append(
                {
                    "heading": heading,
                    "title": title_of(heading),
                    "headline": heading.startswith("## "),
                    "text": block,
                    "chars": len(block),
                    "condition": "；".join(conditions) if conditions else "每次都会拼进来",
                    "params": conditions,
                    "always": not conditions,
                    "base": False,
                    "budget": budget,
                    "variants": [],
                    "said_in": [],
                    "origin": "dynamic" if dynamic else "literal",
                    "source_line": node.lineno,
                }
            )
    return {
        "mode": "ast",
        "note": reason,
        "samples": [],
        "failed_samples": [],
        "blocks": blocks,
        "constants": [finish_constant(c, blocks) for c in constants.values()],
    }


# --------------------------------------------------------------------------
# stages.py and the skill library: static, so both paths have them
# --------------------------------------------------------------------------
def read_stages() -> dict:
    source = STAGES_PY.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source)
    members, card_map, precedence = [], [], []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "TopicStage":
            for statement in node.body:
                if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Constant):
                    members.append(
                        {
                            "name": statement.targets[0].id,
                            "value": statement.value.value,
                            "note": comment_above(lines, statement.targets[0].lineno),
                            "line": statement.targets[0].lineno,
                        }
                    )
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_CARD_STAGE" for t in node.targets):
            for key, value in zip(node.value.keys, node.value.values):
                card_map.append([_qualified(key), _qualified(value)])
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_CARD_PRECEDENCE" for t in node.targets):
            precedence = [_qualified(element) for element in node.value.elts]
    return {"members": members, "card_map": card_map, "precedence": precedence}


def _qualified(node) -> str:
    """`AcceptStatus.conflict` rather than `conflict` — the page has to say who
    owns the name."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return ast.unparse(node)


def read_library() -> dict:
    """Every skill file: frontmatter and body, parsed the way `skills.py` parses
    them (same tag splitting, so the library and the loader cannot disagree)."""
    try:
        skills = load_skills_module()
        parse, parse_tags = skills._parse, skills._parse_tags
    except Exception:
        parse, parse_tags = _fallback_parse, _fallback_tags

    files = []
    for path in sorted(SKILL_DIR.glob("*.md")):
        meta, body = parse(path)
        files.append(
            {
                "file": f"backend/app/domain/agent/skill_library/{path.name}",
                "name": meta.get("name", path.stem),
                "title": meta.get("title", path.stem),
                "scenarios": parse_tags(meta.get("scenarios", "")),
                "description": meta.get("description", ""),
                "body": body,
                "chars": len(body),
            }
        )
    return {"dir": "backend/app/domain/agent/skill_library", "files": files}


def scenario_skills(library: dict, scenario: str = "chat") -> str:
    """What the `skills` argument holds for one scenario — the same
    concatenation `skills.load_scenario` builds, from the same files."""
    return "\n\n---\n\n".join(file["body"] for file in library["files"] if scenario in file["scenarios"])


def _fallback_parse(path: Path):
    text = path.read_text(encoding="utf-8")
    meta, body = {}, text
    if text.startswith("---"):
        _, front, body = text.split("---", 2)
        for line in front.strip().splitlines():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, body.strip()


def _fallback_tags(value: str) -> list[str]:
    return [t.strip() for t in value.strip().strip("[]").split(",") if t.strip()]


def stage_sample_guide(library: dict, stages: dict, stage_guide: str) -> str:
    """The stage guide the samples use — a real one, from the library.

    A guide whose body has a ``\n## `` in it is skipped: it would split the
    block the sample is there to demonstrate."""
    order = [stage_guide, *[member["value"] for member in stages["members"]]]
    for stage in order:
        if not stage:
            continue
        for file in library["files"]:
            if f"stage:{stage}" in file["scenarios"] and "\n## " not in file["body"]:
                return file["body"]
    return "（当前阶段的操作说明原文：见「阶段的操作说明」一节）"


def main() -> None:
    stages = read_stages()
    library = read_library()
    # The sample guide: the first stage that has one and would not split a block.
    sample_stage = stages["members"][0]["value"] if stages["members"] else ""
    if sample_stage:
        for member in stages["members"]:
            if any(f"stage:{member['value']}" in file["scenarios"] for file in library["files"]):
                sample_stage = member["value"]
                break
    guide = stage_sample_guide(library, stages, sample_stage)

    try:
        if os.environ.get("CHEESE_PROMPT_STATIC"):
            raise _Fallback("CHEESE_PROMPT_STATIC 设了：这次专门走静态解析那条退路")
        module = load_prompt_module()
        samples, unknown = samples_for(module, guide, scenario_skills(library))
        result = run_exec(module, samples)
        result["unknown_params"] = unknown
    except _Fallback as exc:
        result = run_ast(str(exc))
        result["unknown_params"] = []
    except Exception as exc:  # noqa: BLE001 — any failure here means "parse instead"
        result = run_ast(f"{type(exc).__name__}: {exc}")
        result["unknown_params"] = []

    result["source"] = "backend/app/domain/agent/harness/prompt.py"
    result["stages_source"] = "backend/app/domain/agent/stages.py"
    result["stages"] = {
        **stages,
        "members": [
            {
                **member,
                "skills": [
                    {"name": file["name"], "title": file["title"], "file": file["file"], "body": file["body"], "description": file["description"]}
                    for file in library["files"]
                    if f"stage:{member['value']}" in file["scenarios"]
                ],
            }
            for member in stages["members"]
        ],
    }
    result["library"] = library
    json.dump(result, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
