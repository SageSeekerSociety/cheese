#!/usr/bin/env python3
"""Verify paths in an orchestrator brief against the repository and in-flight work.

Usage:
    python3 .claude/scripts/verify-brief-paths.py --brief brief.txt
    python3 .claude/scripts/verify-brief-paths.py --brief brief.txt \
        --brief-path path/to/file.py --new-path path/to/new.py \
        --in-flight-actual path/to/changed.py --in-flight-declared path/to/claimed.py

The brief may be a text file, '-' for stdin, or omitted to read stdin when no
explicit brief paths are given. Paths marked "新建" / "new" are expected not to
exist in HEAD or origin/main; on one line, `新增 N 个文件` covers N list paths.
Otherwise repeat the marker for each new path.
In-flight actual and declared paths may be supplied
repeatedly, or as newline-delimited files with --in-flight-actual-list /
--in-flight-declared-list.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PATH_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_./-])(?:\.\.?[/\\]|/)?"
    r"(?:[A-Za-z0-9_.@+\-]|(?<![㐀-鿿])(?!(?:修改|更新|编辑|创建|删除|新增|新建))[㐀-鿿])"
    r"[A-Za-z0-9_./@+\-㐀-鿿]*"
)
NEW_MARKER = re.compile(
    r"(?<![A-Za-z0-9_./-])(?:新建|新增)(?=[ \t:：`]|[A-Za-z0-9_.-]|[㐀-鿿])"
    r"|(?<![A-Za-z0-9_./-])new(?:[ \t]+file)?(?=[ \t:：`])",
    re.IGNORECASE,
)
FILE_SUFFIXES = frozenset(
    {
        ".a",
        ".avif",
        ".bat",
        ".bmp",
        ".c",
        ".cfg",
        ".cjs",
        ".conf",
        ".cpp",
        ".css",
        ".csv",
        ".cs",
        ".docx",
        ".gif",
        ".go",
        ".gz",
        ".h",
        ".html",
        ".ico",
        ".ipynb",
        ".java",
        ".jpeg",
        ".jpg",
        ".js",
        ".json",
        ".jsx",
        ".kt",
        ".lock",
        ".log",
        ".md",
        ".mdx",
        ".mjs",
        ".mkv",
        ".mov",
        ".mp4",
        ".mts",
        ".parquet",
        ".patch",
        ".pdf",
        ".php",
        ".png",
        ".proto",
        ".py",
        ".pyi",
        ".rb",
        ".rs",
        ".rst",
        ".scss",
        ".sh",
        ".sha256",
        ".sql",
        ".svg",
        ".swift",
        ".tar",
        ".toml",
        ".ts",
        ".tsx",
        ".tsv",
        ".txt",
        ".vue",
        ".wasm",
        ".webm",
        ".webp",
        ".woff2",
        ".xml",
        ".yaml",
        ".yml",
        ".zip",
    }
)
NEW_DESCRIPTORS = frozenset(
    {
        "api", "cli", "file", "files", "helper", "http", "mcp", "module",
        "path", "paths", "文件", "接口", "模块", "路径",
    }
)
LIST_CONNECTORS = frozenset({"and", "和", "与", "以及"})
ROOT_FILENAME_STOPWORDS = frozenset(
    {
        "api", "change", "ci", "cli", "db", "e2e", "feature", "http", "id",
        "mcp", "modify", "pr", "project", "python", "sql", "ts", "ui", "update",
        "url", "user", "ux", "version",
    }
)
ROOT_FILENAME_NAMES = frozenset(
    {"authors", "changelog", "codeowners", "contributing", "copying", "license", "notice", "readme", "security"}
)
ROOT_PATH_REFERENCE = re.compile(
    r"(?:(?i:update|modify|edit|touch|create|remove)\s+|(?:修改|更新|编辑|创建|删除|新增|新建)\s*)"
    r"(?P<path>[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z][A-Za-z0-9_-]*)*)"
    r"(?=$|[^A-Za-z0-9_.-])"
)
ROOT_UNICODE_PATH_REFERENCE = re.compile(
    r"(?:(?i:update|modify|edit|touch|create|remove)\s+|(?:修改|更新|编辑|创建|删除|新增|新建)\s*)"
    r"(?P<path>[㐀-鿿][A-Za-z0-9_.@+\-㐀-鿿]*\.[A-Za-z]{1,10})(?=$|[\s,;，；。！？!?㐀-鿿])"
)


def normalize_path(value: str) -> str | None:
    """Return a safe repository-relative path, rejecting traversal and URLs."""
    value = value.strip().strip("`'\"()[]{}<>,;:。；，")
    if not value or "://" in value or value.startswith("/") or "\\" in value:
        return None
    path = Path(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        return None
    return path.as_posix()


def _is_root_filename(token: str) -> bool:
    lower = token.lower()
    return (
        lower not in ROOT_FILENAME_STOPWORDS
        and (token.isupper() and len(token) > 3 or lower in ROOT_FILENAME_NAMES or lower.endswith("file"))
    )


def _attached_descriptor(token: str, known_paths: set[str]) -> str:
    if token in known_paths:
        return ""
    for word in ("文件", "接口", "模块", "路径"):
        if not token.startswith(word):
            continue
        remainder = token[len(word):]
        if not remainder.isascii() and "/" not in remainder:
            continue
        if (
            remainder
            and remainder[0].isalnum()
            and "/" in remainder
            or _is_root_filename(remainder)
            or Path(remainder).suffix.lower() in FILE_SUFFIXES
        ):
            return word
    return ""


def _is_path_token(token: str, known_paths: set[str]) -> bool:
    if token in known_paths or "/" in token or token.startswith("."):
        return True
    if "@" in token or token.lower() in {"e.g", "i.e"}:
        return False
    if re.fullmatch(r"[vV]?\d+(?:\.\d+)+", token):
        return False
    return Path(token).suffix.lower() in FILE_SUFFIXES


def _trim_trailing_prose(token: str, known_paths: set[str]) -> str:
    if token in known_paths:
        return token
    for path in sorted(known_paths, key=len, reverse=True):
        suffix = token[len(path):] if token.startswith(path) else ""
        if not suffix.startswith(("中的", "里的", "上的", "下的", "内的")):
            continue
        # Prose may continue a known path with a version number or a ratio
        # ("中的第1.2节", "中的v2.0"); a longer real path carries a separator or
        # ends in a file extension such as ".md" or ".7z". Trim only when it
        # does neither, so a nonexistent longer path is never silently
        # rewritten to this prefix. A final dot-segment of digits alone is a
        # version or a ratio, not an extension.
        tail = re.search(r"\.([A-Za-z0-9]{1,10})$", suffix)
        if "/" in suffix or (tail and any(char.isalpha() for char in tail.group(1))):
            continue
        return path
    lowered = token.lower()
    for suffix in sorted(FILE_SUFFIXES, key=len, reverse=True):
        end = lowered.rfind(suffix) + len(suffix)
        if end >= len(suffix) and all(
            not char.isascii() or not (char.isalnum() or char in "_./-")
            for char in token[end:]
        ):
            return token[:end]
    if "/" in token:
        matches = list(re.finditer(r"\.([A-Za-z0-9]{1,10})(?=[㐀-鿿])", token))
        for match in reversed(matches):
            extension = match.group(1)
            if not extension.isdigit() and not re.fullmatch(r"[vV](?:er)?\d+", extension):
                return token[:match.end()]
    return token


def _marker_gap_is_valid(gap: str) -> bool:
    gap = gap.strip(" \t`'\"()[]{}<>:：,，、;；（）【】")
    if not gap or re.fullmatch(
        r"(?:\d+|[零〇一二三四五六七八九十百千万两]+)\s*(?:个|份|项)?"
        r"(?:\s*(?:文件|路径|接口|模块|files?|paths?))?",
        gap,
        re.I,
    ):
        return True
    words = re.findall(r"[A-Za-z]+|[㐀-鿿]+", gap)
    return bool(words) and all(word.lower() in NEW_DESCRIPTORS for word in words) and not re.search(
        r"[^A-Za-z㐀-鿿\s]", gap
    )


def _marker_count(gap: str) -> int | None:
    gap = gap.strip(" \t`'\"()[]{}<>:：,，、;；（）【】")
    match = re.fullmatch(
        r"(?P<count>\d+|[零〇一二三四五六七八九十百千万两]+)\s*(?:个|份|项)"
        r"(?:\s*(?:文件|路径|接口|模块|files?|paths?))?",
        gap,
        re.I,
    )
    if not match:
        return None
    value = match.group("count")
    if value.isdigit():
        return int(value)

    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000}
    if all(char in digits for char in value):
        return int("".join(str(digits[char]) for char in value))

    total = number = 0
    for char in value:
        if char in digits:
            number = digits[char]
        elif char == "万":
            total = (total + number) * 10000
            number = 0
        else:
            total += (number or 1) * units[char]
            number = 0
    return total + number


def _is_list_separator(gap: str) -> bool:
    normalized = gap.strip(" \t`'\"").lower()
    return normalized in {",", "，", "、", ";", "；", "&", "＆", "and", "和", "与", "以及"} or bool(
        re.fullmatch(r"[,，、]\s*(?:and|和|与|以及)", normalized)
    )


def _unicode_path_candidates(line: str, known_paths: set[str]):
    for path in known_paths:
        if path.isascii():
            continue
        start = 0
        while (start := line.find(path, start)) >= 0:
            end = start + len(path)
            before = line[start - 1] if start else ""
            after = line[end] if end < len(line) else ""
            if not (before and before.isascii() and (before.isalnum() or before in "_./-")) and not (
                after and after.isascii() and (after.isalnum() or after in "_./-")
            ):
                yield start, end, path
            start = end


def extract_brief_paths(
    text: str, known_paths: set[str] | None = None
) -> tuple[dict[str, bool], list[str]]:
    """Map paths to whether they are marked new; return unsafe path-like tokens."""
    paths: dict[str, bool] = {}
    unsafe: list[str] = []
    known_paths = known_paths or set()
    for line in text.splitlines():
        markers = list(NEW_MARKER.finditer(line))
        candidates: dict[tuple[int, int, str], None] = {}
        unicode_paths = list(_unicode_path_candidates(line, known_paths))
        ignored_unicode_spans: list[tuple[int, int]] = []
        for marker in markers:
            rest = line[marker.end():]
            start_offset = len(rest) - len(rest.lstrip(" \\t`'\"([{ :："))
            marked = rest[start_offset:]
            match = re.match(r"[^\s,;，；、。！？!?`'\"()\[\]{}<>:：（）【】]+", marked)
            if not match:
                continue
            token = _trim_trailing_prose(match.group().rstrip(".,;:!?"), known_paths)
            descriptor = _attached_descriptor(token, known_paths)
            if descriptor:
                start = marker.start()
                ignored_unicode_spans.append((start, marker.end() + start_offset + len(token)))
                token = token[len(descriptor):]
                token = _trim_trailing_prose(token, known_paths)
                start_offset += len(descriptor)
            if (
                descriptor
                or not token.isascii() and ("/" in token or "." in token)
            ) or _is_root_filename(token) or "file" in marker.group().lower():
                start = marker.end() + start_offset
                if token.isascii():
                    candidates[(start, start + len(token), token)] = None
                else:
                    unicode_paths.append((start, start + len(token), token))
        unicode_spans = [(start, end) for start, end, _ in unicode_paths] + ignored_unicode_spans
        counted_remaining: dict[int, int] = {}
        counted_ends: dict[int, int] = {}
        for match in PATH_TOKEN.finditer(line):
            if any(marker.start() <= match.start() < marker.end() for marker in markers):
                continue
            raw_token = _trim_trailing_prose(match.group().rstrip(".,;:!?"), known_paths)
            raw_end = match.start() + len(raw_token)
            if any(start <= match.start() and raw_end <= span_end for start, span_end in unicode_spans):
                continue
            token = raw_token
            token_start = match.start()
            descriptor = _attached_descriptor(token, known_paths)
            if descriptor:
                ignored_unicode_spans.append((match.start(), raw_end))
                token = token[len(descriptor):]
                token = _trim_trailing_prose(token, known_paths)
                token_start += len(descriptor)
            end = token_start + len(token)
            before = line[token_start - 1] if token_start else ""
            if token.lower() in LIST_CONNECTORS and token not in known_paths and any(
                remaining and _is_list_separator(line[counted_ends[marker_end]:token_start])
                for marker_end, remaining in counted_remaining.items()
            ):
                continue
            if token.endswith(("/", "\\")):
                continue
            marker_gaps = [
                (marker, line[marker.end():token_start])
                for marker in markers
                if marker.end() < token_start
                and line[marker.end():token_start].strip(" \t`'\"()[]{}<>:：,，、;；（）【】")
                and _marker_gap_is_valid(line[marker.end():token_start])
            ]
            pathlike = (
                _is_path_token(token, known_paths)
                or "/" in token
                or Path(token).suffix
                or _is_root_filename(token)
            )
            marker_applies = bool(marker_gaps) and pathlike and (
                token.lower() not in NEW_DESCRIPTORS
                and not re.fullmatch(r"\d+", token)
            )
            counted_list_applies = False
            if not marker_applies:
                for marker_end, remaining in counted_remaining.items():
                    previous_end = counted_ends[marker_end]
                    if remaining and _is_list_separator(line[previous_end:token_start]):
                        counted_list_applies = True
                        counted_remaining[marker_end] = remaining - 1
                        counted_ends[marker_end] = end
                        break
            if not token.isascii() and token not in known_paths and not (
                _is_path_token(token, known_paths)
                or marker_applies
                or counted_list_applies
            ):
                continue
            if "/" not in token and before and not before.isascii() and not (
                descriptor or marker_applies or counted_list_applies
            ):
                continue
            if _is_path_token(token, known_paths) or marker_applies or counted_list_applies:
                candidates[(token_start, end, token)] = None
                if marker_applies:
                    marker, gap = marker_gaps[-1]
                    count = _marker_count(gap)
                    if count is not None:
                        counted_remaining[marker.end()] = max(0, count - 1)
                        counted_ends[marker.end()] = end
        for start, end, path in unicode_paths:
            candidates[(start, end, path)] = None
        for match in ROOT_PATH_REFERENCE.finditer(line):
            token = match.group("path")
            if _is_root_filename(token) or Path(token).suffix.lower() in FILE_SUFFIXES:
                candidates[(match.start("path"), match.end("path"), token)] = None
        for match in ROOT_UNICODE_PATH_REFERENCE.finditer(line):
            start, end = match.span("path")
            if any(span_start <= start and end <= span_end for span_start, span_end in ignored_unicode_spans):
                continue
            token = match.group("path")
            candidates[(start, end, token)] = None
        ordered = sorted(candidates)
        descriptor_words = {"文件", "接口", "模块", "路径"}
        shadowed_paths = {
            (start, end, token)
            for start, end, token in ordered
            if any(
                other_start < start
                and other_end == end
                and line[other_start:start] in descriptor_words
                for other_start, other_end, _ in ordered
            )
        }
        for candidate in shadowed_paths:
            candidates.pop(candidate, None)
        ordered = sorted(candidates)
        counted_remaining: dict[int, int] = {}
        counted_ends: dict[int, int] = {}
        for start, end, token in ordered:
            is_new = False
            for marker in markers:
                if marker.end() <= start:
                    gap = line[marker.end():start]
                    if _marker_gap_is_valid(gap):
                        is_new = True
                        count = _marker_count(gap)
                        if count is not None:
                            counted_remaining[marker.end()] = max(0, count - 1)
                            counted_ends[marker.end()] = end
            if not is_new:
                for marker_end, remaining in counted_remaining.items():
                    previous_end = counted_ends[marker_end]
                    if remaining and _is_list_separator(line[previous_end:start]):
                        is_new = True
                        counted_remaining[marker_end] = remaining - 1
                        counted_ends[marker_end] = end
                        break
            path = normalize_path(token)
            if path is None:
                unsafe.append(token)
            else:
                paths[path] = paths.get(path, False) or is_new
    return paths, unsafe


def read_list_file(path: str) -> list[str]:
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def git_tree_paths(root: Path, refs: tuple[str, ...]) -> set[str]:
    paths: set[str] = set()
    for ref in refs:
        result = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "-z", ref],
            cwd=root,
            capture_output=True,
        )
        if result.returncode:
            raise RuntimeError(f"required tree is unavailable: {ref}")
        paths.update(
            path.decode("utf-8", errors="surrogateescape")
            for path in result.stdout.split(b"\0")
            if path
        )
    return paths


def git_path_exists(root: Path, ref: str, path: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}:{path}"], cwd=root, capture_output=True
    )
    if result.returncode not in (0, 1, 128):
        raise RuntimeError(f"git could not inspect {ref}:{path}")
    return result.returncode == 0


def check(
    root: Path,
    brief_paths: dict[str, bool],
    unsafe: list[str],
    in_flight: set[str],
    refs: tuple[str, ...] = ("HEAD", "origin/main"),
) -> list[str]:
    for ref in refs:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{tree}}"],
            cwd=root,
            capture_output=True,
        )
        if result.returncode:
            raise RuntimeError(f"required tree is unavailable: {ref}")
    errors = [f"invalid or unsafe path in brief: {path}" for path in unsafe]
    for path, is_new in sorted(brief_paths.items()):
        present = [ref for ref in refs if git_path_exists(root, ref, path)]
        if is_new and present:
            errors.append(f"marked new but already exists in {' and '.join(present)}: {path}")
        elif not is_new and not present:
            errors.append(f"path does not exist in HEAD or origin/main: {path}")
        if path in in_flight:
            errors.append(f"overlaps in-flight PR actual changes or declared files: {path}")
    return errors


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--brief", help="brief text file, or '-' for stdin (default: stdin)")
    result.add_argument("--brief-path", action="append", default=[], help="explicit path in the brief; repeatable")
    result.add_argument("--new-path", action="append", default=[], help="explicit path marked as new; repeatable")
    result.add_argument("--in-flight-actual", action="append", default=[], help="path changed by an in-flight PR; repeatable")
    result.add_argument("--in-flight-declared", action="append", default=[], help="path declared by in-flight work; repeatable")
    result.add_argument("--in-flight-actual-list", action="append", default=[], help="newline-delimited list of actual PR changes")
    result.add_argument("--in-flight-declared-list", action="append", default=[], help="newline-delimited list of declared files")
    result.add_argument("--root", type=Path, default=REPO_ROOT, help="repository root (default: detected from script location)")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        root = args.root.resolve()
        list_paths = args.in_flight_actual_list + args.in_flight_declared_list
        brief_uses_stdin = args.brief == "-" or (
            args.brief is None and not (args.brief_path or args.new_path)
        )
        if brief_uses_stdin and "-" in list_paths:
            raise ValueError("stdin cannot be used for both the brief and an in-flight list")
        if brief_uses_stdin:
            brief = sys.stdin.read()
        elif args.brief is None:
            brief = ""
        else:
            brief = Path(args.brief).read_text(encoding="utf-8")

        in_flight_values = list(args.in_flight_actual) + list(args.in_flight_declared)
        for list_path in list_paths:
            in_flight_values.extend(read_list_file(list_path))
        in_flight: set[str] = set()
        for value in in_flight_values:
            path = normalize_path(value)
            if path is None:
                raise ValueError(f"invalid in-flight path: {value}")
            in_flight.add(path)

        known_paths = git_tree_paths(root, ("HEAD", "origin/main")) | in_flight
        brief_paths, unsafe = extract_brief_paths(brief, known_paths)
        for value in args.brief_path:
            path = normalize_path(value)
            if path is None:
                unsafe.append(value)
            else:
                brief_paths.setdefault(path, False)
        for value in args.new_path:
            path = normalize_path(value)
            if path is None:
                unsafe.append(value)
            else:
                brief_paths[path] = True

        errors = check(root, brief_paths, unsafe, in_flight)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"cannot verify brief paths: {exc}", file=sys.stderr)
        return 2

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"OK: verified {len(brief_paths)} brief path(s); no in-flight overlap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
