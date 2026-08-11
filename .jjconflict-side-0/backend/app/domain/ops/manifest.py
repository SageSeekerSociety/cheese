"""Operation-request manifest — the file that IS the authorization diff.

GitHub refuses to open a PR with no commits (422 No commits between), so "the PR
is the authorization envelope" cannot mean "open an empty PR and put the
authorization in it". The manifest closes that gap: the request is written as a
file, **the file is the diff**, and the PR carries it.

    ops/requests/<topic8>-<operation_id>.yaml

Two rules make the format worth trusting, and both are enforced here rather than
at execution time (a format only gets safer if it is strict from the first day):

1. **No implicit "current".** `commit_id` on a PR pins only "the PR head has not
   moved" — it says nothing about main moving underneath. So every target must
   be written as a concrete sha / concrete version, and values like `main`,
   `latest`, `当前` are rejected by the schema, not by review.
2. **The registry is the authority.** The seven answers a human reads on the
   card are recomputed from `registry` + `args`; a manifest whose committed
   `resolved:` block disagrees is invalid. An author cannot talk their way into
   a smaller blast radius.

Nothing here executes anything.
"""

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from app.domain.ops.registry import (
    BlastRadius,
    Handle,
    OperationArgs,
    Resolved,
    UnknownOperationError,
    get_operation,
)

REQUESTS_DIR = "ops/requests"
SCHEMA_VERSION = 1

FILENAME_RE = re.compile(
    r"^(?P<topic8>[0-9a-f]{8})-(?P<operation_id>[a-z][a-z0-9_.]*)$"
)

# Values that mean "whatever this points at when it runs". They are the whole
# reason rule 1 exists: a request that says `main` is a request whose target can
# change between the human reading it and the machine running it.
_IMPLICIT_VALUES = frozenset(
    {
        "current",
        "latest",
        "newest",
        "head",
        "now",
        "tip",
        "main",
        "master",
        "trunk",
    }
)
_IMPLICIT_PATTERN = re.compile(
    r"^@?(current|latest|newest|head|now|tip)\b|当前|最新|最近", re.IGNORECASE
)

_HEADER = """\
# 操作请求（operation request）。这份文件本身就是授权 diff：
#   人在 PR 上批准 = 授权这一次执行。改了 args 就要重新批。
# `resolved:` 一段由 registry 生成，请勿手改 —— CI 会重算并比对。
#   重新生成：uv run python scripts/ops_manifest.py render <本文件>
"""


class ManifestError(ValueError):
    """One manifest, one or more problems. Carries every problem, not the first
    — a round trip per mistake is exactly what "校验在提交时" is meant to avoid."""

    def __init__(self, path: str, issues: Sequence[str]) -> None:
        self.path = path
        self.issues = list(issues)
        detail = "\n".join(f"  - {issue}" for issue in self.issues)
        super().__init__(f"{path}:\n{detail}")


class OperationRequest(BaseModel):
    """The author-written part of a manifest, plus the generated `resolved:`."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    operation_id: str
    topic_id: str = Field(pattern=r"^[0-9a-f]{8}$")
    """Short (8-hex) id of the topic asking for it — matches the filename."""

    requested_by: Handle
    """Who is asking. NOT who authorizes: authorization happens on the PR, with
    the approver's own GitHub identity."""

    reason: str = Field(min_length=1, max_length=300)
    """为什么现在要做. The one genuinely free-text field, and deliberately short."""

    authorization: Literal["once", "envelope"] = "once"
    """拍板 6: `envelope` (a long-lived authorization PR) is only open to
    operations whose blast radius is `none`. Everything with a side effect is
    `once` — one PR, one approve, closed after it runs."""

    args: dict[str, JsonValue] = Field(default_factory=dict)
    resolved: Resolved | None = None


@dataclass(frozen=True)
class ValidatedRequest:
    path: str
    request: OperationRequest
    args: OperationArgs
    resolved: Resolved

    @property
    def operation_id(self) -> str:
        return self.request.operation_id

    def card_face(self) -> dict[str, JsonValue]:
        """The 七问, as the card shows them. `operation_id` + `args` are what the
        author asked for; the rest is what the registry says that means."""
        return {
            "path": self.path,
            "operation_id": self.operation_id,
            "topic_id": self.request.topic_id,
            "requested_by": self.request.requested_by,
            "reason": self.request.reason,
            "authorization": self.request.authorization,
            "args": dict(self.request.args),
            "what": self.resolved.what,
            "where": self.resolved.where,
            "blast_radius": self.resolved.blast_radius.value,
            "reversibility": self.resolved.reversibility.value,
            "reversal": self.resolved.reversal,
            "worst_case": self.resolved.worst_case,
            "interruptible": self.resolved.interruptible,
            "human_approvals_required": self.resolved.human_approvals_required,
        }


