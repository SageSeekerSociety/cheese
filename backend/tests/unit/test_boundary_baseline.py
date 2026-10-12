"""脚本 boundary_baseline.py:刷新 `.importlinter` 的冻结块,但不许把一条真债丢掉。

这个脚本管三件事,每一件都是「改错了会让门禁看起来还是绿的」那一类,所以每条规则
都在这里被钉住:

1. **八条契约一条都不能少。** 少一条,刷新的对象就变小了——它会拿着一个残缺的声明
   刷出一份「通过」的基线。删掉或改名任何一条,脚本必须退 2,而不是照刷。
2. **冻结块有固定的形状。** 标记行、副标题、排序、去重、不许通配符;谁手动改坏了
   其中一样,脚本必须拒绝动手,而不是把没识别的东西悄悄留在声明里。
3. **C3 的陈旧行只认检查器。** C1/C2 是重算出来的,C3 不是:它的反馈边集不唯一,
   重算会给同一团环换一套切边。实测在这个仓库上,C3 重算会丢掉 21 条「导入仍在」
   的冻结边、再加回 66 条别的——所以本文件既在真仓库上回归(那些边一条都不许掉),
   也在真子进程里验证 C3 的新边即使 `--freeze-new` 也不写。

测试从 backend/ 目录跑(CI 就是这么跑的),子进程用的是同一个解释器,和 CI、和
pre-commit 用的是同一套依赖。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
SCRIPTS = BACKEND / "scripts"
SCRIPT = SCRIPTS / "boundary_baseline.py"
REAL_CONFIG = BACKEND / ".importlinter"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bb = _load("boundary_baseline_under_test", SCRIPT)
cb = _load("check_boundaries_for_seed", SCRIPTS / "check_boundaries.py")

CYCLE = bb.CYCLE_CONTRACT


# ---------------------------------------------------------------------------
# Building trees and configs. The seed tree is check_boundaries.py's own — the
# smallest tree that satisfies all seven declarations — so the script is judged
# on the same tree the gate is, not on a fixture that agrees with it by luck.
# ---------------------------------------------------------------------------


def _empty_config() -> str:
    """The real declarations with every frozen block emptied out."""
    return bb.write_baselines(
        REAL_CONFIG.read_text(), {c: [] for c in bb.BLOCK_CONTRACTS}
    )


def _plant(tmp: Path, config_text: str, files: dict[str, str] | None = None) -> Path:
    for relative, contents in {**cb._SEED, **(files or {})}.items():
        path = tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    config = tmp / ".importlinter"
    config.write_text(config_text)
    return config


def _run(tmp: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(tmp / ".importlinter"), *args],
        cwd=tmp,
        capture_output=True,
        text=True,
    )


def _section_span(text: str, contract_id: str) -> tuple[int, int]:
    """The inclusive-exclusive line span of one `[importlinter:contract:...]`."""
    lines = text.split("\n")
    header = f"[importlinter:contract:{contract_id}]"
    start = lines.index(header)
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if bb.HEADER_RE.match(lines[index]):
            end = index
            break
    return start, end


def _edit_section(text: str, contract_id: str, edit) -> str:
    """Apply `edit(lines)` to one section's lines and put the file back together."""
    lines = text.split("\n")
    start, end = _section_span(text, contract_id)
    return "\n".join(lines[:start] + edit(lines[start:end]) + lines[end:])


def _drop_section(text: str, contract_id: str) -> str:
    return _edit_section(text, contract_id, lambda lines: [])


def _rename_section(text: str, contract_id: str) -> str:
    return text.replace(
        f"[importlinter:contract:{contract_id}]",
        f"[importlinter:contract:{contract_id}_renamed]",
        1,
    )


def _cycle_files() -> dict[str, str]:
    """A two-node cycle among sibling domains, so C3 has a real violation."""
    alpha_reads_bar = "from app.domain.bar.services import read_bar\n"
    bar_reads_alpha = "from app.domain.alpha.services import alpha_read\n"
    return {
        "app/domain/alpha/__init__.py": "",
        "app/domain/alpha/services.py": alpha_reads_bar,
        "app/domain/bar/services.py": bar_reads_alpha,
    }


