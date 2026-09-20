"""Native CLI calls receive project credentials without changing shell state."""

import contextlib
import hashlib
import io
import json
import os
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from app.domain.agent import forge_cli


@pytest.fixture
def invocation(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    captured = tmp_path / "captured.json"
    binary = tmp_path / "native"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "data = {'args': sys.argv[1:], 'token': os.environ.get('GH_TOKEN'), "
        "'repo': os.environ.get('GH_REPO'), 'stdin': sys.stdin.read(), "
        "'proxy': os.environ.get('HTTPS_PROXY'), "
        "'no_proxy': os.environ.get('NO_PROXY')}\n"
        "if 'XDG_DATA_HOME' in os.environ:\n"
        "    path = Path(os.environ['XDG_DATA_HOME']) / 'forgejo-cli/keys.json'\n"
        "    if os.environ.get('CHEESE_TEST_NATIVE_KEYS_FILE'):\n"
        "        path = Path(os.environ['CHEESE_TEST_NATIVE_KEYS_FILE'])\n"
        "    data.update(keys=json.loads(path.read_text()), path=str(path), "
        "mode=path.stat().st_mode & 511)\n"
        f"Path({str(captured)!r}).write_text(json.dumps(data))\n"
        "sys.exit(7)\n"
    )
    binary.chmod(0o700)
    monkeypatch.setattr(forge_cli, "native_binary", lambda _: str(binary))
    data = {
        "kind": "github_app",
        "project_id": "project-id",
        "url": "https://github.com/team/project.git",
        "api_url": "https://forge.invalid/subpath/api/v1",
        "repo": "team/project",
        "token": "ephemeral-test-token",
    }
    monkeypatch.setattr(forge_cli, "credentials", lambda: data)
    monkeypatch.setattr(forge_cli, "fj_path_transport", contextlib.nullcontext)
    monkeypatch.setattr(
        forge_cli.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO()
    )
    # A real subprocess reads inherited stdin. /dev/null is sufficient here;
    # the launcher must neither consume it nor replace native argument parsing.
    monkeypatch.setattr(forge_cli.subprocess, "call", _with_stdin)
    return data, captured


_native_call = forge_cli.subprocess.call


def _with_stdin(args, **kwargs):
    with open(os.devnull) as stream:
        return _native_call(args, stdin=stream, **kwargs)


def test_gh_forwards_arguments_exit_status_and_mints_again(invocation, monkeypatch):
    data, captured = invocation
    monkeypatch.setenv("GH_TOKEN", "expired-shell-token")
    args = ["api", "repos/team/project/issues", "--jq", ".[0].title"]
    assert forge_cli.run("gh", args) == 7
    got = json.loads(captured.read_text())
    assert got["args"] == args
    assert got["token"] == "ephemeral-test-token"
    assert got["repo"] == "github.com/team/project"
    data["token"] = "renewed-test-token"
    assert forge_cli.run("gh", args) == 7
    assert json.loads(captured.read_text())["token"] == "renewed-test-token"
    assert os.environ["GH_TOKEN"] == "expired-shell-token"


@pytest.mark.parametrize("system", ["linux", "darwin"])
def test_fj_uses_private_temporary_native_config_and_removes_it(
    invocation, monkeypatch, system
):
    data, captured = invocation
    data["kind"] = "forgejo"
    data["url"] = "https://forge.invalid/subpath/team/project.git"
    data["api_url"] = "http://internal-forge:3000/api/v1"
    monkeypatch.setattr(forge_cli.sys, "platform", system)
    if system == "darwin":
        monkeypatch.setenv(
            "CHEESE_TEST_NATIVE_KEYS_FILE",
            str(
                Path.home()
                / "Library/Application Support/forgejo-cli.forgejo-cli/keys.json"
            ),
        )
    assert forge_cli.run("fj", ["pr", "list"]) == 7
    got = json.loads(captured.read_text())
    assert got["keys"] == {
        "hosts": {
            "forge.invalid/subpath": {"type": "Application", "token": data["token"]}
        }
    }
    assert got["mode"] == 0o600
    assert not Path(got["path"]).exists()
    assert not (
        Path.home() / "Library/Application Support/forgejo-cli.forgejo-cli/keys.json"
    ).is_symlink()


def test_wrong_provider_cannot_receive_the_other_providers_token(invocation):
    with pytest.raises(RuntimeError, match="uses gh"):
        forge_cli.run("fj", ["pr", "list"])