def _walk_strings(value: JsonValue, prefix: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield prefix, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_strings(item, f"{prefix}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_strings(item, f"{prefix}[{index}]")


def implicit_target_issues(args: Mapping[str, JsonValue]) -> list[str]:
    """Reject args that name a moving target instead of a pinned one."""
    issues: list[str] = []
    for field, raw in args.items():
        for path, text in _walk_strings(raw, f"args.{field}"):
            stripped = text.strip()
            if stripped.lower() in _IMPLICIT_VALUES or _IMPLICIT_PATTERN.search(
                stripped
            ):
                issues.append(
                    f"{path}: 不允许隐含的「当前」目标（收到 {stripped!r}）——"
                    "写成具体 sha 或具体版本号。commit_id 只锁得住 PR 的 head 没变，"
                    "锁不住 main 变了。"
                )
    return issues


def _filename_issues(path: str, request: OperationRequest) -> list[str]:
    posix = PurePosixPath(path)
    issues: list[str] = []
    parent = posix.parent.as_posix()
    if parent != REQUESTS_DIR:
        issues.append(
            f"清单必须放在 {REQUESTS_DIR}/ 下（当前 {parent or '.'}/），且不分子目录"
        )
    if posix.suffix != ".yaml":
        issues.append("清单扩展名必须是 .yaml")
        return issues
    match = FILENAME_RE.match(posix.name.removesuffix(".yaml"))
    if match is None:
        issues.append("文件名必须是 <topic8>-<operation_id>.yaml")
        return issues
    if match.group("topic8") != request.topic_id:
        issues.append(
            f"文件名里的 topic8 {match.group('topic8')!r} 与 "
            f"topic_id {request.topic_id!r} 不一致"
        )
    if match.group("operation_id") != request.operation_id:
        issues.append(
            f"文件名里的 operation_id {match.group('operation_id')!r} 与 "
            f"operation_id {request.operation_id!r} 不一致"
        )
    return issues


def _pydantic_issues(error: ValidationError) -> list[str]:
    return [
        f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
        for err in error.errors()
    ]


def _envelope_issues(request: OperationRequest, resolved: Resolved) -> list[str]:
    """拍板 6: an envelope may only carry operations it can contain."""
    if request.authorization != "envelope" or resolved.blast_radius.envelope_eligible:
        return []
    return [
        f"authorization: envelope 只允许 blast_radius=none 的操作，"
        f"{request.operation_id} 是 {resolved.blast_radius.value}。"
        "有副作用的操作一律一次性 PR、执行完即关"
        "（拍板 6：每次执行都要一次新的 approve）。"
    ]


def parse_manifest(path: str, text: str) -> OperationRequest:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:  # pragma: no cover - message varies by libyaml
        raise ManifestError(path, [f"YAML 解析失败：{exc}"]) from None
    if not isinstance(raw, dict):
        raise ManifestError(path, ["清单顶层必须是一个 mapping"])
    try:
        return OperationRequest.model_validate(raw)
    except ValidationError as exc:
        raise ManifestError(path, _pydantic_issues(exc)) from None


def validate_manifest(path: str, text: str) -> ValidatedRequest:
    """Full check of one manifest. Raises ManifestError listing every problem."""
    request = parse_manifest(path, text)
    issues = _filename_issues(path, request)

    try:
        operation = get_operation(request.operation_id)
    except UnknownOperationError as exc:
        raise ManifestError(path, [*issues, str(exc)]) from None

    issues += implicit_target_issues(request.args)

    try:
        args = operation.resolve_args(request.args)
    except ValidationError as exc:
        raise ManifestError(path, [*issues, *_pydantic_issues(exc)]) from None

    resolved = operation.resolve(request.args)

    if request.resolved is None:
        issues.append(
            "缺少 resolved: 段（由 registry 生成）——跑 "
            "`uv run python scripts/ops_manifest.py render <文件>` 生成"
        )
    elif request.resolved != resolved:
        for field, expected in resolved.model_dump(mode="json").items():
            actual = request.resolved.model_dump(mode="json")[field]
            if actual != expected:
                issues.append(
                    f"resolved.{field} 与 registry 推导结果不符："
                    f"文件里是 {actual!r}，registry 说是 {expected!r}。"
                    "registry 是权威，不要手改 resolved:。"
                )

    issues += _envelope_issues(request, resolved)

    if issues:
        raise ManifestError(path, issues)
    return ValidatedRequest(path=path, request=request, args=args, resolved=resolved)


def render_manifest(path: str, text: str) -> str:
    """Recompute `resolved:` from the registry and return the full document.

    This is how an author fills the generated block: they never write it. Author
    fields are validated first, so `render` cannot launder a bad manifest into a
    well-formed one.
    """
    request = parse_manifest(path, text)
    issues = _filename_issues(path, request)
    try:
        operation = get_operation(request.operation_id)
    except UnknownOperationError as exc:
        raise ManifestError(path, [*issues, str(exc)]) from None
    issues += implicit_target_issues(request.args)
    try:
        operation.resolve_args(request.args)
    except ValidationError as exc:
        raise ManifestError(path, [*issues, *_pydantic_issues(exc)]) from None
    resolved = operation.resolve(request.args)
    issues += _envelope_issues(request, resolved)
    if issues:
        raise ManifestError(path, issues)

    document = request.model_dump(mode="json", exclude={"resolved"})
    document["resolved"] = resolved.model_dump(mode="json")
    body = yaml.safe_dump(
        document,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        # One answer per line. The default 80-column wrap folds a sentence across
        # lines, and this file's whole job is to be read as a diff by a human
        # deciding whether to authorize it.
        width=4096,
    )
    return _HEADER + body


def blast_radius_of(request: ValidatedRequest) -> BlastRadius:
    return request.resolved.blast_radius


__all__ = [
    "FILENAME_RE",
    "REQUESTS_DIR",
    "SCHEMA_VERSION",
    "ManifestError",
    "OperationRequest",
    "ValidatedRequest",
    "blast_radius_of",
    "implicit_target_issues",
    "parse_manifest",
    "render_manifest",
    "validate_manifest",
]
