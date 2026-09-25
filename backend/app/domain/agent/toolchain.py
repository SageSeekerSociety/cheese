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
FJ_VERSION = "0.6.0-cheese.2"
GH_VERSION = "2.62.0"
#: The runtime a Windows machine's connector provisions for itself, so that the
#: `python3` and `sh` the platform runs everywhere exist there too. Windows only:
#: every other machine brings its own.
PYTHON_VERSION = "3.13.13"
GIT_VERSION = "2.55.0.windows.5"

#: The commits `Sans2.004` and `Serif2.003` pointed at when these were pinned.
_SANS_COMMIT = "523d033d6cb47f4a80c58a35753646f5c3608a78"
_SERIF_COMMIT = "9b0f1436e455d902de067a2501422e5dc71ad16b"

_TYPST_BASE = f"https://github.com/typst/typst/releases/download/v{TYPST_VERSION}"
_PANDOC_BASE = f"https://github.com/jgm/pandoc/releases/download/{PANDOC_VERSION}"
_UV_BASE = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}"
_GH_BASE = f"https://github.com/cli/cli/releases/download/v{GH_VERSION}"
_PYTHON_BASE = f"https://www.python.org/ftp/python/{PYTHON_VERSION}"
_GIT_BASE = f"https://github.com/git-for-windows/git/releases/download/v{GIT_VERSION}"
_FJ_BASE = (
    "https://github.com/SageSeekerSociety/cheese/releases/download/"
    f"forgejo-cli-v{FJ_VERSION}"
)
_NOTO_RAW = "https://raw.githubusercontent.com/notofonts/noto-cjk"

#: Platform strings are OURS, matching what the launcher computes on the machine
#: (`<os>-<arch>`). typst and uv ship musl-static linux builds, so one artifact
#: serves glibc and musl alike; pandoc's linux tarball wants glibc, which is why
#: a musl machine is a documented gap rather than a silent one. Windows is x64
#: only.
PLATFORM_RE = re.compile(r"^((linux|darwin)-(x64|arm64)|windows-x64)$")
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
    # scripts/build-forge-cli.sh applies the merged-status localization fix.
    ("fj", "darwin-arm64"): Artifact(
        f"{_FJ_BASE}/fj-{FJ_VERSION}-aarch64-apple-darwin.tar.gz",
        "190d36a0e4eb1005ac50b60bc48121dd426f29eab884618ee175dce960c6b6fc",
        9139626,
        ".tar.gz",
    ),
    ("fj", "darwin-x64"): Artifact(
        f"{_FJ_BASE}/fj-{FJ_VERSION}-x86_64-apple-darwin.tar.gz",
        "cc8471533caa2758f771cd4e9e973804ab07c9f87ddec8e52c3932c2bde582b1",
        9231619,
        ".tar.gz",
    ),
    ("gh", "linux-x64"): Artifact(
        f"{_GH_BASE}/gh_{GH_VERSION}_linux_amd64.tar.gz",
        "41c8b0698ad3003cb5c44bde672a1ffd5f818595abd80162fbf8cc999418446a",
        13065800,
        ".tar.gz",
    ),
    ("gh", "linux-arm64"): Artifact(
        f"{_GH_BASE}/gh_{GH_VERSION}_linux_arm64.tar.gz",
        "a165413209aab98bfb1db9629b97bc9c59778d38bb7378a33a0363cf822e7965",
        12118266,
        ".tar.gz",
    ),
    ("gh", "darwin-x64"): Artifact(
        f"{_GH_BASE}/gh_{GH_VERSION}_macOS_amd64.zip",
        "cd547c05c175a79e5af6f95ba4881a11ca550c0ff37a63f234bc5c79a58435d5",
        13725376,
        ".zip",
    ),
    ("gh", "darwin-arm64"): Artifact(
        f"{_GH_BASE}/gh_{GH_VERSION}_macOS_arm64.zip",
        "fdb77f31b8a6dd23c3fd858758d692a45f7fc76383e37d475bdcae038df92afc",
        12793347,
        ".zip",
    ),
    ("gh", "windows-x64"): Artifact(
        f"{_GH_BASE}/gh_{GH_VERSION}_windows_amd64.zip",
        "7fd29acdf2714d0129b7aedde215fa12e1cfb3ad5d39280893259bdeeceba209",
        13139194,
        ".zip",
    ),
    ("fj", "linux-arm64"): Artifact(
        f"{_FJ_BASE}/fj-{FJ_VERSION}-aarch64-unknown-linux-gnu.tar.gz",
        "ae29e149de589d04d1e83fc9eb23eb8118bd9cc871bfb07dc8515fc874b2b412",
        10381911,
        ".tar.gz",
    ),
    ("fj", "linux-x64"): Artifact(
        f"{_FJ_BASE}/fj-{FJ_VERSION}-x86_64-unknown-linux-gnu.tar.gz",
        "f426f59e0f4136b0a97c0ae24367f4a4dcc865d4f805aa1f9ec8bde082d371f7",
        10239679,
        ".tar.gz",
    ),
    ("fj", "windows-x64"): Artifact(
        f"{_FJ_BASE}/fj-{FJ_VERSION}-x86_64-pc-windows-gnu.zip",
        "a2222c6c3fa77098aef5e8bcec0cc4276372e91d5f943c838ef4fa7cb1cddafa",
        8343164,
        ".zip",
    ),
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
    ("typst", "windows-x64"): Artifact(
        f"{_TYPST_BASE}/typst-x86_64-pc-windows-msvc.zip",
        "19ce3551153c2fe7ee9fa2f95208310c8f4d3209fedb699e0333faf8913f6736",
        22463684,
        ".zip",
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
    ("pandoc", "windows-x64"): Artifact(
        f"{_PANDOC_BASE}/pandoc-{PANDOC_VERSION}-windows-x86_64.zip",
        "2ab72baf2399450e148ddf7a2a8689806c42e1bba71862b57e220fd9b8456d3d",
        41761100,
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
    ("uv", "windows-x64"): Artifact(
        f"{_UV_BASE}/uv-x86_64-pc-windows-msvc.zip",
        "477bd99a84e34891f2bd4c9152ddeb74e971accccbc59c0f0301f11f08a32d46",
        17578593,
        ".zip",
    ),
    # The connector's own runtime on Windows (see PYTHON_VERSION). The embeddable
    # distribution is the interpreter and its standard library, nothing else.
    ("python", "windows-x64"): Artifact(
        f"{_PYTHON_BASE}/python-{PYTHON_VERSION}-embed-amd64.zip",
        "8766a8775746235e23cf5aee5027ab1060bb981d93110577adcf3508aa0cbd55",
        10950201,
        ".zip",
    ),
    ("git", "windows-x64"): Artifact(
        f"{_GIT_BASE}/PortableGit-2.55.0.5-64-bit.7z.exe",
        "5aa8a20f6e9abb2c755f0e73c91c687701a46b309ad84a0ca6509380fa4ae290",
        58960208,
        ".7z.exe",
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
