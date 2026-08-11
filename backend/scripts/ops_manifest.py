#!/usr/bin/env python3
"""操作请求清单的校验 / 生成 / PR 守卫 —— CLI.

一处逻辑两处用：本地写清单时用它当场校验（不要等到执行才发现格式不对），
CI 用同一个入口做必需检查。

    # 生成 resolved: 段（registry 是权威，人不要手写）
    uv run python scripts/ops_manifest.py render ../ops/requests/<topic8>-<op>.yaml

    # 校验若干清单
    uv run python scripts/ops_manifest.py check ../ops/requests/*.yaml

    # 这个 PR 改的文件合法吗（ops PR 只能碰 ops/requests/）
    uv run python scripts/ops_manifest.py guard ops/requests/a.yaml backend/app/x.py

    # registry 里有哪些 operation
    uv run python scripts/ops_manifest.py describe

只依赖 pydantic + pyyaml，不碰数据库、不 import FastAPI，所以 CI 里可以用
`uv run --no-project --with pydantic --with pyyaml` 起，几秒钟跑完。
它**不执行任何操作**，只读文件。
"""

import argparse
import json
import sys
from pathlib import Path, PurePosixPath

# Run from anywhere (repo root in CI, backend/ locally) without PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.ops.guard import guard_issues  # noqa: E402
from app.domain.ops.manifest import (  # noqa: E402
    REQUESTS_DIR,
    ManifestError,
    render_manifest,
    validate_manifest,
)
from app.domain.ops.registry import describe_registry  # noqa: E402


def logical_path(raw: str) -> str:
    """Repo-relative posix path of a manifest, whatever cwd the caller used.

    The manifest's own rules (filename must match topic_id/operation_id, must sit
    directly under ops/requests/) are checked against this, so `check
    ../ops/requests/x.yaml` from backend/ and `check ops/requests/x.yaml` from the
    repo root must agree.
    """
    parts = PurePosixPath(Path(raw).as_posix()).parts
    for index in range(len(parts) - 1):
        if parts[index] == "ops" and parts[index + 1] == "requests":
            return "/".join(parts[index:])
    return PurePosixPath(raw).as_posix()


def _report(error: ManifestError) -> None:
    print(f"✗ {error.path}", file=sys.stderr)
    for issue in error.issues:
        print(f"    - {issue}", file=sys.stderr)


def cmd_check(paths: list[str]) -> int:
    failed = 0
    for raw in paths:
        text = Path(raw).read_text(encoding="utf-8")
        try:
            validated = validate_manifest(logical_path(raw), text)
        except ManifestError as exc:
            _report(exc)
            failed += 1
            continue
        face = validated.resolved
        print(
            f"✓ {validated.path}  {validated.operation_id}"
            f"  blast_radius={face.blast_radius.value}"
            f"  authorization={validated.request.authorization}"
        )
    if failed:
        print(f"\n{failed} 份清单没通过校验。", file=sys.stderr)
    return 1 if failed else 0


def cmd_render(paths: list[str], to_stdout: bool) -> int:
    failed = 0
    for raw in paths:
        path = Path(raw)
        try:
            rendered = render_manifest(logical_path(raw), path.read_text("utf-8"))
        except ManifestError as exc:
            _report(exc)
            failed += 1
            continue
        if to_stdout:
            print(rendered, end="")
        else:
            path.write_text(rendered, encoding="utf-8")
            print(f"✓ 已重算 resolved: {logical_path(raw)}")
    return 1 if failed else 0


def cmd_guard(paths: list[str]) -> int:
    issues = guard_issues(paths)
    if not issues:
        return 0
    print("✗ 操作请求 PR 的文件范围不合法：", file=sys.stderr)
    for issue in issues:
        print(f"    - {issue}", file=sys.stderr)
    return 1


def cmd_describe() -> int:
    print(json.dumps(describe_registry(), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help=f"校验 {REQUESTS_DIR}/ 下的清单")
    check.add_argument("paths", nargs="+")

    render = sub.add_parser("render", help="按 registry 重算 resolved: 段")
    render.add_argument("paths", nargs="+")
    render.add_argument("--stdout", action="store_true", help="打印而不是写回文件")

    guard = sub.add_parser("guard", help="检查一个 PR 改的文件范围")
    guard.add_argument("paths", nargs="*")

    sub.add_parser("describe", help="打印 registry")

    args = parser.parse_args(argv)
    if args.command == "check":
        return cmd_check(args.paths)
    if args.command == "render":
        return cmd_render(args.paths, args.stdout)
    if args.command == "guard":
        return cmd_guard(args.paths)
    return cmd_describe()


if __name__ == "__main__":
    raise SystemExit(main())
