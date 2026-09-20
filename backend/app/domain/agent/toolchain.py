"""What the platform puts on a machine so a room can write a document.

The single source for these — versions, where each artifact comes from, and its
digest. It lives in the agent layer for the same reason claude's pin lives in
``harness/claude_code/device_launch`` rather than in ``machine/claude_dist``:
the layer that RUNS a room decides what a room needs, and the machine layer
serves whatever that decision names. The import only ever goes machine → agent,
and `check-repo-rules` enforces that direction.

What these tools do NOT share with claude and pi is a vendor checksum. Measured
on 2026-09-16: typst v0.15.1 and pandoc 3.11 publish no checksum asset at all,
and only uv does. So the digest lives here, beside the version.

That is not a downgrade from the other two — it is the stronger property. A
checksum the vendor serves from the same place as the artifact vouches for
nothing against whoever can replace the artifact; it catches a corrupt transfer
and a bad mirror. A digest recorded here was looked at by a person in a pull
request, and a release re-cut under the same tag fails to verify instead of
being served. The cost is that bumping a version is a commit that changes the
version AND the digest, which is the review this is for.

The fonts are here for the same reason the binaries are, and they are not
optional decoration: with no CJK face typst exits 0 and writes a PDF of normal
size in which every Chinese character is an empty box. Nothing the platform can
check sees that — only a person looking at the page. They are pinned to the
commit their release tag resolved to rather than to the tag, because a tag can
be moved and a commit cannot.
"""

import re
from dataclasses import dataclass

TYPST_VERSION = "0.15.1"
PANDOC_VERSION = "3.11"
UV_VERSION = "0.12.15"

#: The commits `Sans2.004` and `Serif2.003` pointed at when these were pinned.
_SANS_COMMIT = "523d033d6cb47f4a80c58a35753646f5c3608a78"
_SERIF_COMMIT = "9b0f1436e455d902de067a2501422e5dc71ad16b"

_TYPST_BASE = f"https://github.com/typst/typst/releases/download/v{TYPST_VERSION}"
_PANDOC_BASE = f"https://github.com/jgm/pandoc/releases/download/{PANDOC_VERSION}"
_UV_BASE = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}"
_NOTO_RAW = "https://raw.githubusercontent.com/notofonts/noto-cjk"

#: Platform strings are OURS, matching what the launcher computes on the machine
#: (`<os>-<arch>`). typst and uv ship musl-static linux builds, so one artifact
#: serves glibc and musl alike; pandoc's linux tarball wants glibc, which is why
#: a musl machine is a documented gap rather than a silent one.
PLATFORM_RE = re.compile(r"^(linux|darwin)-(x64|arm64)$")
TOOL_RE = re.compile(r"^[a-z0-9-]{1,32}$")

_ANY = "any"


@dataclass(frozen=True, slots=True)
class Artifact:
    url: str
    sha256: str
    size: int
    #: What the machine receives, so it can pick an unpacker without guessing
    #: from the bytes.
    suffix: str


