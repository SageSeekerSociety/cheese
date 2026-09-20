r"""Cross-platform local path normalization — normalize first, compare after.

A grant names a directory on someone's own machine, and every later access has to
answer one question: *is this path the granted directory, or something under it?*
The obvious implementation — ``path.startswith(grant)`` — is wrong in a way that
is invisible until it is exploited, so it is not written anywhere in this package.
It is wrong because a prefix is not a containment relation::

    grant  C:/Users/alice/MyDocs
    path   C:/Users/alice/MyDocuments/secret.txt     startswith -> True

and because the string that arrives is not the string the filesystem will use::

    C:\Users\alice\MyDocs\..\..\Windows\System32\config\SAM   (Windows spelling)
    C:/Users/alice/MyDocs/sub/../../../../etc/shadow
    ~/Documents            (or ``./docs``, or ``Documents``)
    C:/Users/alice/mydocs/x          (Windows folds case; the string does not)
    C:/Users/alice/MyDocs./x         (Win32 strips a trailing dot from the segment)
    C:/Users/alice/MyDocs/NUL.txt    (a reserved device name, not a file at all)

So the only comparison this package offers is over **normalized segments**: fold
the path to an absolute, separator-free, dot-free, case-folded-on-case-insensitive-
filesystems list of segments, then ask whether one segment list is a prefix of the
other. Two different files can never produce the same segment list, and a path can
never escape its own root — a ``..`` that would climb above the root is an ERROR
here, not a silent clamp, because the clamp is what turns an escape attempt into a
quiet success on some other directory.

**Symlinks are deliberately NOT resolved here.** Resolving them requires reading
the filesystem, and the filesystem that matters is the user's, not the platform's —
so resolution happens on the device (``cli/internal/localfs``), which resolves the
real path and sends it back. The platform then normalizes *that* and compares. A
module that guessed at symlink targets from the platform side would be comparing
its guess, not the disk.

This module is pure: no I/O, no settings, no database. The Go daemon carries a
deliberate mirror of it (``cli/internal/localfs/paths.go``) because the device
must reach the same verdict on its own — a platform that is buggy, rolled back or
merely lying must not be able to talk the daemon into touching an ungranted path.
The two implementations are held to the same table of cases in their tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "MAX_PATH_LENGTH",
    "MAX_SEGMENTS",
    "NormalizedPath",
    "PathRefused",
    "Platform",
    "contains",
    "is_within",
    "normalize",
]


class Platform(str, Enum):
    """The filesystem conventions of the machine a path lives on.

    Not the platform the *platform* runs on: these paths are always somebody
    else's, so the caller must say which machine it is asking about. Getting this
    wrong is how a Windows path acquires POSIX case-sensitivity and a macOS path
    loses it.
    """

    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"

    @property
    def case_insensitive(self) -> bool:
        """Whether two paths differing only in case name the same file.

        Windows and macOS both default to a case-insensitive, case-preserving
        filesystem (NTFS, APFS/HFS+). Linux is case-sensitive. Folding the
        comparison key on the first two is what makes ``C:/Users/Alice`` and
        ``c:/users/alice`` one grant instead of two — without it, a grant is
        trivially bypassed by re-casing the path.
        """
        return self is not Platform.LINUX


class PathRefused(Exception):
    """A path that cannot be normalized, carrying a reason code.

    Every refusal is a named code rather than a bare failure, because the reason
    is shown to the person whose file was refused and written to the audit log.
    拒绝要让人看得见 — a refusal nobody can explain is indistinguishable from a
    bug, and the audit row is what makes it a decision instead.
    """

    def __init__(self, reason: str, detail: str, raw: str) -> None:
        super().__init__(reason + ": " + detail)
        self.reason = reason
        self.detail = detail
        self.raw = raw


@dataclass(frozen=True, slots=True)
class NormalizedPath:
    """A path reduced to something two paths can be compared by.

    ``root`` is the anchor that ``..`` may never climb past — ``/`` for POSIX, a
    drive (``C:``) or a UNC share (``//server/share``) for Windows. ``segments``
    holds the path below it, already dot-free. ``key`` is the single string a
    human-readable comparison and a database index both use; it is not what the
    filesystem is asked to open (that is the device's original, resolved text).
    """

    platform: Platform
    root: str
    segments: tuple[str, ...]
    # The same segments folded the way this filesystem compares names.
    # ``contains`` reads THIS, not ``segments``: on a case-insensitive
    # filesystem two names differing only in case are one file, and a
    # containment check over the unfolded segments would deny a legitimate
    # access while looking entirely correct.
    folded_segments: tuple[str, ...]
    # Canonical text: root + segments, case preserved. This is what the UI shows.
    text: str
    # Comparison key: ``text`` case-folded on case-insensitive platforms.
    key: str

    @property
    def is_root(self) -> bool:
        return not self.segments


# A path longer than this is refused rather than truncated: truncation would make
# two different paths compare equal, which is the one outcome this module exists
# to prevent. 4096 is the modern ceiling and comfortably above anything a person
# authorizes by hand.
MAX_PATH_LENGTH = 4096
# Depth cap. Also the bound that keeps a recursive listing from being unbounded —
# the listing limit itself is much smaller and lives at the call site.
MAX_SEGMENTS = 256

# Win32 reserved device names. Any of these, with or without an extension, names a
# device rather than a file under the directory — ``C:/granted/NUL.txt`` is the
# null device, so a "write inside the grant" would not touch the disk at all.
_RESERVED_WINDOWS_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {"com" + str(d) for d in range(1, 10)}
    | {"lpt" + str(d) for d in range(1, 10)}
)

_BACKSLASH = chr(92)


def _has_control_char(text: str) -> bool:
    """NUL and the C0/C1 control range, which no filesystem in scope accepts.

    Written as a scan rather than a regex on purpose: a regex would need the same
    escapes in both this file and its Go mirror, and the two escaping dialects are
    exactly where a security check silently stops matching.
    """
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text)


def normalize(raw: str, platform: Platform) -> NormalizedPath:
    """Reduce ``raw`` to a :class:`NormalizedPath`, or refuse it by name.

    The order of the steps is the security property, not an implementation
    detail. Separators are folded first so that one spelling of a path cannot
    dodge a check written against another; ``..`` is resolved second, before
    anything compares segments; case is folded last, so that the key two callers
    compare is already in the form the filesystem would have used.
    """
    if not isinstance(raw, str):
        raise PathRefused("not_text", "路径必须是文本", repr(raw))
    if not raw.strip():
        raise PathRefused("empty", "路径为空", raw)
    if _has_control_char(raw):
        raise PathRefused("control_char", "路径含控制字符", raw)
    if len(raw) > MAX_PATH_LENGTH:
        raise PathRefused(
            "too_long", "路径超过 " + str(MAX_PATH_LENGTH) + " 个字符", raw
        )

    if platform is Platform.WINDOWS:
        root, segments = _windows(raw)
    else:
        root, segments = _posix(raw)

    if len(segments) > MAX_SEGMENTS:
        raise PathRefused("too_deep", "路径超过 " + str(MAX_SEGMENTS) + " 层", raw)

    text = _join(root, segments)
    key = text.casefold() if platform.case_insensitive else text
    folded = (
        tuple(s.casefold() for s in segments)
        if platform.case_insensitive
        else segments
    )
    return NormalizedPath(
        platform=platform,
        root=root,
        segments=segments,
        folded_segments=folded,
        text=text,
        key=key,
    )


def _windows(raw: str) -> tuple[str, tuple[str, ...]]:
    """Windows: a UNC share or a drive-letter root; nothing else is absolute
    enough to authorize."""
    # One spelling. A caller who writes the backslash form and one who writes the
    # forward-slash form must land on the same grant, or the second spelling is a
    # way to reach a path the first spelling's check would have refused.
    text = raw.replace(_BACKSLASH, "/")

    # The ``//?/`` and ``//./`` prefixes (the Win32 device namespaces) reach the
    # filesystem through a different parser — one that does NOT strip trailing
    # dots and does not resolve ``..``. Refusing them outright is the only way to
    # keep this module's assumption true: that Win32's own normalization agrees
    # with ours.
    if text.startswith("//?/") or text.startswith("//./"):
        raise PathRefused("device_prefix", "不支持 Win32 设备命名空间前缀", raw)

    if text.startswith("//"):
        # UNC ``//server/share/rest``. Server and share are the root; ``..`` must
        # not be able to climb above the share into another share on the server.
        parts = [p for p in text[2:].split("/") if p not in ("", ".")]
        if len(parts) < 2:
            raise PathRefused("unc_incomplete", "UNC 路径缺少共享名", raw)
        server, share, rest = parts[0], parts[1], parts[2:]
        if not server or not share:
            raise PathRefused("unc_incomplete", "UNC 路径缺少服务器或共享名", raw)
        root = "//" + server + "/" + share
        segments = _collapse(rest, root, raw, fold_segment=_fold_windows_segment)
        return root, segments

    if len(text) < 2 or text[1] != ":" or not text[0].isalpha():
        # No drive letter. ``/Users/alice`` and ``Users/alice`` are both refused:
        # the first is root-relative (it means "on whatever drive the process is
        # on", which is not a fact this module knows) and the second is relative to
        # a working directory that is not a fact either. A relative path cannot be
        # authorized, because the directory it denotes is not fixed.
        raise PathRefused(
            "not_absolute", "Windows 路径必须以盘符或 UNC 共享开头", raw
        )
    letter = text[0].upper()
    rest = text[2:]
    if not rest.startswith("/"):
        # ``C:foo`` is drive-relative — it means "foo under the current directory
        # of drive C", whose location depends on per-drive process state. Two runs
        # of the same request can name two different files, so it cannot be
        # authorized at all.
        raise PathRefused("drive_relative", "不支持盘符相对路径（如 C:foo）", raw)
    root = letter + ":"
    segments = _collapse(
        [p for p in rest.split("/") if p not in ("", ".")],
        root,
        raw,
        fold_segment=_fold_windows_segment,
    )
    return root, segments


def _posix(raw: str) -> tuple[str, tuple[str, ...]]:
    """POSIX: an absolute ``/``-rooted path, or nothing."""
    if not raw.startswith("/"):
        # Same reasoning as Windows' ``not_absolute``: ``Documents``, ``./docs``
        # and ``../x`` all denote a directory that depends on where the process
        # happens to be standing, so none of them can be authorized. The tilde is
        # included here on purpose — the shell expands it, the kernel does not, and
        # a device that received it would either fail or (worse) expand it to a
        # home directory the platform never agreed to. The device resolves the
        # tilde before normalization, which is why what reaches here is absolute.
        raise PathRefused(
            "not_absolute",
            "路径必须是绝对路径（~ 与相对路径请在设备上解析为绝对路径）",
            raw,
        )
    # POSIX allows exactly two leading slashes to be implementation-defined; one
    # slash or three-or-more are the same as one. Since a grant is compared by
    # segment, collapsing ``//`` to ``/`` here keeps ``//etc`` from becoming a
    # second, unrefused spelling of ``/etc``.
    stripped = raw.lstrip("/")
    segments = _collapse(
        [p for p in stripped.split("/") if p not in ("", ".")],
        "/",
        raw,
    )
    return "/", segments


def _collapse(
    parts: list[str],
    root: str,
    raw: str,
    *,
    fold_segment=None,
) -> tuple[str, ...]:
    """Resolve ``.`` and ``..`` lexically; refuse a climb above ``root``.

    ``..`` is popped against the segments already accepted, which is the same
    thing the filesystem does for a path with no symlinks in it. When there is
    nothing left to pop, the path is trying to leave the anchor it is measured
    against — and that is refused rather than clamped. Clamping is the tempting
    behaviour and the dangerous one: it turns ``/granted/../../etc`` into
    ``/etc``, i.e. it answers a refusal-shaped question with a silent success on
    some *other* directory.
    """
    out: list[str] = []
    for part in parts:
        if part == ".":
            continue
        if part == "..":
            if not out:
                raise PathRefused(
                    "escapes_root", "路径向上越过了根 " + root, raw
                )
            out.pop()
            continue
        if fold_segment is not None:
            part = fold_segment(part, raw)
        if not part:
            continue
        out.append(part)
    return tuple(out)


def _fold_windows_segment(segment: str, raw: str) -> str:
    """Apply Win32's own segment rewrites, so our idea of the path matches its.

    Two rewrites matter enough to implement, because both are ways to name a file
    other than the one the raw string appears to name:

    * **Trailing dots and spaces are stripped.** ``MyDocs.`` and ``MyDocs `` (with
      a trailing blank) both reach ``MyDocs``. A check that compared the raw
      segment would see a different name than the one opened.
    * **Reserved device names name devices.** ``NUL``, ``CON``, ``COM1`` … are
      devices whether or not an extension follows, so ``NUL.txt`` is not a file
      under the directory and must not be treated as one.

    Neither rewrite is a containment bypass on its own; both are the reason a
    "normalize first" module has to know which filesystem it is normalizing for.
    """
    stripped = segment.rstrip(" .")
    if not stripped:
        raise PathRefused("empty_segment", "段在去掉结尾的点与空格后为空", raw)
    stem = stripped.split(".")[0]
    if stem.casefold() in _RESERVED_WINDOWS_NAMES:
        raise PathRefused(
            "reserved_name", "「" + stripped + "」是 Windows 保留设备名", raw
        )
    return stripped


def _join(root: str, segments: tuple[str, ...]) -> str:
    if root == "/":
        return "/" + "/".join(segments) if segments else "/"
    if root.startswith("//"):
        return root + ("/" + "/".join(segments) if segments else "")
    # A drive root keeps its slash: ``C:/`` is the drive, ``C:`` is drive-relative.
    return root + "/" + "/".join(segments) if segments else root + "/"


def contains(outer: NormalizedPath, inner: NormalizedPath) -> bool:
    """Whether ``inner`` is ``outer`` itself or lies beneath it.

    Segment-wise, so the ``MyDocs`` / ``MyDocuments`` confusion cannot be
    expressed. The roots must match, and ``outer``'s folded segments must be a
    prefix of ``inner``'s. Both sides must already be normalized — there is no
    raw-string overload, deliberately, because the raw-string version is the bug.

    The comparison runs on ``folded_segments``, so it asks the question the
    filesystem would answer: on Windows and macOS ``C:/Users/Alice/MyDocs`` does
    contain ``c:/users/alice/mydocs/x``, and on Linux it does not. Comparing the
    unfolded segments instead would silently deny access that the folder's own
    grant plainly covers — a failure on the safe side, but a real failure.

    Two paths on different kinds of filesystem are never comparable, so a
    mismatched platform pair answers False rather than picking one side's rules.
    In practice a device has one platform, so this only catches caller errors.
    """
    if outer.platform is not inner.platform:
        return False
    if _folded_root(outer) != _folded_root(inner):
        return False
    if len(inner.folded_segments) < len(outer.folded_segments):
        return False
    return (
        inner.folded_segments[: len(outer.folded_segments)] == outer.folded_segments
    )


def _folded_root(path: NormalizedPath) -> str:
    """The anchor, folded the way this filesystem compares it.

    A UNC root (``//server/share``) is two names a Windows filesystem folds; a
    POSIX root is a slash and folding it changes nothing.
    """
    return path.root.casefold() if path.platform.case_insensitive else path.root


def is_within(grant: NormalizedPath, candidate: NormalizedPath) -> bool:
    """Alias of :func:`contains`, named for the question the caller is asking."""
    return contains(grant, candidate)