def _c2_violation() -> dict[str, str]:
    """A route reading another domain's models directly — a real C2 violation."""
    return {
        "app/api/routes/demo.py": (
            "from app.domain.bar.models import Bar\n"
            "from app.domain.bar.services import read_bar\n"
        )
    }


def _c1_violation() -> dict[str, str]:
    """A core module reaching up into api — a real C1 violation."""
    return {"app/core/jobs.py": "from app.api.deps import get_db\n"}


#: The two-node cycle the seed below plants. `_REAL_C3_EDGE` is a cut of it that
#: the checker needs (so it is never stale) but that a recompute does *not*
#: name — a recompute names the opposite edge. That is the point: a baseline
#: refreshed from the recompute would swap one live edge for the other, so the
#: tests below can tell a stale-only drop apart from writing the recomputed cut.
#: `_BOGUS_C3_PAIR` names modules that exist but do not import, so the checker
#: calls exactly that entry unused.
_REAL_C3_EDGE = "app.domain.alpha.services -> app.domain.bar.services"
_BOGUS_C3_PAIR = "app.domain.alpha.services -> app.domain.bar.models"


def _c3_config(bogus: bool) -> str:
    """The declarations with the C3 block holding the cut, plus a stale pair."""
    entries = [_REAL_C3_EDGE, _BOGUS_C3_PAIR] if bogus else [_REAL_C3_EDGE]
    return bb.write_baselines(
        _empty_config(),
        {
            "api-domain-core": [],
            "routes-touch-no-models": [],
            "domains-acyclic": entries,
            "routes-independent": [],
        },
    )


# ---------------------------------------------------------------------------
# Every one of the eight contracts must be declared, or the refresh is refused.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("contract_id", bb.CONTRACT_IDS)
def test_a_missing_declaration_is_cannot_judge(tmp_path, contract_id):
    config = _plant(tmp_path, _drop_section(_empty_config(), contract_id))
    result = _run(tmp_path)
    assert result.returncode == bb.CANNOT_JUDGE, result.stdout + result.stderr
    assert "cannot judge" in (result.stdout + result.stderr)
    assert config.read_text() == _drop_section(_empty_config(), contract_id)


@pytest.mark.parametrize("contract_id", bb.CONTRACT_IDS)
def test_a_renamed_declaration_is_cannot_judge(tmp_path, contract_id):
    _plant(tmp_path, _rename_section(_empty_config(), contract_id))
    result = _run(tmp_path)
    assert result.returncode == bb.CANNOT_JUDGE, result.stdout + result.stderr
    assert "cannot judge" in (result.stdout + result.stderr)


# ---------------------------------------------------------------------------
# The generated block has one shape, and this script refuses anything else
# rather than rewriting a file it did not fully understand.
# ---------------------------------------------------------------------------


def test_the_c3_block_carries_its_own_subtitle():
    # The test below edits this exact string; if the file stopped carrying it,
    # that test would silently stop testing anything.
    assert bb.C3_SUBHEAD in REAL_CONFIG.read_text()
    assert bb.C3_SUBHEAD != bb.SUBHEAD


def test_a_block_carrying_contract_without_its_marker_is_refused():
    text = _edit_section(
        _empty_config(),
        "api-domain-core",
        lambda lines: [line for line in lines if line.strip() != bb.MARKER.strip()],
    )
    with pytest.raises(bb.ConfigShapeError, match="must carry the generated"):
        bb.parse(text)


def test_a_wrong_subtitle_is_refused():
    text = _empty_config().replace(bb.C3_SUBHEAD, bb.SUBHEAD)
    with pytest.raises(bb.ConfigShapeError, match="not the shape this script writes"):
        bb.parse(text)


#: How the guard must see the option however it is spelled: configparser
#: lower-cases the name and delimits it with either `=` or `:` (its defaults,
#: and the reader import-linter uses).
_OPTION_SPELLINGS = ["ignore_imports", "Ignore_Imports"]
_OPTION_DELIMITERS = ["=", ":"]