ARTIFACTS: dict[tuple[str, str], Artifact] = {
    ("typst", "linux-x64"): Artifact(
        f"{_TYPST_BASE}/typst-x86_64-unknown-linux-musl.tar.xz",
        "a6d077d0a95eed5a2eba715b2dae06be954f624ccbf85758a03f389ded33118c",
        17462992,
        ".tar.xz",
    ),
    ("typst", "linux-arm64"): Artifact(
        f"{_TYPST_BASE}/typst-aarch64-unknown-linux-musl.tar.xz",
        "5aa8d74a3d906e60ea12a66ac2f37f8eef1b14cbad7182a745e393a10c23dcee",
        16216812,
        ".tar.xz",
    ),
    ("typst", "darwin-x64"): Artifact(
        f"{_TYPST_BASE}/typst-x86_64-apple-darwin.tar.xz",
        "7f9fdd9584866245de9a79e0add8f9236fae6f40a8a45e2c4771ccc14db4e0fa",
        15635600,
        ".tar.xz",
    ),
    ("typst", "darwin-arm64"): Artifact(
        f"{_TYPST_BASE}/typst-aarch64-apple-darwin.tar.xz",
        "48f62ed034aa3a7978309579ac6ca00045e2ef0da73114e8af27cfd8e74dc05a",
        14438168,
        ".tar.xz",
    ),
    ("pandoc", "linux-x64"): Artifact(
        f"{_PANDOC_BASE}/pandoc-{PANDOC_VERSION}-linux-amd64.tar.gz",
        "37edb3bbcf722f921a009941bf5874e2e0c09263226c9b4a2d980788cb062ab6",
        34940580,
        ".tar.gz",
    ),
    ("pandoc", "linux-arm64"): Artifact(
        f"{_PANDOC_BASE}/pandoc-{PANDOC_VERSION}-linux-arm64.tar.gz",
        "56ed5566ec41d22ec9ee0704e6ac0b98ba102e92384efd5306173a22d314c79a",
        37408185,
        ".tar.gz",
    ),
    ("pandoc", "darwin-x64"): Artifact(
        f"{_PANDOC_BASE}/pandoc-{PANDOC_VERSION}-x86_64-macOS.zip",
        "3b1c1b57f160112c821d02f23d946ede8b7f57a6ccf4632a25a512d334a9291f",
        26145603,
        ".zip",
    ),
    ("pandoc", "darwin-arm64"): Artifact(
        f"{_PANDOC_BASE}/pandoc-{PANDOC_VERSION}-arm64-macOS.zip",
        "15806bedf9517bfead72e88fe6a6696635c3691efbb6e152173440e9c5bb50b4",
        41832712,
        ".zip",
    ),
    ("uv", "linux-x64"): Artifact(
        f"{_UV_BASE}/uv-x86_64-unknown-linux-musl.tar.gz",
        "999c0c3da986953e508985c3932d283d2c62eb167b4f8d81e79f565e34104959",
        22429965,
        ".tar.gz",
    ),
    ("uv", "linux-arm64"): Artifact(
        f"{_UV_BASE}/uv-aarch64-unknown-linux-musl.tar.gz",
        "93b801abb146e6431fb0434346a0162e65d3f0d1cd7360144d04c43488fd7f7d",
        20754110,
        ".tar.gz",
    ),
    ("uv", "darwin-x64"): Artifact(
        f"{_UV_BASE}/uv-x86_64-apple-darwin.tar.gz",
        "e9ca61775532368fe518ab03e7a354c7ecab8ccb3c7d941c775fcc4a362b801b",
        20292831,
        ".tar.gz",
    ),
    ("uv", "darwin-arm64"): Artifact(
        f"{_UV_BASE}/uv-aarch64-apple-darwin.tar.gz",
        "dc304b9ed1b24174572290fba60ac3f6fe63c73a671f0439e62a91375841964d",
        16678128,
        ".tar.gz",
    ),
    # Fonts carry no machine code, so one file serves every platform.
    ("font-sans", _ANY): Artifact(
        f"{_NOTO_RAW}/{_SANS_COMMIT}/Sans/Variable/OTF/Subset/NotoSansSC-VF.otf",
        "d13ed01ec8aa45d6178999b648e96fb92150683e9f8e2a581f2acf208dcbe44b",
        15054748,
        ".otf",
    ),
    ("font-serif", _ANY): Artifact(
        f"{_NOTO_RAW}/{_SERIF_COMMIT}/Serif/Variable/OTF/Subset/NotoSerifSC-VF.otf",
        "71b4d3ded2d90ff43bb75a4e48cdbe170f0b8d5486dc89ff87f2a1728b56da64",
        22613272,
        ".otf",
    ),
}

#: Tools whose artifact is the same everywhere, so a caller may ask for any
#: platform and get the one file.
PLATFORM_FREE = frozenset({"font-sans", "font-serif"})


def fonts_pin() -> str:
    """One directory name for the font pair, so bumping either refetches both.

    Two fonts with two commits would otherwise need two directories and two
    entries in TYPST_FONT_PATHS, and the pair is only ever shipped together.
    """
    return f"{_SANS_COMMIT[:12]}-{_SERIF_COMMIT[:12]}"


#: What the launcher places on a machine: (tool, version, kind, filename).
#: `kind` is what the machine does with the artifact — unpack and find an
#: executable of that name, or drop the file into the font directory. The
#: version is the directory name on the machine, which is what makes a bump land
#: beside the old copy rather than over it.
PLACEMENTS: tuple[tuple[str, str, str, str], ...] = (
    ("typst", TYPST_VERSION, "bin", "typst"),
    ("pandoc", PANDOC_VERSION, "bin", "pandoc"),
    ("uv", UV_VERSION, "bin", "uv"),
    ("font-sans", "pinned", "font", "NotoSansSC-VF.otf"),
    ("font-serif", "pinned", "font", "NotoSerifSC-VF.otf"),
)


def resolve(tool: str, platform: str) -> tuple[str, Artifact] | None:
    """The artifact for this request, and the platform key it is cached under.

    Returns ``None`` for anything not served, so the caller answers 404 rather
    than reaching upstream for a name nobody publishes.
    """
    if not TOOL_RE.fullmatch(tool) or not PLATFORM_RE.fullmatch(platform):
        return None
    key = _ANY if tool in PLATFORM_FREE else platform
    artifact = ARTIFACTS.get((tool, key))
    return (key, artifact) if artifact else None
