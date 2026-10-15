"""Where a room's package downloads come from, and when they come from a mirror.

Two things are asked here. One is the promise: a tool that installs something
in a room reaches its official source — exactly where it would have without
this feature — whenever a mirror cannot serve it, and a domestic mirror when
one can. The other is that the promise is kept in both places it has to be:
the environment an agent's tools inherit, which the executor builds on the
machine (`bootstrap.prepared`), and the launcher script a device runs
(`machine_launcher.launch_script`). Those cannot share code, so the tests hold
the two renderings to each other rather than to a second copy of the table.
"""

import contextlib
import os
import re
import shutil
import subprocess
import sys
import types
import urllib.request
import uuid

import pytest

from app.core.config import settings
from app.domain.agent import package_mirrors as mirrors
from app.domain.agent.harness.claude_code.remote_execution import bootstrap
from app.domain.agent.harness.claude_code.remote_execution.launch import payload_for

# What a room is given by default, with the one mirror no deployment names
# filled in: the default configuration leaves `uv_python` empty on purpose
# (those builds are GitHub release assets, and the mirrors carrying them are
# third-party proxies), and a test that could not tell "named but empty" from
# "not named" would not be checking that.
_CONFIG = {
    "pypi": "https://mirror.test/simple",
    "pypi_official": "https://pypi.org/simple",
    "npm": "https://npm.test",
    "playwright": "https://browser.test/binaries/playwright",
    "uv_python": "https://python.test/cpython",
}


def _names(key):
    """Every variable one mirror exports, in the order the table lists them."""
    for mirror in mirrors.MIRRORS:
        if mirror.key == key:
            return [name for name, _ in mirror.exports]
    raise AssertionError(f"no mirror named {key!r}")


_ALL_NAMES = [name for mirror in mirrors.MIRRORS for name in _names(mirror.key)]
_DEAD = ""


def _answers_except(dead):
    """The probe: everything answers except `dead`."""

    def answers(url):
        if dead and dead in url:
            return False
        return True

    return answers


def _mirror_environment(monkeypatch, config, dead=_DEAD, **kwargs):
    """`environment()` with the network replaced by the answer under test."""
    monkeypatch.setattr(mirrors, "_answers", _answers_except(dead))
    return mirrors.environment(config, **kwargs)


def _the_machine_answers(monkeypatch, dead=""):
    """This machine's network, replaced by the answer under test.

    A prepare reads the mirror table through `runpy`, which runs the file
    again: patching `mirrors._answers` leaves that copy probing for real, on
    whatever network the machine running the tests happens to be on. What both
    copies share is the module the probe is made with, so that is what is
    replaced. The URLs asked are returned, because a prepare that asked nothing
    and one that asked and got no answer look the same from the outside.
    """
    asked = []

    def urlopen(request, timeout=None):
        asked.append(request.full_url)
        if dead and dead in request.full_url:
            raise OSError("no answer")
        return contextlib.nullcontext()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return asked


def _room_payload():
    """The payload the backend sends a room, as `launch.payload_for` builds it."""
    return payload_for(
        uuid.uuid4(),
        uuid.uuid4(),
        {"CHEESE_API": "http://127.0.0.1:1", "CHEESE_TOKEN": "test"},
        sandbox=False,
        platform_machine=False,
    )


def _stub_curl(tmp_path, codes):
    """A `curl` on PATH that answers with `codes` by URL, and logs its calls.

    The one question a test cannot ask the internet is whether a mirror answers
    right now, so the machine's curl is replaced by this. It reads the URL out
    of whatever the launcher passes (`--head -o /dev/null ... <url>`) and
    nothing more, and it logs, because how MANY probes a call makes is half of
    what is being pinned.
    """
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "curl.log"
    cases = "".join(
        f"  {url.rstrip('/')}/*) exit {code};;\n" for url, code in codes.items()
    )
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        'url=""\n'
        'for a in "$@"; do case "$a" in http*) url="$a";; esac; done\n'
        f'printf "%s\\n" "$url" >> "{log}"\n'
        'case "$url" in\n'
        f"{cases}"
        "esac\n"
        "exit 0\n"
    )
    curl.chmod(0o755)
    return bin_dir, log


