#!/usr/bin/env python3
"""把一份 Figma 稿读成结构，或把画板导成图。

只用标准库：哪台机器上都直接 `python3 scripts/figma.py …` 跑，不需要 uv、不需要装包。
token 从环境变量 `FIGMA_TOKEN` 读（用户在 项目设置 → 环境变量 里加一条），Figma 侧在
Settings → Security → Personal access tokens 生成。

    python3 scripts/figma.py file   <网址> [--depth 2]
    python3 scripts/figma.py node   <网址> [--node 12:34] [--depth 2]
    python3 scripts/figma.py export <网址> [--node 12:34] [--format png] [--out 稿.png]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.figma.com/v1"

#: 网址里出现在 file key 前面那一段。`figma.com/file/`、`/design/`、`/board/` 都用过。
MARKERS = ("design", "file", "board", "proto", "slides")

NO_TOKEN = (
    "[figma] 没有 FIGMA_TOKEN。让用户在 项目设置 → 环境变量 里加一条 "
    "FIGMA_TOKEN=<Figma personal access token>（Figma：Settings → Security → "
    "Personal access tokens），再重开会话。"
)


def token() -> str:
    value = os.environ.get("FIGMA_TOKEN", "").strip()
    if not value:
        print(NO_TOKEN, file=sys.stderr)
        sys.exit(2)
    return value


def call(path: str) -> dict:
    request = urllib.request.Request(API + path, headers={"X-Figma-Token": token()})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        print(f"[figma] Figma 回了 {exc.code}：{detail}", file=sys.stderr)
    except urllib.error.URLError as exc:
        print(f"[figma] 连不上 api.figma.com：{exc.reason}", file=sys.stderr)
    sys.exit(1)


def target(value: str) -> tuple[str, str]:
    """网址或裸 file key → (key, 节点 id)；`node-id=12-34` 归一成 API 的 `12:34`。"""
    if "/" not in value:
        return value, ""
    parsed = urllib.parse.urlparse(value if "//" in value else "https://" + value)
    parts = [part for part in parsed.path.split("/") if part]
    key = next(
        (
            parts[i + 1]
            for i, part in enumerate(parts)
            if part in MARKERS and i + 1 < len(parts)
        ),
        parts[-1] if parts else "",
    )
    node = urllib.parse.parse_qs(parsed.query).get("node-id", [""])[0]
    return key, node.replace("-", ":")


def name_of(node: dict) -> str:
    return " ".join(str(node.get("name") or "?").split())[:60]


def size_of(node: dict) -> str:
    box = node.get("absoluteBoundingBox") or {}
    width, height = box.get("width"), box.get("height")
    return f" {round(width)}×{round(height)}" if width and height else ""


def color(paint: dict) -> str:
    channels = paint.get("color") or {}
    red, green, blue = (round(255 * channels.get(key, 0)) for key in "rgb")
    return f"#{red:02x}{green:02x}{blue:02x}"


def show(node: dict, indent: int, depth: int) -> None:
    pad = "  " * indent
    head = f"{name_of(node)} [{node.get('type')} {node.get('id')}]{size_of(node)}"
    print(f"{pad}- {head}")
    if node.get("type") == "TEXT":
        style = node.get("style") or {}
        text = " ".join(str(node.get("characters") or "").split())[:120]
        font = f"{style.get('fontFamily')} {style.get('fontSize')}px"
        print(f"{pad}  文字 {font}：{text}")
    for paint in node.get("fills") or []:
        if paint.get("type") == "SOLID" and paint.get("visible", True):
            print(f"{pad}  填充 {color(paint)}")
    if depth > 0:
        for child in node.get("children") or []:
            show(child, indent + 1, depth - 1)


def asked_node(args: argparse.Namespace) -> tuple[str, str]:
    key, from_url = target(args.url)
    node = args.node or from_url
    if not key or not node:
        print("[figma] 拿不到画板 id：网址里没有就得给 --node。", file=sys.stderr)
        sys.exit(2)
    return key, node


def cmd_file(args: argparse.Namespace) -> None:
    key, _ = target(args.url)
    document = call(f"/files/{key}?depth={args.depth}")["document"]
    for page in document.get("children") or []:
        show(page, 0, 1)


def cmd_node(args: argparse.Namespace) -> None:
    key, node = asked_node(args)
    found = call(f"/files/{key}/nodes?ids={urllib.parse.quote(node)}")["nodes"]
    for entry in found.values():
        show(entry["document"], 0, args.depth)


def cmd_export(args: argparse.Namespace) -> None:
    key, node = asked_node(args)
    path = f"/images/{key}?ids={urllib.parse.quote(node)}&format={args.format}"
    url = next(iter(call(path + "&scale=2")["images"].values()), None)
    if not url:
        print("[figma] 这块画板导不出来（类型不支持，或节点是空的）。", file=sys.stderr)
        sys.exit(1)
    out = args.out or f"figma-{node.replace(':', '-')}.{args.format}"
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read()
    with open(out, "wb") as handle:
        handle.write(data)
    print(f"已导出 {out}（{len(data) // 1024} KB）。用 Read 打开看一眼再动手。")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)

    p_file = subs.add_parser("file", help="结构：各页面下的顶层画板、名字、id、尺寸")
    p_file.add_argument("url", help="figma.com 的网址，或裸 file key")
    p_file.add_argument("--depth", type=int, default=2, help="往下画几层（默认 2）")
    p_file.set_defaults(run=cmd_file)

    p_node = subs.add_parser("node", help="细节：类型、尺寸、填充色、文字与字号")
    p_node.add_argument("url")
    p_node.add_argument("--node", default="", help="节点 id（如 12:34）")
    p_node.add_argument("--depth", type=int, default=2)
    p_node.set_defaults(run=cmd_node)

    p_export = subs.add_parser("export", help="把一块画板导成图，落到本地")
    p_export.add_argument("url")
    p_export.add_argument("--node", default="")
    p_export.add_argument("--format", choices=("png", "svg"), default="png")
    p_export.add_argument("--out", default="", help="存到哪（默认 figma-<id>.<格式>）")
    p_export.set_defaults(run=cmd_export)

    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