@pytest.mark.parametrize("delimiter", _OPTION_DELIMITERS)
@pytest.mark.parametrize("spelling", _OPTION_SPELLINGS)
def test_an_ignore_imports_above_the_marker_is_refused(tmp_path, spelling, delimiter):
    # The generated block is rebuilt on a rewrite; options above the marker are
    # kept verbatim. An `ignore_imports` there would keep silencing a real C1
    # violation while never being counted — and with the generated block empty it
    # would not even leave a stale warning, so the refresh would cry "unchanged".
    # Give the run something that *would* change, so the only thing stopping a
    # write is this guard, and require the file back untouched and a refusal.
    def smuggle(lines: list[str]) -> list[str]:
        marker = next(
            i for i, line in enumerate(lines) if line.strip() == bb.MARKER.strip()
        )
        return [
            *lines[:marker],
            f"{spelling} {delimiter}",
            "    app.core.jobs -> app.api.deps",
            *lines[marker:],
        ]

    config = _plant(
        tmp_path,
        _edit_section(_empty_config(), "api-domain-core", smuggle),
        _c1_violation(),
    )
    before = config.read_text()

    with pytest.raises(bb.ConfigShapeError, match="above the generated block"):
        bb.parse(before)

    result = _run(tmp_path)
    assert result.returncode == bb.CANNOT_JUDGE, result.stdout + result.stderr
    assert config.read_text() == before


def test_an_inline_colon_ignore_imports_before_the_marker_is_refused(tmp_path):
    # The exact shape from the review: a colon-delimited option written on one
    # line, above the marker, silencing a real C1 violation. import-linter reads
    # it (configparser takes `:` as a delimiter) but the generated block is
    # empty, so nothing else would notice — the refresh used to say "unchanged".
    def smuggle(lines: list[str]) -> list[str]:
        marker = next(
            i for i, line in enumerate(lines) if line.strip() == bb.MARKER.strip()
        )
        return [
            *lines[:marker],
            "ignore_imports: app.core.jobs -> app.api.deps",
            *lines[marker:],
        ]

    config = _plant(
        tmp_path,
        _edit_section(_empty_config(), "api-domain-core", smuggle),
        _c1_violation(),
    )
    before = config.read_text()

    with pytest.raises(bb.ConfigShapeError, match="above the generated block"):
        bb.parse(before)

    result = _run(tmp_path, "--update")
    assert result.returncode == bb.CANNOT_JUDGE, result.stdout + result.stderr
    assert config.read_text() == before


def test_dry_run_without_update_reports_the_dry_run_first(tmp_path):
    # `--dry-run` on its own used to fall through the "--update is missing"
    # message, hiding that nothing was going to be written either way. The dry
    # run is the more specific reason, so it must be the one reported. A stale
    # entry gives the run something to drop, so it reaches the write decision
    # instead of being turned back earlier for a new violation.
    config_text = bb.write_baselines(
        _empty_config(),
        {
            "api-domain-core": ["app.core.zzz_gone -> app.core.nope_also_gone"],
            "routes-touch-no-models": [],
            "domains-acyclic": [],
            "routes-independent": [],
        },
    )
    config = _plant(tmp_path, config_text)
    before = config.read_text()

    result = _run(tmp_path, "--dry-run")
    assert result.returncode == bb.OK, result.stdout + result.stderr
    assert "dry run" in result.stdout
    assert "pass --update" not in result.stdout
    assert config.read_text() == before


def _first_populated_block(text: str) -> str:
    """A block-carrying contract whose frozen block still holds entries.

    The two tests below perturb a *real* frozen block, and C1's is empty now
    that its three upward imports were paid down. Naming a contract instead
    would make them stop testing anything the day that contract's debt is paid
    too, so they take whichever block still carries entries — and refuse to run
    rather than silently perturb nothing.
    """
    entries = bb.read_entries(text)
    populated = [c for c in bb.BLOCK_CONTRACTS if entries.get(c)]
    assert populated, (
        "no frozen block carries entries any more, so these tests would be "
        "perturbing an empty file; give them another subject"
    )
    return populated[0]


def test_out_of_order_entries_are_refused():
    def swap_first_two(lines: list[str]) -> list[str]:
        first = next(
            i
            for i, line in enumerate(lines)
            if line.startswith("    ") and " -> " in line
        )
        return [*lines[:first], lines[first + 1], lines[first], *lines[first + 2 :]]

    text = REAL_CONFIG.read_text()
    contract_id = _first_populated_block(text)
    text = _edit_section(text, contract_id, swap_first_two)
    with pytest.raises(bb.ConfigShapeError, match="out of order"):
        bb.parse(text)


