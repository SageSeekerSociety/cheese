#!/usr/bin/env python3
"""check-get-by-ids-sibling.py — a single-row get_by_id comes with its batch read.

WHY THIS EXISTS. 2026-10-18, #1923 item 4. A line that needs N rows greps for a
batch reader, finds only ``get_by_id``, and writes a loop of single reads: the
N+1 starts there. #4341 gave ``app/domain/reads.py`` the shared ``get_by_ids``
primitive and settled the name; its docstring states the pairing as a
convention. This guard is what makes the convention hold for a new repository.

WHAT IT JUDGES. In ``backend/app``, every class that defines
``async def get_by_id(self, ...) -> X | None`` (or ``Optional[X]``) must define
``get_by_ids`` in the same class body. The unit is the class, not the file: a
file often holds two repositories, and a sibling on one must not cover for the
other. A violation names the file and the class.

BASELINE. 2026-10-18, origin/main 96945ae7 had 17 such classes without the
sibling; they are frozen in ``BASELINE``, one ``path::Class`` per line. Both
directions fail: a new class missing the sibling, and a baseline entry that no
longer matches the tree (fixed, renamed or deleted) — a stale entry is room for
the next violation to hide in, so it has to be removed when it is fixed.

Usage: check-get-by-ids-sibling.py [--root DIR] [--baseline FILE] [--json]
       check-get-by-ids-sibling.py --self-test
Exit 0 the tree matches the baseline, 1 a new violation or a stale baseline
     entry, 2 a file could not be parsed or the root is missing.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIR = Path("backend") / "app"

#: Classes on main with a single-row get_by_id and no get_by_ids. Only shrinks:
#: add the sibling (on ``app.domain.reads.get_by_ids``), then delete the line.
BASELINE: tuple[str, ...] = (
    "backend/app/domain/answers/repositories.py::AnswerRepository",
    "backend/app/domain/discussion/repositories.py::DiscussionRepository",
    "backend/app/domain/discussion/repositories.py::ReactionTypeRepository",
    "backend/app/domain/groups/repositories.py::GroupTargetRepository",
    "backend/app/domain/materials/repositories.py::MaterialBundleRepository",
    "backend/app/domain/memory/files_store.py::MemoryFileStore",
    "backend/app/domain/questions/queries.py::QuestionQueries",
    "backend/app/domain/questions/repositories.py::QuestionInvitationRepository",
    "backend/app/domain/space/group_repositories.py::SpaceDomainGroupRepository",
    "backend/app/domain/space/repositories.py::SpaceCategoryRepository",
    "backend/app/domain/space/repositories.py::SpaceRepository",
    "backend/app/domain/task/submission_repositories.py::TaskSubmissionRepository",
    "backend/app/domain/team/recruitment_repositories.py::RecruitmentRepository",
    "backend/app/domain/team/recruitment_services.py::RecruitmentService",
    "backend/app/domain/team/repositories.py::TeamMembershipApplicationRepository",
)


class CannotJudge(Exception):
    """A file under the scan root could not be read or parsed."""


def _returns_optional(node: ast.expr | None) -> bool:
    """``X | None``, ``None | X`` or ``Optional[X]``, as written or quoted."""
    if node is None:
        return False
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return False
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return any(
            isinstance(side, ast.Constant) and side.value is None
            for side in (node.left, node.right)
        )
    if isinstance(node, ast.Subscript):
        target = node.value
        name = (
            target.attr
            if isinstance(target, ast.Attribute)
            else getattr(target, "id", "")
        )
        return name == "Optional"
    return False


def _is_single_read(node: ast.stmt) -> bool:
    if not isinstance(node, ast.AsyncFunctionDef) or node.name != "get_by_id":
        return False
    params = node.args.posonlyargs + node.args.args
    return bool(params) and params[0].arg == "self" and _returns_optional(node.returns)


def _has_sibling(cls: ast.ClassDef) -> bool:
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "get_by_ids"
        for node in cls.body
    )


def scan(root: Path) -> list[str]:
    """``path::Class`` for each class under ``root/backend/app`` missing the sibling."""
    base = root / SCAN_DIR
    if not base.is_dir():
        raise CannotJudge(f"{base} 不是目录")
    found: list[str] = []
    for path in sorted(base.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            raise CannotJudge(f"{path} 解析失败: {exc}") from exc
        relative = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if any(_is_single_read(item) for item in node.body) and not _has_sibling(
                node
            ):
                found.append(f"{relative}::{node.name}")
    return found


def compare(found: list[str], baseline: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """(new violations, stale baseline entries)."""
    frozen = set(baseline)
    present = set(found)
    return sorted(present - frozen), sorted(frozen - present)


def load_baseline(path: Path | None) -> tuple[str, ...]:
    if path is None:
        return BASELINE
    lines = path.read_text(encoding="utf-8").splitlines()
    return tuple(line.strip() for line in lines if line.strip())


def judge(root: Path, baseline: tuple[str, ...]) -> dict:
    found = scan(root)
    new, stale = compare(found, baseline)
    return {"found": found, "new": new, "stale": stale, "frozen": len(baseline)}


# --- self-test -------------------------------------------------------------

_COMPLIANT = """
class TagRepository:
    async def get_by_id(self, tag_id: int) -> Tag | None:
        ...

    async def get_by_ids(self, tag_ids):
        ...