def _run_shell_block(tmp_path, block, *, env=None, codes=None):
    """Run a rendered block the way the launcher runs it and report what it
    leaves behind.

    An unset variable reports as an empty value, never as a missing key, so
    "did this export anything" is only ever a question about the value.
    """
    bin_dir, _log = _stub_curl(tmp_path, codes or {})
    dump = tmp_path / "env.dump"
    probe = "".join(
        f'printf "%s=%s\\n" {name} "${{{name}:-}}" >> "{dump}"\n' for name in _ALL_NAMES
    )
    result = subprocess.run(
        ["sh", "-c", f"{block}\n{probe}"],
        env={
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            **(env or {}),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    seen = dict(
        line.split("=", 1) for line in dump.read_text().splitlines() if "=" in line
    )
    return {name: value for name, value in seen.items() if value}


def test_a_missing_package_still_resolves_from_the_official_index(monkeypatch):
    """The fallback, in the two programs that cannot share a fallback.

    pip and uv both get a second index, because neither reads the other's
    variables — uv ignores `PIP_*` outright, so an `UV_INDEX` alone would leave
    uv with a mirror and nothing behind it, and a mirror that has not synced a
    release would be a package uv cannot install at all.

    uv's own strategy is what makes the second index *reachable*: its default
    takes the first index that has the package NAME, so a mirror holding the
    project but not the release still wins and still fails.
    """
    seen = _mirror_environment(monkeypatch, _CONFIG)

    for name in ("UV_DEFAULT_INDEX", "PIP_EXTRA_INDEX_URL"):
        assert seen[name] == _CONFIG["pypi_official"], name
    assert seen["UV_INDEX"] == seen["PIP_INDEX_URL"] == _CONFIG["pypi"]
    assert seen["UV_INDEX_STRATEGY"] == "unsafe-first-match"


@pytest.mark.parametrize("key", ["pypi", "npm", "playwright", "uv_python"])
def test_a_mirror_that_does_not_answer_is_not_used(monkeypatch, key):
    """The failure this exists for: a mirror that is simply not there.

    npm, `playwright install` and `uv python install` take one address each and
    have no second one inside them, so a mirror named but unreachable there is
    a room that cannot install anything — worse than the traffic it saves. The
    answer is to export nothing and let that tool be on its official source,
    which is where it was before any of this.
    """
    dead = mirrors._probe_url(_CONFIG[key])
    seen = _mirror_environment(monkeypatch, _CONFIG, dead=dead)

    for name in _names(key):
        assert name not in seen, name
    for mirror in mirrors.MIRRORS:
        if mirror.key == key:
            continue
        for name in _names(mirror.key):
            if name in ("UV_DEFAULT_INDEX", "PIP_EXTRA_INDEX_URL"):
                # The official index, which the mirror named it anyway.
                continue
            assert name in seen, name


def test_nothing_is_exported_when_no_mirror_answers(monkeypatch):
    """Every mirror down is the behaviour of the room before this feature, and
    it has to stay reachable: the room installs, slowly, from the official
    sources rather than not at all."""
    seen = _mirror_environment(monkeypatch, _CONFIG, dead=".")

    assert seen == {}


def test_a_mirror_nobody_named_exports_nothing(monkeypatch):
    """An empty address is a decision not to use that mirror — how the default
    configuration leaves `UV_PYTHON_INSTALL_MIRROR` — and not a reason to
    export a variable pointing at nothing."""
    config = {**_CONFIG, "uv_python": ""}
    seen = _mirror_environment(monkeypatch, config)

    assert "UV_PYTHON_INSTALL_MIRROR" not in seen
    assert "UV_PYTHON_INSTALL_MIRROR" not in mirrors.governed(config)


def test_the_feature_can_be_turned_off(monkeypatch):
    """A deployment that wants the old behaviour gets precisely it: no probe,
    and not one variable.

    The addresses are still named, and that is the half that reaches a room
    that is already running: a room carrying a mirror from before the switch
    can only be told to drop it by name.
    """
    probes = []

    def probe(url):
        probes.append(url)
        return True

    monkeypatch.setattr(mirrors, "_answers", probe)
    off = types.SimpleNamespace(
        package_mirror_enabled=False,
        package_mirror_pypi_index=_CONFIG["pypi"],
        package_mirror_pypi_fallback=_CONFIG["pypi_official"],
        package_mirror_npm_registry=_CONFIG["npm"],
        package_mirror_playwright_host=_CONFIG["playwright"],
        package_mirror_uv_python=_CONFIG["uv_python"],
    )
    config = mirrors.configuration(off)

    assert mirrors.environment(config) == {}
    assert mirrors.shell(config) == ""
    assert probes == []
    assert set(mirrors.governed(config)) == set(_ALL_NAMES)


def test_a_name_is_governed_whether_or_not_its_mirror_answers(monkeypatch):
    """What a room must stop using is not a question about the probe: a mirror
    that stopped answering is exactly the case the names exist for, and the
    room still carrying its address is the one that has to be told."""
    monkeypatch.setattr(mirrors, "_answers", _answers_except("."))

    assert mirrors.environment(_CONFIG) == {}
    assert set(mirrors.governed(_CONFIG)) == set(_ALL_NAMES)

    # An address nobody set is not governed: there is nothing to take away.
    unnamed = {**_CONFIG, "uv_python": ""}

    assert "UV_PYTHON_INSTALL_MIRROR" not in mirrors.governed(unnamed)


def test_a_probe_is_paid_for_once_and_not_once_per_tool_call(monkeypatch, tmp_path):
    """The executor prepares on every tool call it serves, so this runs there.

    Probing each time would be a probe per call, and a room whose network is
    gone would pay every probe's whole budget on every call. The answer is kept
    under the room's store instead, and the next prepare reads it.
    """
    probes = []

    def counting(url):
        probes.append(url)
        return True

    monkeypatch.setattr(mirrors, "_answers", counting)
    first = mirrors.environment(_CONFIG, tmp_path)
    second = mirrors.environment(_CONFIG, tmp_path)

    assert first == second
    assert len(probes) == len(set(probes)), "one probe per mirror, not per prepare"
    assert (tmp_path / "package-mirrors.json").is_file()


def test_an_answer_is_forgotten_once_it_is_old(monkeypatch, tmp_path):
    """A mirror that goes down must stop taking a room's installs with it, and
    an answer kept forever would never notice."""
    probes = []

    def counting(url):
        probes.append(url)
        return True

    monkeypatch.setattr(mirrors, "_answers", counting)
    mirrors.environment(_CONFIG, tmp_path, now=0.0)
    before = len(probes)
    mirrors.environment(_CONFIG, tmp_path, now=mirrors.ANSWER_KEPT_S + 1)

    assert len(probes) == before * 2


def test_an_answer_about_other_addresses_is_probed_again(monkeypatch, tmp_path):
    """A deployment that moves a mirror — a different registry, an internal
    index — must not be answered out of what the last one said."""
    probes = []

    def counting(url):
        probes.append(url)
        return True

    monkeypatch.setattr(mirrors, "_answers", counting)
    mirrors.environment(_CONFIG, tmp_path)
    before = len(probes)
    mirrors.environment({**_CONFIG, "npm": "https://npm2.test"}, tmp_path)

    assert len(probes) > before


def test_the_launcher_and_the_executor_export_the_same_variables(monkeypatch):
    """The same policy, rendered twice by code that cannot share any.

    Compared as pairs rather than as names, because a mirror in the environment
    and the same name pointed somewhere else in the launcher is exactly the
    drift a table is supposed to prevent.
    """
    monkeypatch.setattr(mirrors, "_answers", _answers_except(_DEAD))
    environment = mirrors.environment(_CONFIG)
    block = mirrors.shell(_CONFIG)

    assert set(re.findall(r"export (\w+)=", block)) == set(environment)
    for name, value in environment.items():
        assert f"export {name}='{value}'" in block, name


def test_the_launcher_uses_a_mirror_only_when_it_answers(tmp_path):
    """The probe is a property of the machine, which is why the launcher makes
    it there — the backend that builds this script is not on the network of
    every machine it launches on."""
    block = mirrors.shell(_CONFIG)
    alive = _run_shell_block(
        tmp_path,
        block,
        codes={
            _CONFIG["npm"]: 7,
            _CONFIG["pypi"]: 22,
            _CONFIG["playwright"]: 0,
            _CONFIG["uv_python"]: 6,
        },
    )

    # 22 is a mirror refusing the HEAD: it is there, and a browser mirror
    # answers exactly that for the directory above its browsers.
    assert alive["PIP_INDEX_URL"] == _CONFIG["pypi"]
    assert alive["PLAYWRIGHT_DOWNLOAD_HOST"] == _CONFIG["playwright"]
    # 7 and 6 are a mirror that is not there.
    assert "npm_config_registry" not in alive
    assert "UV_PYTHON_INSTALL_MIRROR" not in alive


def test_the_launcher_keeps_what_the_machine_already_had(tmp_path):
    """A project's own setup scripts run under this, and a project that names
    an index of its own keeps it — the same rule the executor follows."""
    seen = _run_shell_block(
        tmp_path,
        mirrors.shell(_CONFIG),
        env={"PIP_INDEX_URL": "https://internal.test/simple"},
    )

    assert seen["PIP_INDEX_URL"] == "https://internal.test/simple"
    assert seen["UV_INDEX"] == _CONFIG["pypi"]


def test_the_launcher_probes_without_a_curl_and_launches_anyway(tmp_path):
    """A machine without curl gets no mirrors and no failure. `set -e` is in
    force in the launcher, so a probe that cannot run has to be survivable by
    construction rather than by luck."""
    block = mirrors.shell(_CONFIG)
    bin_dir = tmp_path / "emptybin"
    bin_dir.mkdir()
    dump = tmp_path / "env.dump"
    result = subprocess.run(
        # By its absolute path: this PATH is the machine's, and it has no sh.
        [shutil.which("sh"), "-c", f"set -e\n{block}\nprintf ok > {dump}"],
        env={"PATH": str(bin_dir)},
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert dump.read_text() == "ok"


def test_a_device_launch_carries_the_mirrors(tmp_path):
    """The launcher half, out of the script a device actually runs.

    The launcher's own downloads are not the agent's and not the same
    environment, but a launch that fetched its toolchain and a project's
    dependencies over the public internet while the room's installs went to a
    mirror would be half of the traffic this is for.
    """
    from app.domain.agent import machine_launcher

    script = machine_launcher.launch_script(command='"$AGENT"')
    start = script.index("# Where this room's package downloads come from.")
    closing = script.index("\nfi\n", script.index("if command -v curl", start))
    seen = _run_shell_block(tmp_path, script[start : closing + 4], codes={})

    assert seen["UV_INDEX"] == settings.package_mirror_pypi_index
    assert seen["PIP_EXTRA_INDEX_URL"] == settings.package_mirror_pypi_fallback
    assert seen["npm_config_registry"] == settings.package_mirror_npm_registry


def test_a_prepared_room_hands_its_agent_the_mirrors(tmp_path, monkeypatch):
    """The end of the chain, and the point of the whole thing.

    An agent's Bash does not inherit the launcher's environment: it gets the
    environment the executor computed for it, so a mirror that only the
    launcher set would not be what `pip install` in a room goes through. This
    is that environment, built from the payload the backend sends, out of the
    probes this machine's own network answers.
    """
    payload = _room_payload()
    assert payload["package_mirrors"] == mirrors.configuration(settings)
    monkeypatch.setattr(bootstrap, "binary", lambda *_: sys.executable)
    asked = _the_machine_answers(monkeypatch)

    with bootstrap.prepared(payload, tmp_path, fetch_toolchain=False) as (
        _home,
        config,
        _state,
        _env,
    ):
        seen = config["env"]
        assert asked, "the prepare asks this machine which mirrors answer"
        assert seen["UV_INDEX"] == settings.package_mirror_pypi_index
        assert seen["PIP_EXTRA_INDEX_URL"] == settings.package_mirror_pypi_fallback
        assert seen["npm_config_registry"] == settings.package_mirror_npm_registry
        # The payload key is the platform's own, not a variable: it carries the
        # addresses to the executor and stops there.
        assert not [name for name in seen if "package_mirrors" in name]
        assert "npm_config_registry" in config["env_reset"]


def test_a_mirror_that_stopped_answering_is_named_for_removal(tmp_path, monkeypatch):
    """The half a variable cannot state on its own.

    A room prepares on every tool call it serves, and a prepare adds to what
    the room already has, so a mirror that stopped answering cannot be taken
    away by leaving its variable out: the room is carrying it. The name is
    stated instead, and the executor drops it (`runtime`, `configure`).
    """
    payload = _room_payload()
    monkeypatch.setattr(bootstrap, "binary", lambda *_: sys.executable)
    dead = settings.package_mirror_npm_registry
    asked = _the_machine_answers(monkeypatch, dead=dead)

    with bootstrap.prepared(payload, tmp_path, fetch_toolchain=False) as (
        _home,
        config,
        _state,
        _env,
    ):
        assert dead.rstrip("/") + "/" in asked
        assert "npm_config_registry" not in config["env"]
        assert "npm_config_registry" in config["env_reset"]
        assert config["env"]["UV_INDEX"] == settings.package_mirror_pypi_index