def test_gh_offline_keeps_native_arguments_and_uses_tunnel(invocation, monkeypatch):
    _, captured = invocation
    monkeypatch.setenv("CHEESE_API", "https://platform.invalid/api")
    monkeypatch.setenv("CHEESE_TOKEN", "scoped-test-token")
    monkeypatch.setenv("HTTPS_PROXY", "http://unreachable.invalid")

    def offline(*args, **kwargs):
        raise forge_cli.urllib.error.URLError("unreachable")

    monkeypatch.setattr(forge_cli.urllib.request, "urlopen", offline)
    monkeypatch.setattr(
        forge_cli.runpy,
        "run_path",
        lambda *_: {
            "handle_connection": lambda *_: None,
        },
    )
    assert forge_cli.run("gh", ["run", "rerun", "123"]) == 7
    data = json.loads(captured.read_text())
    assert data["args"] == ["run", "rerun", "123"]
    assert data["repo"] == "github.com/team/project"
    assert data["proxy"].startswith("http://127.0.0.1:")
    assert data["no_proxy"] == ""
    assert os.environ["HTTPS_PROXY"] == "http://unreachable.invalid"


@pytest.mark.parametrize(
    "prefix",
    [[], ["--host", "https://forge.invalid"], ["--host=https://forge.invalid"]],
)
def test_fj_offline_uses_relay_without_replaying_the_command(
    invocation, monkeypatch, prefix
):
    data, captured = invocation
    data.update(kind="forgejo", url="https://forge.invalid/team/project.git")
    monkeypatch.setenv("CHEESE_API", "https://platform.invalid/api")

    def offline(*args, **kwargs):
        raise forge_cli.urllib.error.URLError("offline")

    monkeypatch.setattr(forge_cli.urllib.request, "urlopen", offline)
    assert forge_cli.run("fj", [*prefix, "pr", "create"]) == 7
    got = json.loads(captured.read_text())
    relay = "https://platform.invalid/api/sandbox/forge/project-id/"
    assert got["args"] == (
        ["--host=" + relay, "pr", "create"]
        if prefix and "=" in prefix[0]
        else ["--host", relay, "pr", "create"]
    )
    assert got["keys"]["hosts"] == {
        "platform.invalid/api/sandbox/forge/project-id": {
            "type": "Application",
            "token": data["token"],
        }
    }
    assert not Path(got["path"]).exists()


@pytest.mark.parametrize(
    "system,tool",
    [("linux", "fj"), ("linux", "gh"), ("darwin", "gh"), ("darwin", "fj")],
)
def test_missing_binary_downloads_verified_archive_and_reuses_cache(
    monkeypatch, tmp_path, system, tool
):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(os, "get_exec_path", lambda: [])
    monkeypatch.setattr(forge_cli.sys, "platform", system)
    monkeypatch.setattr(forge_cli.platform, "machine", lambda: "arm64")
    monkeypatch.setenv("CHEESE_API", "https://platform.invalid/api")
    payload = b"#!/bin/sh\nexit 0\n"
    archive = io.BytesIO()
    if system == "darwin" and tool == "gh":
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr(f"release/bin/{tool}", payload)
    else:
        with tarfile.open(fileobj=archive, mode="w:gz") as package:
            member = tarfile.TarInfo(tool if tool == "fj" else f"release/bin/{tool}")
            member.size = len(payload)
            package.addfile(member, io.BytesIO(payload))
    content = archive.getvalue()
    calls = []

    def download(url, timeout):
        calls.append(url)
        response = io.BytesIO(content)
        response.headers = {"X-Checksum-SHA256": hashlib.sha256(content).hexdigest()}
        return response

    monkeypatch.setattr(forge_cli.urllib.request, "urlopen", download)
    binary = Path(forge_cli.native_binary(tool))
    assert binary.read_bytes() == payload
    assert binary.stat().st_mode & 0o777 == 0o700
    assert forge_cli.native_binary(tool) == str(binary)
    assert calls == [
        f"https://platform.invalid/api/connector/toolchain/{tool}/{system}-arm64/artifact"
    ]


def test_bad_checksum_never_installs_binary(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(os, "get_exec_path", lambda: [])
    monkeypatch.setattr(forge_cli.sys, "platform", "linux")
    monkeypatch.setattr(forge_cli.platform, "machine", lambda: "aarch64")
    monkeypatch.setenv("CHEESE_API", "https://platform.invalid/api")
    response = io.BytesIO(b"corrupt archive")
    response.headers = {"X-Checksum-SHA256": "0" * 64}
    monkeypatch.setattr(forge_cli.urllib.request, "urlopen", lambda *a, **kw: response)
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        forge_cli.native_binary("fj")
    assert not (tmp_path / ".cheese/native/fj-0.6.0/fj").exists()