def test_a_duplicated_entry_is_refused():
    def duplicate_first(lines: list[str]) -> list[str]:
        first = next(
            i
            for i, line in enumerate(lines)
            if line.startswith("    ") and " -> " in line
        )
        return [*lines[:first], lines[first], *lines[first:]]

    text = REAL_CONFIG.read_text()
    contract_id = _first_populated_block(text)
    text = _edit_section(text, contract_id, duplicate_first)
    with pytest.raises(bb.ConfigShapeError, match="repeated"):
        bb.parse(text)


def test_a_wildcard_entry_is_refused():
    text = _edit_section(
        _empty_config(),
        "routes-touch-no-models",
        lambda lines: [
            *lines,
            "ignore_imports =",
            "    app.api.routes.** -> app.domain.**",
        ],
    )
    with pytest.raises(bb.ConfigShapeError, match="wildcard"):
        bb.parse(text)


@pytest.mark.parametrize("delimiter", _OPTION_DELIMITERS)
@pytest.mark.parametrize("spelling", _OPTION_SPELLINGS)
def test_an_unrecognised_ignore_imports_is_refused(spelling, delimiter):
    # A section with no generated block (a structural contract) may not smuggle
    # in an `ignore_imports` either — the same guard, whatever the spelling or
    # the delimiter.
    text = _edit_section(
        _empty_config(),
        "turn-layers",
        lambda lines: [*lines, f"{spelling} {delimiter}", "    app.api -> app.domain"],
    )
    with pytest.raises(bb.ConfigShapeError):
        bb.parse(text)


@pytest.mark.parametrize("delimiter", _OPTION_DELIMITERS)
@pytest.mark.parametrize("spelling", _OPTION_SPELLINGS)
def test_an_unknown_contract_with_ignore_imports_is_refused(spelling, delimiter):
    # A contract this script does not know may not carry exemptions at all: it
    # would run and silence imports while the refresh never counts them.
    text = (
        _empty_config()
        + "\n[importlinter:contract:custom-thing]\n"
        + f"{spelling} {delimiter}\n"
        + "    app.api -> app.domain\n"
    )
    with pytest.raises(bb.ConfigShapeError, match="not one of the contracts"):
        bb.parse(text)


@pytest.mark.parametrize(
    "line, expected",
    [
        ("ignore_imports =", True),
        ("Ignore_Imports =", True),
        ("ignore_imports: app.core.jobs -> app.api.deps", True),
        ("Ignore_Imports: app.core.jobs -> app.api.deps", True),
        ("  ignore_IMPORTS = app.a -> app.b", True),
        # A bare key is not a valid option, but it is the option token, and
        # refusing is the safe direction.
        ("ignore_imports", True),
        ("# ignore_imports are listed below", False),
        ("; ignore_imports are listed below", False),
        ("    app.core.jobs -> app.api.deps", False),
        ("name = ignore_imports", False),
        ("name: ignore_imports", False),
        ("unmatched_ignore_imports_alerting = warn", False),
        ("unmatched_ignore_imports_alerting: warn", False),
    ],
)
def test_the_ignore_imports_key_is_matched_case_insensitively(line, expected):
    # Only the option key counts, read to the earliest of configparser's two
    # delimiters: a comment, a value, or an option whose name merely contains
    # the word is not the option.
    assert bb._is_ignore_imports(line) is expected


def test_a_block_on_a_structural_contract_is_refused():
    text = _edit_section(
        _empty_config(),
        "turn-layers",
        lambda lines: [*lines, bb.MARKER, bb.SUBHEAD],
    )
    with pytest.raises(bb.ConfigShapeError):
        bb.parse(text)