"""

_MISSING = """
class TagRepository:
    async def get_by_id(self, tag_id: int) -> Tag | None:
        ...

    async def get_by_ids(self, tag_ids):
        ...


class LabelRepository:
    async def get_by_id(self, label_id: int) -> Optional[Label]:
        ...
"""

_NOT_A_SINGLE_READ = """
class Service:
    async def get_by_id(self, item_id: int) -> Item:
        ...


def get_by_id(session, item_id) -> Item | None:
    ...


class Quoted:
    async def get_by_id(self, item_id: int) -> "Item | None":
        ...
"""


def _self_test() -> int:
    """Run the judgement on synthetic trees; each case says what it must give."""
    cases = (
        # (name, file contents, baseline, expected new, expected stale)
        ("合规：有 get_by_ids → 不报", _COMPLIANT, (), [], []),
        (
            "同文件另一个类缺兄弟 → 报出那个类",
            _MISSING,
            (),
            ["backend/app/domain/x/repositories.py::LabelRepository"],
            [],
        ),
        (
            "缺兄弟但在 baseline → 不报",
            _MISSING,
            ("backend/app/domain/x/repositories.py::LabelRepository",),
            [],
            [],
        ),
        (
            "baseline 里的类已补上 → 报 stale",
            _COMPLIANT,
            ("backend/app/domain/x/repositories.py::TagRepository",),
            [],
            ["backend/app/domain/x/repositories.py::TagRepository"],
        ),
        (
            "非 Optional 返回与模块级函数不算；引号注解照判",
            _NOT_A_SINGLE_READ,
            (),
            ["backend/app/domain/x/repositories.py::Quoted"],
            [],
        ),
    )
    failures = 0
    ran = 0
    for name, source, baseline, want_new, want_stale in cases:
        ran += 1
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / SCAN_DIR / "domain" / "x" / "repositories.py"
            target.parent.mkdir(parents=True)
            target.write_text(source, encoding="utf-8")
            result = judge(root, baseline)
        if result["new"] != want_new or result["stale"] != want_stale:
            failures += 1
            print(
                f"自测「{name}」判错了：new={result['new']} stale={result['stale']}，"
                f"应为 new={want_new} stale={want_stale}",
                file=sys.stderr,
            )
    ran += 1
    with tempfile.TemporaryDirectory() as tmp:
        broken = Path(tmp) / SCAN_DIR / "broken.py"
        broken.parent.mkdir(parents=True)
        broken.write_text("class (:\n", encoding="utf-8")
        try:
            scan(Path(tmp))
        except CannotJudge:
            pass
        else:
            failures += 1
            print(
                "自测「解析失败 → 判不了」判错了：没有抛 CannotJudge", file=sys.stderr
            )
    if failures:
        return 1
    print(f"get_by_ids 兄弟守卫自测通过（{ran} 个场景）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=REPO_ROOT, help="仓库根，默认本仓库"
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        help="一行一个 path::Class；默认用内置 BASELINE",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--self-test", action="store_true", help="跑内置用例")
    args = parser.parse_args()
    if args.self_test:
        return _self_test()

    try:
        result = judge(args.root.resolve(), load_baseline(args.baseline))
    except (CannotJudge, OSError) as exc:
        print(f"get_by_ids 兄弟守卫判不了: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for key in result["new"]:
            path, cls = key.split("::")
            print(
                f"{path}: {cls} 有单条 get_by_id 但没有 get_by_ids。"
                "在同一个类里补上 get_by_ids(ids) -> dict[id, row]，"
                "用 app.domain.reads.get_by_ids，过滤条件与 get_by_id 一致。",
                file=sys.stderr,
            )
        for key in result["stale"]:
            print(
                f"BASELINE 里的 {key} 已不再缺 get_by_ids（补上了、改名或删了），"
                "从 .claude/scripts/check-get-by-ids-sibling.py 的 BASELINE 删掉这一行。",
                file=sys.stderr,
            )
        if not result["new"] and not result["stale"]:
            print(
                f"get_by_ids 兄弟守卫通过（缺兄弟的类 {len(result['found'])} 个，"
                f"全部在 BASELINE 里，冻结 {result['frozen']} 个）"
            )
    return 1 if result["new"] or result["stale"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