# ---------------------------------------------------------------------------
# C1/C2: the original recompute. A new violation is reported, and only frozen
# when somebody says so on purpose.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "files, entry",
    [
        (_c2_violation(), "app.api.routes.demo -> app.domain.bar.models"),
        (_c1_violation(), "app.core.jobs -> app.api.deps"),
    ],
    ids=["C2", "C1"],
)
def test_a_new_violation_needs_a_decision_then_freezes_on_request(
    tmp_path, files, entry
):
    config = _plant(tmp_path, _empty_config(), files)
    before = config.read_text()

    reported = _run(tmp_path)
    assert reported.returncode == bb.NEEDS_DECISION, reported.stdout + reported.stderr
    assert config.read_text() == before

    updated = _run(tmp_path, "--update")  # still refuses without --freeze-new
    assert updated.returncode == bb.NEEDS_DECISION, updated.stdout + updated.stderr
    assert config.read_text() == before

    frozen = _run(tmp_path, "--update", "--freeze-new")
    assert frozen.returncode == bb.OK, frozen.stdout + frozen.stderr
    written = config.read_text()
    assert entry in written

    # A second pass has nothing left to do.
    again = _run(tmp_path, "--update")
    assert again.returncode == bb.OK
    assert "unchanged" in again.stdout
    assert config.read_text() == written


def test_a_stale_entry_is_dropped_on_the_clean_tree(tmp_path):
    config_text = bb.write_baselines(
        _empty_config(),
        {
            "api-domain-core": ["app.core.zzz_gone -> app.core.nope_also_gone"],
            "routes-touch-no-models": [],
            "domains-acyclic": [],
            "routes-independent": [],
        },
    )
    config = _plant(tmp_path, config_text)

    dropped = _run(tmp_path, "--update")
    assert dropped.returncode == bb.OK, dropped.stdout + dropped.stderr
    assert "app.core.zzz_gone" not in config.read_text()

    again = _run(tmp_path, "--update")
    assert again.returncode == bb.OK
    assert "unchanged" in again.stdout


def test_dry_run_reports_and_writes_nothing(tmp_path):
    config = _plant(tmp_path, _empty_config(), _c2_violation())
    before = config.read_text()
    result = _run(tmp_path, "--update", "--dry-run", "--freeze-new")
    assert result.returncode == bb.OK
    assert "dry run" in result.stdout
    assert config.read_text() == before


# ---------------------------------------------------------------------------
# C3: a recomputed cut is never written — not even with --freeze-new — and a
# real new C3 violation is refused, never reported as unchanged.
# ---------------------------------------------------------------------------


def test_a_new_c3_violation_is_never_frozen(tmp_path):
    config = _plant(tmp_path, _empty_config(), _cycle_files())
    before = config.read_text()

    result = _run(tmp_path, "--update", "--freeze-new")
    assert result.returncode != bb.OK, result.stdout + result.stderr
    assert config.read_text() == before
    entries = bb.read_entries(config.read_text())[CYCLE]
    assert entries == [], "a recomputed C3 cut was written"


def test_the_c3_stale_pair_is_stale_to_the_real_checker(tmp_path, monkeypatch):
    # The premise of the three tests below: the checker really calls the bogus
    # pair unused while the cut is still needed, and a recompute names the
    # *other* cut of the same cycle. If either stopped holding, those tests
    # would silently stop exercising the drop or stop telling it apart from a
    # recompute.
    config = _plant(tmp_path, _c3_config(bogus=True), _cycle_files())
    monkeypatch.chdir(tmp_path)
    code, stale = bb.check_tree(config)
    assert code == bb.OK, "the pair plus the cut is not a passing baseline"
    assert stale[CYCLE] == [_BOGUS_C3_PAIR]

    recomputed = bb.recompute(_c3_config(bogus=True))[CYCLE]
    assert _REAL_C3_EDGE not in recomputed, (
        "the seed's recompute names the frozen cut, so these tests could not "
        "tell a stale-only drop apart from writing the recomputed cut"
    )


def test_a_stale_c3_entry_is_dropped_and_the_cut_kept(tmp_path):
    # The one path where C3 shrinks: `--update` drops the checker's stale entry
    # and keeps the frozen cycle-breaker. A recompute names the *other* cut, so
    # a refresh that wrote the recompute would replace the kept edge — which is
    # exactly what this pins down.
    config = _plant(tmp_path, _c3_config(bogus=True), _cycle_files())

    updated = _run(tmp_path, "--update")
    assert updated.returncode == bb.OK, updated.stdout + updated.stderr
    entries = bb.read_entries(config.read_text())[CYCLE]
    assert _BOGUS_C3_PAIR not in entries, "the stale C3 entry was not dropped"
    assert _REAL_C3_EDGE in entries, "a live C3 edge was dropped with the stale one"
    assert entries == [_REAL_C3_EDGE]

    again = _run(tmp_path, "--update")
    assert again.returncode == bb.OK, again.stdout + again.stderr
    assert "unchanged" in again.stdout
    assert bb.read_entries(config.read_text())[CYCLE] == [_REAL_C3_EDGE]


def test_c3_dry_run_reports_the_drop_and_writes_nothing(tmp_path):
    config = _plant(tmp_path, _c3_config(bogus=True), _cycle_files())
    before = config.read_text()

    result = _run(tmp_path, "--update", "--dry-run")
    assert result.returncode == bb.OK, result.stdout + result.stderr
    assert "dry run" in result.stdout
    assert _BOGUS_C3_PAIR in result.stdout
    assert config.read_text() == before


def test_a_refresh_that_would_not_converge_rolls_back(tmp_path):
    # A C1 pair that no longer matches anything is stale and the shrink drops it,
    # but the tree also holds a C3 cycle, so the rewritten file would be broken.
    # The update must not write a baseline that leaves the check red, and the
    # original must come back byte for byte.
    config_text = bb.write_baselines(
        _empty_config(),
        {
            "api-domain-core": ["app.core.zzz_gone -> app.core.nope_also_gone"],
            "routes-touch-no-models": [],
            "domains-acyclic": [],
            "routes-independent": [],
        },
    )
    config = _plant(tmp_path, config_text, _cycle_files())
    before = config.read_text()

    result = _run(tmp_path, "--update")
    assert result.returncode == bb.CANNOT_JUDGE, result.stdout + result.stderr
    assert "not converge" in result.stderr
    assert config.read_text() == before, "the original was not restored precisely"


def test_a_second_measure_that_disagrees_rolls_back(tmp_path, monkeypatch):
    # The other half of "converges": a rewrite nobody can re-measure to the same
    # answer is a rewrite nobody can reason about, even if the check happens to
    # be green. This walks the real main() with only the second measurement
    # perturbed, and requires the original file back.
    config = _plant(tmp_path, _empty_config(), _c2_violation())
    before = config.read_text()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--config",
            str(tmp_path / ".importlinter"),
            "--update",
            "--freeze-new",
        ],
    )

    real_measure = bb.measure
    calls = {"n": 0}

    def drifting(text, stale):
        calls["n"] += 1
        result = real_measure(text, stale)
        if calls["n"] >= 2:
            result = {
                **result,
                CYCLE: [*result[CYCLE], "app.domain.drifted -> app.domain.apart"],
            }
        return result

    monkeypatch.setattr(bb, "measure", drifting)
    code = bb.main()
    assert calls["n"] == 2, "measure must be run once to decide and once to verify"
    assert code == bb.CANNOT_JUDGE
    assert config.read_text() == before


# ---------------------------------------------------------------------------
# The real repository: the C3 recompute would drop live edges. The update must
# keep every one of them. No count is hard-coded — the set is recomputed here,
# so the guard survives the tree changing under it.
# ---------------------------------------------------------------------------


def test_the_update_keeps_c3_edges_a_recompute_would_drop(monkeypatch):
    monkeypatch.chdir(BACKEND)
    text = REAL_CONFIG.read_text()
    frozen = set(bb.read_entries(text)[CYCLE])

    recomputed = set(bb.recompute(text)[CYCLE])
    _, stale = bb.check_tree(REAL_CONFIG)
    stale_c3 = set(stale.get(CYCLE, ()))

    would_drop = frozen - recomputed - stale_c3
    assert would_drop, (
        "the recompute and the frozen set agree today, so this regression has "
        "nothing to guard — re-derive it before deleting it"
    )

    new = bb.measure(text, stale)
    assert would_drop <= set(new[CYCLE]), "the update dropped a live C3 edge"
    assert set(new[CYCLE]) - frozen == set(), "the update added a C3 edge"


def test_the_real_baseline_is_already_stable(monkeypatch):
    monkeypatch.chdir(BACKEND)
    text = REAL_CONFIG.read_text()
    _, stale = bb.check_tree(REAL_CONFIG)
    new = bb.measure(text, stale)
    for contract_id, entries in bb.read_entries(text).items():
        assert new[contract_id] == entries, contract_id
