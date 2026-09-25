"""Exercise the executor through its public process/socket protocol."""

import ast
import base64
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

if __package__:
    from tests.pinned_claude import claude_binary
else:
    # The acceptance suite runs this file as a script, from outside the package.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from pinned_claude import claude_binary

RUNTIME = (
    Path(__file__).resolve().parents[2]
    / "app/domain/agent/harness/claude_code/remote_execution/runtime.py"
)
spec = importlib.util.spec_from_file_location("execution_runtime", RUNTIME)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


@pytest.mark.parametrize("running", [False, True])
def test_only_a_running_command_holds_an_idle_upgrade(tmp_path, running):
    executor = runtime.Executor.__new__(runtime.Executor)
    executor.state = tmp_path
    executor.config = {"claude": "old"}
    executor.admission_lock = threading.Lock()
    executor.active_calls = 0
    executor.upgrading = False
    executor.running = {"shell-1": {}} if running else {}

    result = executor.dispatch("begin_upgrade", {"release": "next"})

    assert result["ready"] is not running
    assert executor.upgrading is not running


def _proxy_source() -> str:
    return (
        (RUNTIME.parent / "proxy.js")
        .read_text()
        .replace(
            "__EXECUTION_CONFIG__",
            json.dumps(
                {
                    "central_config": "/config",
                    "central_workspace": "/view",
                    "workspace": "/work",
                    "session_workspace": "/work",
                }
            ),
        )
    )


def _run_proxy(program: str) -> None:
    subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            program,
            base64.b64encode(_proxy_source().encode()).decode(),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_unavailable_search_tools_do_not_search_the_session_host():
    _run_proxy("""
        import assert from 'node:assert/strict';
        const url = 'data:text/javascript;base64,' + process.argv[1];
        const {register} = await import(url);
        const handlers = {};
        register((event, handler) => {handlers[event] = handler});
        for (const tool of ['Glob', 'Grep']) {
          const result = await handlers['tool.call']({}, {
            tool, tool_use_id: 'search', path: '/work', pattern: 'private',
          }, () => {throw new Error('searched session host')});
          assert.match(result.deny, /use Bash/);
        }
    """)


def test_isolated_subagents_are_refused_with_the_way_that_works():
    _run_proxy("""
        import assert from 'node:assert/strict';
        const url = 'data:text/javascript;base64,' + process.argv[1];
        const {register} = await import(url);
        const handlers = {};
        register((event, handler) => {handlers[event] = handler});
        for (const isolation of ['worktree', 'remote']) {
          const result = await handlers['tool.call']({}, {
            tool: 'Agent', tool_use_id: 'spawn', description: 'look',
            prompt: 'list files', isolation,
          }, () => {throw new Error('spawned on the session host')});
          assert.match(result.deny, /omit isolation/);
          assert.match(result.deny, /cheese_task/);
        }
        const spawned = await handlers['tool.call']({}, {
          tool: 'Agent', tool_use_id: 'spawn', description: 'look',
          prompt: 'list files',
        }, (event) => ({spawned: event.tool}));
        assert.deepEqual(spawned, {spawned: 'Agent'});
    """)


def test_offline_work_does_not_prevent_session_bootstrap(tmp_path):
    from app.domain.agent.executor_transport import MachineOutOfReach, RemoteClient
    from app.domain.agent.harness.claude_code.remote_execution.client import (
        prepare,
        sync_context,
    )

    launch = prepare(
        tmp_path,
        {"kind": "unavailable", "workspace": "/unavailable-project"},
        claude=claude_binary(),
    )
    target = json.loads(Path(launch["execution"]).read_text())
    assert Path(launch["cwd"]).is_dir()
    assert "平台工具可用" in sync_context(launch["execution"])["instructions"]
    with pytest.raises(MachineOutOfReach):
        RemoteClient(target).call(
            "invoke", {"tool": "Bash", "args": {"command": "touch forbidden"}}
        )
    assert not (Path(launch["cwd"]) / "forbidden").exists()


def test_subagent_identity_is_not_forwarded_as_a_tool_argument():
    _run_proxy("""
        import assert from 'node:assert/strict';
        const url = 'data:text/javascript;base64,' + process.argv[1];
        const {register} = await import(url);
        const handlers = {};
        register((event, handler) => {handlers[event] = handler});
        let called;
        const api = {
          session: {id: async () => 'session'},
          mcp: {call: async (server, tool, args) => {
            called = {server, tool, args};
            const text = JSON.stringify({result: {ok: true}});
            return {content: [{type: 'text', text}]};
          }},
        };
        await handlers['tool.call'](api, {
          tool: 'Read', tool_use_id: 'read', agentId: 'child',
          file_path: '/work/image.png',
        });
        assert.deepEqual(called, {server: 'native', tool: 'invoke', args: {
          id: 'read', tool: 'Read', args: {file_path: '/work/image.png'},
          session_id: 'session',
        }});
        // A skill's file, which the build found in this host's config
        // directory, is the project's on the executor.
        await handlers['tool.call'](api, {
          tool: 'Read', tool_use_id: 'skill',
          file_path: '/config/skills/check/SKILL.md',
        });
        assert.deepEqual(called.args.args,
          {file_path: '/work/.claude/skills/check/SKILL.md'});
    """)


def test_a_platform_receipt_never_becomes_an_empty_text_block():
    """`platform_request`'s receipt is always `{"stdout": body, "stderr": ""}`,
    and the plugin used to turn BOTH halves into a `text` block unconditionally.
    The empty one is not harmless padding: a provider that validates text
    content rejects the whole request over it (Moonshot's Anthropic endpoint
    answers 400 "Invalid request: text content is empty"), and the block then
    stays in the conversation — so one platform call wedges every later turn in
    that room. Nothing else about the receipt changes."""
    _run_proxy("""
        import assert from 'node:assert/strict';
        const url = 'data:text/javascript;base64,' + process.argv[1];
        const {register} = await import(url);
        const handlers = {};
        register((event, handler) => {handlers[event] = handler});
        const body = '{"code":200,"message":"ok","data":null}';
        const receipt = {result: {stdout: body, stderr: ''}};
        const api = {
          session: {id: async () => 'session'},
          mcp: {call: async () => ({
            content: [{type: 'text', text: JSON.stringify(receipt)}],
          })},
        };
        const call = {tool: 'mcp__native__platform_request', tool_use_id: 'req',
                      method: 'GET', path: '/topics/x/doc'};
        assert.deepEqual(await handlers['tool.call'](api, call), {
          result: [{type: 'text', text: '{"code":200,"message":"ok","data":null}'}],
        });
        receipt.result = {stdout: '', stderr: ''};
        assert.deepEqual(await handlers['tool.call'](api, call), {result: []});
        receipt.result = {stdout: 'out', stderr: 'err'};
        assert.deepEqual(await handlers['tool.call'](api, call), {
          result: [{type: 'text', text: 'out'}, {type: 'text', text: 'err'}],
        });
    """)


def test_large_platform_receipt_reaches_the_caller_as_json():
    _run_proxy("""
        import assert from 'node:assert/strict';
        const url = 'data:text/javascript;base64,' + process.argv[1];
        const {register} = await import(url);
        const handlers = {};
        register((event, handler) => {handlers[event] = handler});
        const body = JSON.stringify({data: 'x'.repeat(180000)});
        const receipt = {result: {stdout: body, stderr: ''}};
        const api = {
          session: {id: async () => 'session'},
          mcp: {call: async () => ({content: [{type: 'text', text:
            JSON.stringify({receipt_path: '/config/tool-results/large.json'})}]})},
          fs: {read: async (path, {as}) => {
            assert.equal(path, '/config/tool-results/large.json');
            assert.equal(as, 'text');
            return JSON.stringify(receipt);
          }},
        };
        const call = {tool: 'mcp__native__platform_request', tool_use_id: 'req',
                      method: 'GET', path: '/projects/x/alerts'};
        assert.deepEqual(await handlers['tool.call'](api, call), {
          result: [{type: 'text', text: body}],
        });
    """)


def test_large_edit_receipt_reaches_the_caller_without_replaying_the_edit():
    _run_proxy("""
        import assert from 'node:assert/strict';
        const url = 'data:text/javascript;base64,' + process.argv[1];
        const {register} = await import(url);
        const handlers = {};
        register((event, handler) => {handlers[event] = handler});
        const result = {result: {originalFile: 'large file\\n'.repeat(20000)}};
        let calls = 0;
        const api = {
          session: {id: async () => 'session'},
          mcp: {call: async () => {
            calls++;
            return {content: [{type: 'text', text: JSON.stringify({
              receipt_path: '/config/tool-results/result.json',
            })}]};
          }},
          fs: {read: async (path, {as}) => {
            assert.equal(path, '/config/tool-results/result.json');
            assert.equal(as, 'text');
            return JSON.stringify(result);
          }},
        };
        const answer = await handlers['tool.call'](api, {
          tool: 'Edit', tool_use_id: 'edit', file_path: '/work/large.txt',
          old_string: 'before', new_string: 'after',
        });
        assert.deepEqual(answer, result);
        assert.equal(calls, 1);
    """)


def test_the_build_runs_bash_and_its_own_tasks_and_its_words_reach_the_model():
    """Bash, TaskStop and a read of the build's own output files are the
    build's to run (the shell prefix carries Bash to the executor); only the
    file tools go to the executor. What the build writes about a command
    reaches the model as the build wrote it: the session sees the project at
    the executor's path, so there is nothing to respell."""
    source = _proxy_source().replace(
        '"workspace": "/work"', '"workspace": "/work", "central_tmp": "/session-tmp"'
    )
    subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            """
        import assert from 'node:assert/strict';
        const url = 'data:text/javascript;base64,' + process.argv[1];
        const {register} = await import(url);
        const handlers = {};
        register((event, handler) => {handlers[event] = handler});
        const forwarded = [];
        const api = {
          session: {id: async () => 'session'},
          mcp: {call: async (server, tool, args) => {
            forwarded.push(args.tool);
            const text = JSON.stringify({result: {ok: true}});
            return {content: [{type: 'text', text}]};
          }},
        };
        const reset = 'Shell cwd was reset to /work';
        const outcome = {
          ref: 1, result: {stdout: 'x', stderr: reset}, text: 'x\\n' + reset,
        };
        const bash = await handlers['tool.call'](api, {
          tool: 'Bash', tool_use_id: 'b', command: 'cd /tmp',
        }, async () => outcome);
        assert.equal(bash, outcome);
        const stop = await handlers['tool.call'](api, {
          tool: 'TaskStop', tool_use_id: 's', task_id: 'b123',
        }, async () => 'harness');
        assert.equal(stop, 'harness');
        for (const path of ['/session-tmp/claude-1/x/s/tasks/b1.output',
                            '/config/projects/-w/s/tool-results/r.txt']) {
          const own = await handlers['tool.call'](api, {
            tool: 'Read', tool_use_id: 'r', file_path: path,
          }, async () => 'local');
          assert.equal(own, 'local');
        }
        for (const path of ['/session-tmp/../etc/passwd', '/work/a.txt',
                            '/config/projects/-w/s.jsonl']) {
          await handlers['tool.call'](api, {
            tool: 'Read', tool_use_id: 'r', file_path: path,
          }, async () => { throw new Error('read on the session host: ' + path) });
        }
        assert.deepEqual(forwarded, ['Read', 'Read', 'Read']);
    """,
            base64.b64encode(source.encode()).decode(),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_send_user_file_is_delivered_to_the_room_never_to_the_anthropic_upload():
    """`SendUserFile` is the build's own "hand this file to the person watching".

    Left alone it POSTs the bytes to Anthropic's `/api/oauth/file_upload` on a
    scoped cheese token that means nothing upstream — the tool reports 401 on
    every call and the room never sees the screenshot or the report. It has to
    land on this room's own delivery route, and the caller has to get back the
    result shape `SendUserFile` promised it.
    """
    _run_proxy("""
        import assert from 'node:assert/strict';
        const url = 'data:text/javascript;base64,' + process.argv[1];
        const {register} = await import(url);
        const handlers = {};
        register((event, handler) => {handlers[event] = handler});
        let called;
        const bytes = Buffer.from('hello');
        const api = {
          session: {id: async () => 'session'},
          fs: {
            stat: async (path, {resolve}) => {
              assert.equal(typeof resolve, 'boolean');
              return {kind: 'file', size: bytes.length, mtimeMs: 0, isLink: false};
            },
            read: async (path, {as}) => {
              assert.equal(path, 'report.pdf');
              assert.equal(as, 'bytes');
              return {base64: bytes.toString('base64')};
            },
          },
          mcp: {call: async (server, tool, args) => {
            called = {server, tool, args};
            const text = JSON.stringify({result: {attachments: [{
              path: 'report.pdf', size: bytes.length, isImage: false,
              media_type: 'application/pdf', pathValidated: true,
            }]}});
            return {content: [{type: 'text', text}]};
          }},
        };
        let handed_back = false;
        const next = async () => {handed_back = true; return 'next'};

        const mine = await handlers['tool.call'](api, {
          tool: 'SendUserFile', tool_use_id: 'send', agentId: 'child',
          files: ['report.pdf'], caption: "Here's the report.", status: 'normal',
        }, next);

        assert.equal(handed_back, false,
          'the built-in Anthropic upload must not run');
        assert.deepEqual(called, {
          server: 'native', tool: 'send_user_file', args: {
            id: 'send', session_id: 'session',
            files: [{
              path: 'report.pdf', name: 'report.pdf',
              data_b64: bytes.toString('base64'),
            }],
            caption: "Here's the report.",
            status: 'normal',
          },
        });
        assert.deepEqual(mine, {result: {attachments: [{
          path: 'report.pdf', size: bytes.length, isImage: false,
          media_type: 'application/pdf', pathValidated: true,
        }]}});
    """)


def test_send_user_file_reads_the_path_the_model_named():
    """The session sees the project at the executor's own path, so the path the
    model names is the one `$.fs` opens on this host, as it would open it
    there; a relative one is the build's own working directory's, the
    workspace, whatever directory the shell is in.
    """
    _run_proxy("""
        import assert from 'node:assert/strict';
        const url = 'data:text/javascript;base64,' + process.argv[1];
        const {register} = await import(url);
        const handlers = {};
        register((event, handler) => {handlers[event] = handler});
        const bytes = Buffer.from('png');
        const asked = [];
        let sent;
        const api = {
          session: {id: async () => 'session'},
          fs: {
            stat: async (path) => {
              asked.push(path);
              return {kind: 'file', size: bytes.length, mtimeMs: 0, isLink: false};
            },
            read: async (path) => ({base64: bytes.toString('base64')}),
          },
          mcp: {call: async (server, tool, args) => {
            sent = args.files;
            const text = JSON.stringify({result: {attachments: []}});
            return {content: [{type: 'text', text}]};
          }},
        };

        await handlers['tool.call'](api, {
          tool: 'SendUserFile', tool_use_id: 'send',
          files: ['/work/shot.png', 'docs/plan.md'], status: 'proactive',
        }, async () => 'next');

        assert.deepEqual(asked, ['/work/shot.png', 'docs/plan.md']);
        assert.deepEqual(sent.map((file) => file.data_b64),
          [bytes.toString('base64'), bytes.toString('base64')]);
    """)


def test_send_user_file_a_file_this_host_cannot_read_still_reaches_the_transport():
    """A private container's file is not on this host at all, and `$.fs.read`
    refuses anything over its own transfer cap. Neither is "the file does not
    exist": both go to the transport, which reads them off the executor the way
    every other project file is read.
    """
    _run_proxy("""
        import assert from 'node:assert/strict';
        const url = 'data:text/javascript;base64,' + process.argv[1];
        const {register} = await import(url);
        const handlers = {};
        register((event, handler) => {handlers[event] = handler});
        let called;
        const api = {
          session: {id: async () => 'session'},
          fs: {
            stat: async () => ({kind: 'file', size: 3, mtimeMs: 0, isLink: false}),
            read: async () => {
              throw new Error(
                '$.fs.read: refused: the file is over the 4194304-byte limit'
              );
            },
          },
          mcp: {call: async (server, tool, args) => {
            called = args;
            const text = JSON.stringify({result: {attachments: []}});
            return {content: [{type: 'text', text}]};
          }},
        };

        await handlers['tool.call'](api, {
          tool: 'SendUserFile', tool_use_id: 'send',
          files: ['big.pdf'], status: 'normal',
        }, async () => 'next');

        assert.deepEqual(called.files, [{path: 'big.pdf', name: 'big.pdf'}]);
    """)


def test_send_user_file_an_oversize_file_is_refused_before_it_is_read():
    """The room's own limit is 10MB (`/topics/{id}/shown`). Asking `$.fs` for
    more than that is a transfer nobody accepts on the far side.
    """
    _run_proxy("""
        import assert from 'node:assert/strict';
        const url = 'data:text/javascript;base64,' + process.argv[1];
        const {register} = await import(url);
        const handlers = {};
        register((event, handler) => {handlers[event] = handler});
        let called;
        let read = false;
        const api = {
          session: {id: async () => 'session'},
          fs: {
            stat: async () => ({
              kind: 'file', size: 11 * 1024 * 1024, mtimeMs: 0, isLink: false,
            }),
            read: async () => {read = true; return {base64: ''};},
          },
          mcp: {call: async (server, tool, args) => {
            called = args;
            const text = JSON.stringify({result: {attachments: []}});
            return {content: [{type: 'text', text}]};
          }},
        };

        await handlers['tool.call'](api, {
          tool: 'SendUserFile', tool_use_id: 'send',
          files: ['huge.bin'], status: 'normal',
        }, async () => 'next');

        assert.equal(read, false);
        assert.equal(called.files[0].data_b64, undefined);
        assert.match(called.files[0].upload_error, /10MB/);
    """)


def test_send_user_file_names_the_object_form_it_cannot_take():
    """The tool text also allows a pre-resolved {file_uuid, file_name, size,
    is_image} entry — a file already in Anthropic's filestore. This room has no
    such filestore to pull it from, and stringifying the object used to name it
    `[object Object]` and fail later on that name. Refuse the form outright, and
    say which form it was.
    """
    _run_proxy("""
        import assert from 'node:assert/strict';
        const url = 'data:text/javascript;base64,' + process.argv[1];
        const {register} = await import(url);
        const handlers = {};
        register((event, handler) => {handlers[event] = handler});
        let delivered = false;
        const api = {
          session: {id: async () => 'session'},
          fs: {
            stat: async () => {throw new Error('must not stat');},
            read: async () => {throw new Error('must not read');},
          },
          mcp: {call: async (server, tool, args) => {
            delivered = true;
            const text = JSON.stringify({result: {attachments: []}});
            return {content: [{type: 'text', text}]};
          }},
        };

        const outcome = await handlers['tool.call'](api, {
          tool: 'SendUserFile', tool_use_id: 'send',
          files: [{file_uuid: 'f-1', file_name: 'shot.png', size: 3, is_image: true}],
          status: 'normal',
        }, async () => 'next');

        assert.equal(delivered, false);
        assert.equal(typeof outcome.deny, 'string');
        assert.match(outcome.deny, /file_uuid/);
        assert.match(outcome.deny, /path/);
    """)


def test_executor_bootstrap_starts_in_room_without_a_git_checkout(
    tmp_path, monkeypatch, capsys
):
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap
    from app.domain.agent.harness.claude_code.remote_execution.launch import script

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-reach-executor")
    monkeypatch.setattr(bootstrap, "binary", lambda *_: claude_binary())
    project, resource = uuid.uuid4(), uuid.uuid4()
    home = tmp_path / ".cheese/home" / str(project) / str(resource)
    state = home / ".cheese/executor"
    # Use the installation payload shipped to devices, including its CLI.
    program = script(
        project, resource, {"CHEESE_API": "http://unused", "CHEESE_TOKEN": "test"}
    )
    call = ast.parse(program).body[-1].value
    payload = json.loads(ast.literal_eval(call.args[0].args[0]))
    try:
        bootstrap.configure(payload)
        assert json.loads(capsys.readouterr().out)["workspace"] == str(home / "room")
        assert not (home / "room/.git").exists()
        config = json.loads((state / "config.json").read_text())
        assert "ANTHROPIC_API_KEY" not in config["env"]
        installed = subprocess.run(
            [str(home / ".cheese/cheese"), "--help"],
            env={**os.environ, **config["env"]},
            capture_output=True,
            text=True,
            check=True,
        )
        assert "worktree" in installed.stdout
        deadline = time.monotonic() + 10
        while not Path(runtime.socket_path(state)).exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert runtime.request(state, "ping")["workspace"] == str(home / "room")
        sync_help = runtime.request(
            state,
            "invoke",
            {
                "id": "cli-worker-help",
                "tool": "Bash",
                "args": {
                    "command": 'test -S "$CHEESE_CLI_SOCKET" && cheese sync --help'
                },
            },
        )
        assert "--all" in sync_help["value"]["stdout"]
        payload["files"]["cheese"] = base64.b64encode(
            b"import sys\n"
            b"if __name__ == 'preload':\n"
            b"    sys.cheese_cli_preloaded = True\n"
            b"if __name__ == '__main__':\n"
            b"    print(getattr(sys, 'cheese_cli_preloaded', False))\n"
        ).decode()
        bootstrap.configure(payload)
        capsys.readouterr()
        preloaded = runtime.request(
            state,
            "invoke",
            {
                "id": "cli-preloaded-dispatch",
                "tool": "Bash",
                "args": {"command": "cheese"},
            },
        )
        assert preloaded["value"]["stdout"].strip() == "True"
        from app.domain.agent import environment_runner

        environment = home / ".cheese-environment"
        environment.mkdir()
        payload["environment"] = {"revision": "existing"}
        (state / "environment.json").write_text(json.dumps(payload["environment"]))
        for status in ("ready", "failed", "pending"):
            environment_runner.write_json(
                environment / "status.json",
                {
                    "state": status,
                    "pid": os.getpid(),
                    "process_identity": environment_runner.process_identity(
                        os.getpid()
                    ),
                },
            )
            bootstrap.configure(payload)
            reused = json.loads(capsys.readouterr().out)
            assert reused["pid"] == runtime.request(state, "ping")["pid"]
            assert reused["environment_status"] == status
    finally:
        subprocess.run(
            [sys.executable, str(RUNTIME), "stop", "--state", str(state)],
            capture_output=True,
            timeout=15,
        )


@pytest.mark.parametrize("custom_cache", [False, True])
def test_executor_rooms_share_installed_tools(
    tmp_path, monkeypatch, capsys, custom_cache
):
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap
    from app.domain.agent.harness.claude_code.remote_execution.launch import payload_for
    from tests.unit.test_machine_launcher import _fake_upstream

    bin_dir, downloads = _fake_upstream(tmp_path)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CHEESE_STORE", raising=False)
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "inherited-cache"))
    monkeypatch.setattr(bootstrap, "binary", lambda *_: claude_binary())
    project = uuid.uuid4()
    tally = tmp_path / "installations"
    environment = {
        "revision": "shared-tools-test",
        "variables": {"UV_CACHE_DIR": str(tmp_path / "custom-cache")}
        if custom_cache
        else {},
        "setup_script": (
            f'printf x >> "{tally}"\n'
            'mkdir -p "$HOME/.local/bin"\n'
            'printf "#!/bin/sh\\necho shared-tool-ok\\n" '
            '> "$HOME/.local/bin/shared-test-tool"\n'
            'chmod +x "$HOME/.local/bin/shared-test-tool"\n'
            'mkdir -p "$UV_CACHE_DIR"\n'
            'printf cached > "$UV_CACHE_DIR/setup-marker"\n'
        ),
        "startup_script": "",
    }
    states = []
    try:
        for room_project in (project, project, uuid.uuid4()):
            resource = uuid.uuid4()
            home = tmp_path / ".cheese/home" / str(room_project) / str(resource)
            state = home / ".cheese/executor"
            states.append(state)
            payload = payload_for(
                room_project,
                resource,
                {
                    "CHEESE_API": "http://unused",
                    "CHEESE_TOKEN": "test",
                    "CHEESE_ENVIRONMENT": json.dumps(environment),
                },
            )
            bootstrap.configure(payload)
            capsys.readouterr()
            font = (
                tmp_path
                / ".cheese/toolchain/fonts"
                / payload["toolchain_fonts"]
                / "NotoSerifSC-VF.otf"
            )
            deadline = time.monotonic() + 10
            while not font.exists():
                assert time.monotonic() < deadline
                time.sleep(0.01)
            bootstrap.configure(payload)
            capsys.readouterr()
            result = runtime.request(
                state,
                "invoke",
                {
                    "id": "shared-tool",
                    "tool": "Bash",
                    "args": {
                        "command": 'shared-test-tool; printf "%s\\n" "$HOME" '
                        '"$UV_CACHE_DIR" "$UV_PYTHON_INSTALL_DIR" '
                        '"$npm_config_store_dir" "$npm_config_cache" "$PIP_CACHE_DIR"; '
                        'typst; pandoc; cat "$TYPST_FONT_PATHS/NotoSerifSC-VF.otf"'
                    },
                },
            )
            store = tmp_path / ".cheese/store" / str(room_project)
            cache = tmp_path / "custom-cache" if custom_cache else store / "uv-cache"
            assert result["value"]["stdout"].splitlines() == [
                "shared-tool-ok",
                str(home),
                str(cache),
                str(store / "uv-python"),
                str(store / "pnpm-store"),
                str(store / "npm-cache"),
                str(store / "pip-cache"),
                "typst",
                "pandoc",
                "OTTO serif",
            ]
            assert (cache / "setup-marker").read_text() == "cached"
            assert not (home / ".local/bin/shared-test-tool").exists()
            assert tally.read_text() == ("x" if room_project == project else "xx")
        assert len(downloads.read_text().splitlines()) == 5
    finally:
        for state in states:
            subprocess.run(
                [sys.executable, str(RUNTIME), "stop", "--state", str(state)],
                capture_output=True,
                timeout=15,
            )


def _room_prepared_under_the_previous_root(tmp_path, monkeypatch):
    """A room as it exists today: its executor installed in `.claude`.

    Returns the payload that would prepare it again under the root in force now,
    and the previous root's directory.
    """
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap
    from app.domain.agent.harness.claude_code.remote_execution.launch import script

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(bootstrap, "binary", lambda *_: claude_binary())
    project, resource = uuid.uuid4(), uuid.uuid4()
    home = tmp_path / ".cheese/home" / str(project) / str(resource)
    previous = home / ".claude"
    (previous / "remote-execution").mkdir(parents=True)
    # Its OWN runtime, the one that started it: the protocol a running executor
    # answers is the one it was installed with, not the one being installed now.
    shutil.copyfile(RUNTIME, previous / "remote-execution/runtime.py")
    work = home / "room"
    work.mkdir(parents=True)
    program = script(
        project, resource, {"CHEESE_API": "http://unused", "CHEESE_TOKEN": "test"}
    )
    call = ast.parse(program).body[-1].value
    payload = json.loads(ast.literal_eval(call.args[0].args[0]))
    return payload, home, previous, resource


def _await_socket(state, timeout=10):
    deadline = time.monotonic() + timeout
    while not Path(runtime.socket_path(state)).exists():
        assert time.monotonic() < deadline, f"executor never answered at {state}"
        time.sleep(0.01)


def _start_executor(state, workspace):
    subprocess.run(
        [sys.executable, str(RUNTIME), "start", "--state", str(state)],
        input=json.dumps({"workspace": str(workspace), "env": {}}),
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )


def test_a_room_left_under_the_previous_root_loses_its_old_executor(
    tmp_path, monkeypatch, capsys
):
    """Moving the platform's own directory has to come back for what the old one
    started.

    The executor is a detached daemon — closing a screen does not close it — and
    the branch that stops a previous executor looks for its state under the root
    in force today. A room prepared under an earlier root would therefore keep
    its daemon running and get the new one beside it: two processes, one HOME,
    one `.cheese-environment/status.json` between them.
    """
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap

    payload, home, previous, resource = _room_prepared_under_the_previous_root(
        tmp_path, monkeypatch
    )
    old_state = previous / "executor"
    _start_executor(old_state, home / "room")
    old_pid = runtime.request(old_state, "ping")["pid"]
    state = home / ".cheese/executor"
    try:
        bootstrap.configure(payload)

        # The one that was running is not running any more, and what is left
        # under the old root says nothing about a room that is.
        deadline = time.monotonic() + 10
        while True:
            try:
                os.kill(old_pid, 0)
            except OSError:
                break
            assert time.monotonic() < deadline, "the old executor is still running"
            time.sleep(0.01)
        assert not old_state.exists()
        assert not (previous / "execution-owner.json").exists()
        # The room came up under the root in force, and only there.
        _await_socket(state)
        assert runtime.request(state, "ping")["workspace"] == str(home / "room")
        assert json.loads((home / ".cheese/execution-owner.json").read_text()) == {
            "resource": str(resource)
        }
    finally:
        subprocess.run(
            [sys.executable, str(RUNTIME), "stop", "--state", str(state)],
            capture_output=True,
            timeout=15,
        )


def test_a_previous_root_with_nothing_behind_it_does_not_hold_the_room_back(
    tmp_path, monkeypatch, capsys
):
    """A machine that rebooted leaves the old executor's state on disk with no
    process behind it. That is the ordinary case, not a reason to refuse the
    room — the stop has to ask whether anything is there before insisting."""
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap

    payload, home, previous, _resource = _room_prepared_under_the_previous_root(
        tmp_path, monkeypatch
    )
    old_state = previous / "executor"
    old_state.mkdir()
    (old_state / "config.json").write_text(
        json.dumps({"workspace": str(home / "room"), "env": {}})
    )
    (previous / "execution-owner.json").write_text('{"resource": "whatever"}')
    state = home / ".cheese/executor"
    try:
        bootstrap.configure(payload)

        _await_socket(state)
        assert runtime.request(state, "ping")["workspace"] == str(home / "room")
        assert not old_state.exists()
        assert not (previous / "execution-owner.json").exists()
    finally:
        subprocess.run(
            [sys.executable, str(RUNTIME), "stop", "--state", str(state)],
            capture_output=True,
            timeout=15,
        )


@pytest.mark.parametrize("rollback", [False, True])
def test_executor_upgrade_retries_after_installer_failure(
    tmp_path, monkeypatch, capsys, rollback
):
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap
    from app.domain.agent.harness.claude_code.remote_execution.launch import payload_for

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(bootstrap, "binary", lambda *_: claude_binary())
    project, resource = uuid.uuid4(), uuid.uuid4()
    payload = payload_for(
        project, resource, {"CHEESE_API": "http://unused", "CHEESE_TOKEN": "test"}
    )
    home = tmp_path / ".cheese/home" / str(project) / str(resource)
    state = home / ".cheese/executor"
    run = subprocess.run
    try:
        bootstrap.configure(payload)
        original = json.loads(capsys.readouterr().out)
        old_sync = payload["files"]["cheese-sync"]
        payload["files"]["cheese-sync"] = base64.b64encode(b"# next release\n").decode()

        def fail_stop(command, **kwargs):
            if "stop" in command:
                raise RuntimeError("installer interrupted")
            return run(command, **kwargs)

        with monkeypatch.context() as failure:
            failure.setattr(bootstrap.subprocess, "run", fail_stop)
            with pytest.raises(RuntimeError, match="installer interrupted"):
                bootstrap.configure(payload)
        assert runtime.request(state, "ping")["upgrading"]
        with pytest.raises(RuntimeError, match="request was not accepted"):
            runtime.request(
                state,
                "invoke",
                {"id": "late", "tool": "Bash", "args": {"command": "touch late"}},
            )
        if rollback:
            payload["files"]["cheese-sync"] = old_sync
        bootstrap.configure(payload)
        updated = json.loads(capsys.readouterr().out)
        assert updated["pid"] != original["pid"]
        assert not updated["upgrading"]
        assert not (state / "upgrade.json").exists()
        assert not (home / "room/late").exists()
        result = runtime.request(
            state,
            "invoke",
            {"id": "after", "tool": "Bash", "args": {"command": "printf resumed"}},
        )
        assert result["value"]["stdout"] == "resumed"
    finally:
        run(
            [sys.executable, str(RUNTIME), "stop", "--state", str(state)],
            capture_output=True,
            timeout=15,
        )


@pytest.mark.parametrize("socket_gone", [False, True])
def test_stop_waits_for_service_lock_after_socket_disappears(tmp_path, socket_gone):
    state = tmp_path / "executor"
    state.mkdir()
    attempted = tmp_path / "lock-attempted"
    signalled = tmp_path / "signalled"
    observer = tmp_path / "observed_stop.py"
    observer.write_text(
        """
import importlib.util
import pathlib
import sys

source, state, attempted, signalled, socket_gone = sys.argv[1:]
spec = importlib.util.spec_from_file_location("executor_runtime", source)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
original_flock = runtime.fcntl.flock

def observe_flock(lock, operation):
    try:
        return original_flock(lock, operation)
    except BlockingIOError:
        pathlib.Path(attempted).touch()
        raise

runtime.fcntl.flock = observe_flock
if socket_gone == "False":
    runtime.request = lambda *_: {"pid": 123}
    runtime.os.kill = lambda *_: pathlib.Path(signalled).touch()
sys.argv = [source, "stop", "--state", state]
runtime.main()
"""
    )
    with (state / "service.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stop = subprocess.Popen(
            [
                sys.executable,
                str(observer),
                str(RUNTIME),
                str(state),
                str(attempted),
                str(signalled),
                str(socket_gone),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 5
            while not attempted.exists():
                if stop.poll() is not None:
                    _, stderr = stop.communicate()
                    pytest.fail(f"stop returned before lock release: {stderr.decode()}")
                assert time.monotonic() < deadline
                time.sleep(0.01)
            assert stop.poll() is None
            if not socket_gone:
                assert signalled.exists()
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            try:
                _, stderr = stop.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                stop.kill()
                stop.communicate()
                raise
        assert stop.returncode == 0, stderr.decode()


def test_stop_with_no_state_remains_a_no_op(tmp_path):
    state = tmp_path / "missing"
    result = subprocess.run(
        [sys.executable, str(RUNTIME), "stop", "--state", str(state)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert not state.exists()


@pytest.mark.parametrize("update_kind", ["runtime", "binary", "helper"])
def test_executor_release_waits_for_commands_and_preserves_results(
    tmp_path, monkeypatch, capsys, update_kind
):
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap
    from app.domain.agent.harness.claude_code.remote_execution.launch import payload_for

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(bootstrap, "binary", lambda *_: claude_binary())
    project, resource = uuid.uuid4(), uuid.uuid4()
    payload = payload_for(
        project, resource, {"CHEESE_API": "http://unused", "CHEESE_TOKEN": "test"}
    )
    home = tmp_path / ".cheese/home" / str(project) / str(resource)
    state = home / ".cheese/executor"
    source = home / ".cheese/remote-execution/runtime.py"

    def ready():
        deadline = time.monotonic() + 10
        while True:
            try:
                return runtime.request(state, "ping")
            except (ConnectionError, FileNotFoundError):
                assert time.monotonic() < deadline
                time.sleep(0.01)

    try:
        bootstrap.configure(payload)
        capsys.readouterr()
        original = ready()
        task = "retained-output"
        runtime.request(
            state,
            "control",
            {
                "subtype": "shell",
                "operation": "start",
                "command_id": task,
                "kind": "sh",
                "body": "while [ ! -f release ]; do sleep 0.05; done; printf kept",
                "cwd": original["workspace"],
                "env": {},
                "merge": True,
                "stdin": None,
            },
        )
        changed = base64.b64decode(payload["files"]["remote-execution/runtime.py"])
        if update_kind == "runtime":
            changed += b"\n# release fixture\n"
        elif update_kind == "binary":
            next_binary = tmp_path / "next-claude"
            next_binary.symlink_to(claude_binary())
            monkeypatch.setattr(bootstrap, "binary", lambda *_: str(next_binary))
        else:
            payload["files"]["cheese-sync"] = base64.b64encode(b"# new sync\n").decode()
        payload["files"]["remote-execution/runtime.py"] = base64.b64encode(
            changed
        ).decode()
        before = source.read_bytes()
        payload["env"]["CHEESE_TOKEN"] = "refreshed-while-busy"
        try:
            bootstrap.configure(payload)
        except RuntimeError as error:
            log = home / ".cheese/executor-bootstrap.log"
            pytest.fail(
                f"{error}; executor-bootstrap.log:\n"
                f"{log.read_text() if log.exists() else '<missing>'}"
            )
        deferred = json.loads(capsys.readouterr().out)
        assert deferred["upgrade_pending"] is True
        assert deferred["pid"] == original["pid"]
        assert (home / ".cheese/cheese-preview.token").read_text() == (
            "refreshed-while-busy"
        )
        assert (
            json.loads((state / "config.json").read_text())["env"]["CHEESE_TOKEN"]
            == "refreshed-while-busy"
        )
        assert source.read_bytes() == before
        assert ready()["pid"] == original["pid"]
        (home / "room/release").touch()
        assert _collect(state, task) == ("kept", 0)
        try:
            bootstrap.configure(payload)
        except RuntimeError as error:
            log = home / ".cheese/executor-bootstrap.log"
            pytest.fail(
                f"{error}; restart executor-bootstrap.log:\n"
                f"{log.read_text() if log.exists() else '<missing>'}"
            )
        capsys.readouterr()
        updated = ready()
        assert updated["pid"] != original["pid"]
        assert updated["runtime_sha256"] == hashlib.sha256(changed).hexdigest()
        assert _collect(state, task) == ("kept", 0)
        bootstrap.configure(payload)
        capsys.readouterr()
        assert ready()["pid"] == updated["pid"]
    finally:
        subprocess.run(
            [sys.executable, str(RUNTIME), "stop", "--state", str(state)],
            capture_output=True,
            timeout=15,
        )


def _collect(state, command_id, timeout=30):
    """A command's whole output and exit status, read the way its reader does."""
    output, deadline = b"", time.monotonic() + timeout
    while True:
        answer = runtime.request(
            state,
            "control",
            {
                "subtype": "shell",
                "operation": "read",
                "command_id": command_id,
                "out": len(output),
                "err": 0,
                "wait": 1,
            },
        )
        output += base64.b64decode(answer["out"])
        if "exit" in answer:
            return output.decode(), answer["exit"]
        assert time.monotonic() < deadline, output


def test_running_executor_prepares_updated_room_without_restart(
    tmp_path, monkeypatch, capsys
):
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap
    from app.domain.agent.harness.claude_code.remote_execution.launch import script

    monkeypatch.setenv("HOME", str(tmp_path))
    binary = tmp_path / ".cheese/claude/versions" / bootstrap.VERSION
    binary.parent.mkdir(parents=True)
    version_calls = tmp_path / "version-calls"
    # The stub counts version checks; the executor's commands need the real
    # build behind it, because every command runs through its `mcp serve`.
    binary.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        f"  echo checked >> '{version_calls}'\n"
        f"  echo '{bootstrap.VERSION}'\n"
        "  exit 0\n"
        "fi\n"
        f"exec '{claude_binary()}' \"$@\"\n"
    )
    binary.chmod(0o700)
    project, resource = uuid.uuid4(), uuid.uuid4()
    home = tmp_path / ".cheese/home" / str(project) / str(resource)
    state = home / ".cheese/executor"
    call = (
        ast.parse(
            script(
                project,
                resource,
                {
                    "CHEESE_API": "http://unused",
                    "CHEESE_TOKEN": "first",
                },
            )
        )
        .body[-1]
        .value
    )
    payload = json.loads(ast.literal_eval(call.args[0].args[0]))
    import base64

    cli = (
        "from pathlib import Path\n"
        "if __name__ == 'preload':\n"
        "    with (Path.home() / '.cheese/preload-calls').open('a') as output:\n"
        "        output.write('loaded\\n')\n"
        "if __name__ == '__main__':\n"
        "    print('first CLI')\n"
    )
    payload["files"]["cheese"] = base64.b64encode(cli.encode()).decode()
    try:
        bootstrap.configure(payload)
        capsys.readouterr()
        deadline = time.monotonic() + 10
        while not Path(runtime.socket_path(state)).exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        original = runtime.request(state, "ping")
        assert "prepare" in original["capabilities"]
        from app.domain.agent.harness.claude_code.remote_execution.launch import (
            payload_for,
        )

        delta = payload_for(project, resource, payload["env"], original["files"])
        # The fixture replaces the CLI; all other installed helpers are unchanged.
        assert set(delta["files"]) == {"cheese"}
        payload["env"]["CHEESE_TOKEN"] = "refreshed"
        ready = runtime.request(state, "prepare", payload)
        assert ready["pid"] == original["pid"]
        assert ready["workspace"] == str(home / "room")
        assert (home / ".cheese/cheese-preview.token").read_text() == "refreshed"
        assert (
            json.loads((state / "config.json").read_text())["env"]["CHEESE_TOKEN"]
            == "refreshed"
        )
        for iteration in range(2):
            result = runtime.request(
                state,
                "invoke",
                {
                    "id": f"preload-{iteration}",
                    "tool": "Bash",
                    "args": {"command": "cheese --version"},
                },
            )
            assert result["value"]["stdout"].strip() == "first CLI"
            assert (home / ".cheese/preload-calls").read_text() == "loaded\n"
            runtime.request(state, "prepare", payload)
        payload["files"]["cheese"] = base64.b64encode(
            cli.replace("first CLI", "updated CLI").encode()
        ).decode()
        with pytest.raises(RuntimeError, match="idle upgrade"):
            runtime.request(state, "prepare", payload)
        pinned_cli = Path(original["release"]) / "cheese"
        bootstrap.configure(payload)
        capsys.readouterr()
        original = runtime.request(state, "ping")
        assert pinned_cli.read_text() == cli
        updated = runtime.request(
            state,
            "invoke",
            {
                "id": "updated-cli",
                "tool": "Bash",
                "args": {"command": "cheese --version"},
            },
        )
        assert updated["value"]["stdout"].strip() == "updated CLI"
        assert (home / ".cheese/preload-calls").read_text() == "loaded\nloaded\n"
        runtime.request(state, "prepare", payload)
        checked = version_calls.read_text()
        runtime.request(state, "prepare", payload)
        assert version_calls.read_text() == checked
        before = binary.stat()
        binary.write_text(binary.read_text() + "# changed in place\n")
        os.utime(binary, ns=(before.st_atime_ns, before.st_mtime_ns))
        runtime.request(state, "prepare", payload)
        assert version_calls.read_text() == checked + "checked\n"
        checked = version_calls.read_text()
        replacement = binary.with_suffix(".replacement")
        replacement.write_text(binary.read_text())
        replacement.chmod(0o700)
        replacement.replace(binary)
        runtime.request(state, "prepare", payload)
        assert version_calls.read_text() == checked + "checked\n"
        from app.domain.agent import environment_runner

        environment = home / ".cheese-environment"
        environment.mkdir()
        payload["environment"] = {"revision": "existing"}
        (state / "environment.json").write_text(json.dumps(payload["environment"]))
        for status in ("ready", "failed", "pending"):
            environment_runner.write_json(
                environment / "status.json",
                {
                    "state": status,
                    "pid": os.getpid(),
                    "process_identity": environment_runner.process_identity(
                        os.getpid()
                    ),
                },
            )
            prepared = runtime.request(state, "prepare", payload)
            assert prepared["pid"] == original["pid"]
            assert prepared["environment_status"] == status
        payload["resource"] = str(uuid.uuid4())
        with pytest.raises(RuntimeError, match="another room"):
            runtime.request(state, "prepare", payload)
        assert not (home.parent / payload["resource"]).exists()
    finally:
        subprocess.run(
            [sys.executable, str(RUNTIME), "stop", "--state", str(state)],
            capture_output=True,
            timeout=15,
        )


def test_modified_verified_binary_with_wrong_version_is_not_reused(tmp_path):
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap

    binary = tmp_path / ".cheese/claude/versions" / bootstrap.VERSION
    binary.parent.mkdir(parents=True)
    binary.write_text(f"#!/bin/sh\necho '{bootstrap.VERSION}'\n")
    binary.chmod(0o700)
    fallback = tmp_path / ".local/bin/claude"
    fallback.parent.mkdir(parents=True)
    fallback.write_text(binary.read_text())
    fallback.chmod(0o700)
    verified = {}
    assert bootstrap.binary(tmp_path, "http://unused", verified) == str(binary)
    binary.write_text("#!/bin/sh\necho '0.0.0'\n")
    assert bootstrap.binary(tmp_path, "http://unused", verified) == str(binary)
    assert binary.read_bytes() == fallback.read_bytes()
    fallback.write_text("#!/bin/sh\necho 0.0.0\n")
    assert bootstrap.binary(tmp_path, "http://unused", verified) == str(binary)


class RemoteExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cheese-execution-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / "project with spaces"
        self.workspace.mkdir()
        self.state = self.root / "state"
        claude = os.environ.get("CHEESE_TEST_CLAUDE") or shutil.which("claude")
        self.assertIsNotNone(
            claude, "Install the pinned Claude Code build before running acceptance"
        )
        self.config = {
            "workspace": str(self.workspace),
            "claude": claude,
            "env": {"EXECUTOR_MARKER": "remote-environment"},
        }
        self.start()
        self.addCleanup(self.stop)

    def start(self):
        process = subprocess.run(
            [sys.executable, str(RUNTIME), "start", "--state", str(self.state)],
            input=json.dumps(self.config),
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.pid = json.loads(process.stdout)["pid"]

    def stop(self):
        subprocess.run(
            [sys.executable, str(RUNTIME), "stop", "--state", str(self.state)],
            capture_output=True,
            timeout=15,
        )

    def invoke(self, tool, args, key=None):
        response = runtime.request(
            self.state,
            "invoke",
            {"id": key or str(uuid.uuid4()), "tool": tool, "args": args},
        )
        self.assertNotIn("error", response, response)
        return response["value"]

    def test_bash_search_reads_only_the_executor_workspace(self):
        (self.workspace / "needle.txt").write_text("executor-only-needle\n")
        (self.root / "needle.txt").write_text("session-host-decoy\n")
        result = self.invoke("Bash", {"command": "grep -n needle needle.txt"})
        self.assertIn("executor-only-needle", result["stdout"])
        self.assertNotIn("session-host-decoy", result["stdout"])

    def test_native_files_and_rc_agree(self):
        target = self.workspace / "sample.txt"
        target.write_text("before\n")
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(["git", "add", "."], cwd=self.workspace, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-qm",
                "seed",
            ],
            cwd=self.workspace,
            check=True,
        )
        read = self.invoke("Read", {"file_path": str(target)})
        self.assertEqual(read["file"]["content"], "before\n")
        self.invoke(
            "Edit",
            {"file_path": str(target), "old_string": "before", "new_string": "after"},
        )
        self.invoke(
            "Write", {"file_path": str(self.workspace / "new.txt"), "content": "你好\n"}
        )
        preview = runtime.request(
            self.state, "control", {"subtype": "read_file", "path": "sample.txt"}
        )
        self.assertEqual(preview["contents"], "after\n")
        diff = runtime.request(self.state, "control", {"subtype": "get_workspace_diff"})
        self.assertIn("+after", diff["diff"])
        self.assertEqual((self.workspace / "new.txt").read_text(), "你好\n")

    def test_shell_cwd_environment_and_exit_status(self):
        (self.workspace / "sub dir").mkdir()
        self.invoke("Bash", {"command": "cd 'sub dir'"})
        result = self.invoke(
            "Bash",
            {
                "command": 'printf "%s\\n" "$PWD" "$EXECUTOR_MARKER"; '
                "printf failure >&2; exit 7"
            },
        )
        # A failure comes back the way the build gives it to its own agent:
        # the exit code first, then everything the command printed, together.
        self.assertTrue(result["stdout"].startswith("Exit code 7"), result)
        self.assertIn(
            str(self.workspace / "sub dir") + "\nremote-environment", result["stdout"]
        )
        self.assertIn("failure", result["stdout"])
        self.assertEqual(result["stderr"], "")

    def test_a_refreshed_environment_reaches_the_next_command(self):
        # A token arrives refreshed through `configure` while the serve process
        # keeps the environment it started with; the command must see the new
        # value, or every `cheese` call from the shell dies with the old token.
        self.assertEqual(
            self.invoke("Bash", {"command": 'printf "$EXECUTOR_MARKER"'})["stdout"],
            "remote-environment",
        )
        runtime.request(
            self.state, "configure", {"env": {"EXECUTOR_MARKER": "refreshed"}}
        )
        self.assertEqual(
            self.invoke("Bash", {"command": 'printf "$EXECUTOR_MARKER"'})["stdout"],
            "refreshed",
        )

    def test_request_replay_does_not_repeat_write(self):
        args = {"command": "printf x >> count.txt"}
        original = self.invoke("Bash", args, key="same-request")
        self.assertEqual(self.invoke("Bash", args, key="same-request"), original)
        self.assertEqual((self.workspace / "count.txt").read_text(), "x")
        with self.assertRaisesRegex(RuntimeError, "different input"):
            self.invoke(
                "Bash", {"command": "printf y >> count.txt"}, key="same-request"
            )

    def finished_task(self, task_id):
        """A task's whole output and exit status once it has ended."""
        output, code = _collect(self.state, task_id)
        return {"output": output, "exit": code}

    def test_reconnect_retains_background_task(self):
        task = self.invoke(
            "Bash",
            {"command": "sleep 0.2; printf background-done", "run_in_background": True},
        )
        previous_pid = self.pid
        self.start()
        self.assertEqual(self.pid, previous_pid)
        self.assertEqual(
            self.finished_task(task["backgroundTaskId"]),
            {"output": "background-done", "exit": 0},
        )

    def test_stop_kills_descendant_ignoring_term(self):
        task = self.invoke(
            "Bash",
            {
                "command": 'bash -c \'trap "" TERM; touch started; sleep 2; '
                "printf survived > forbidden.txt' & wait",
                "run_in_background": True,
            },
        )
        deadline = time.monotonic() + 3
        while not (self.workspace / "started").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue((self.workspace / "started").exists())
        stopped = self.invoke("TaskStop", {"task_id": task["backgroundTaskId"]})
        self.assertEqual(stopped["task_id"], task["backgroundTaskId"])
        time.sleep(2.2)
        self.assertFalse((self.workspace / "forbidden.txt").exists())
        self.assertNotEqual(self.finished_task(task["backgroundTaskId"])["exit"], 0)

    def test_restart_preserves_completed_request_receipt(self):
        self.invoke("Bash", {"command": "printf x >> count.txt"}, key="persisted")
        self.stop()
        self.start()
        self.invoke("Bash", {"command": "printf x >> count.txt"}, key="persisted")
        self.assertEqual((self.workspace / "count.txt").read_text(), "x")

    def test_context_reads_project_instructions_and_skill_assets(self):
        (self.workspace / "CLAUDE.md").write_text("REMOTE_INSTRUCTIONS")
        skill = self.workspace / ".claude/skills/example"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("REMOTE_SKILL")
        (skill / "asset.bin").write_bytes(b"\x00\xff")
        result = runtime.request(self.state, "context")
        self.assertEqual(
            set(result["files"]),
            {
                "CLAUDE.md",
                ".claude/skills/example/SKILL.md",
                ".claude/skills/example/asset.bin",
            },
        )

    def test_context_sends_only_changed_working_tree_files(self):
        instructions = self.workspace / "CLAUDE.md"
        instructions.write_text("first")
        first = runtime.request(self.state, "context")
        known = {
            name: hashlib.sha256(base64.b64decode(value)).hexdigest()
            for name, value in first["files"].items()
        }
        unchanged = runtime.request(self.state, "context", {"known_files": known})
        self.assertEqual(unchanged["files"], {})
        self.assertIn("CLAUDE.md", unchanged["file_names"])
        self.assertEqual(unchanged["instructions"], first["instructions"])

        instructions.write_text("uncommitted change")
        changed = runtime.request(self.state, "context", {"known_files": known})
        self.assertEqual(
            base64.b64decode(changed["files"]["CLAUDE.md"]), b"uncommitted change"
        )
        self.assertIn("uncommitted change", changed["instructions"])
        instructions.unlink()
        removed = runtime.request(self.state, "context", {"known_files": known})
        self.assertNotIn("CLAUDE.md", removed["file_names"])
        self.assertEqual(removed["instructions"], "")

    def test_context_fs_lists_metadata_and_reads_bytes_on_demand(self):
        (self.workspace / "CLAUDE.md").write_text("root @docs/more.md @.claude")
        (self.workspace / "docs").mkdir()
        (self.workspace / "docs/more.md").write_text("imported @nested.md")
        (self.workspace / "docs/nested.md").write_text("nested import")
        (self.workspace / "backend").mkdir()
        (self.workspace / "backend/CLAUDE.md").write_text("nested instructions")
        skill = self.workspace / ".claude/skills/example"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("skill body")
        (skill / "support.bin").write_bytes(b"012345")
        (skill / "support-link").symlink_to("support.bin")
        (self.workspace / ".claude/settings.json").write_text("do not expose")
        (self.workspace / ".claude/settings.local.json").write_text("do not expose")

        tree = runtime.request(self.state, "context_fs", {"operation": "tree"})
        self.assertEqual(tree["unsupported_imports"], [])
        self.assertEqual(tree["unsupported_paths"], [])
        self.assertIn("CLAUDE.md", tree["entries"])
        self.assertIn("docs/more.md", tree["entries"])
        self.assertIn("docs/nested.md", tree["entries"])
        self.assertIn("backend/CLAUDE.md", tree["entries"])
        self.assertIn(".claude/skills/example/support.bin", tree["entries"])
        self.assertEqual(
            tree["entries"][".claude/skills/example/support-link"]["kind"],
            "symlink",
        )
        self.assertEqual(
            tree["entries"][".claude/skills/example/support-link"]["target"],
            "support.bin",
        )
        self.assertNotIn(".claude/settings.json", tree["entries"])
        self.assertNotIn(".claude/settings.local.json", tree["entries"])
        chunk = runtime.request(
            self.state,
            "context_fs",
            {
                "operation": "read",
                "path": ".claude/skills/example/support.bin",
                "offset": 2,
                "size": 3,
            },
        )
        self.assertEqual(base64.b64decode(chunk["data"]), b"234")

        (skill / "SKILL.md").write_text("changed skill body")
        changed = runtime.request(self.state, "context_fs", {"operation": "tree"})
        self.assertNotEqual(changed["generation"], tree["generation"])

    def test_project_workflow_save_reaches_executor_and_other_paths_stay_read_only(
        self,
    ):
        spec = importlib.util.spec_from_file_location(
            "forwarded_fs", RUNTIME.with_name("forwarded_fs.py")
        )
        forwarded_fs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(forwarded_fs)

        workflows = self.workspace / ".claude/workflows"
        view = forwarded_fs.ForwardedProject(
            lambda method, params: runtime.request(self.state, method, params)
        )
        view.refresh()
        view.mkdir("/.claude", 0o700)
        view.mkdir("/.claude/workflows", 0o700)
        view.create("/.claude/workflows/draft.js", 0o600)
        view.write("/.claude/workflows/draft.js", b"export default 1\n", 0)
        view.rename("/.claude/workflows/draft.js", "/.claude/workflows/saved.js")
        assert (workflows / "saved.js").read_text() == "export default 1\n"
        assert ".claude/workflows/saved.js" in view.entries
        assert view.read("/.claude/workflows/saved.js", 100, 0) == b"export default 1\n"
        view.open("/.claude/workflows/saved.js", os.O_WRONLY | os.O_TRUNC)
        view.write("/.claude/workflows/saved.js", b"export default 2\n", 0)
        assert (workflows / "saved.js").read_text() == "export default 2\n"

        with pytest.raises(OSError) as outside:
            view.create("/.claude/settings.json", 0o600)
        assert outside.value.errno == errno.EROFS
        (workflows / "outside").symlink_to(self.root)
        with pytest.raises(RuntimeError, match="Only project workflows"):
            runtime.request(
                self.state,
                "context_fs",
                {"operation": "create", "path": ".claude/workflows/outside/leak"},
            )
        assert not (self.root / "leak").exists()

    def test_context_fs_reports_imports_outside_project_boundary(self):
        absolute_project_import = str(self.workspace / "inside.md")
        (self.workspace / "inside.md").write_text("inside")
        (self.workspace / "CLAUDE.md").write_text(
            f"@/etc/hosts @../../outside.md @~/.claude/machine.md "
            f"@{absolute_project_import}"
        )
        tree = runtime.request(self.state, "context_fs", {"operation": "tree"})
        self.assertEqual(
            tree["unsupported_imports"],
            sorted(
                [
                    "../../outside.md",
                    "/etc/hosts",
                    absolute_project_import.split()[0],
                    str(Path.home() / ".claude/machine.md"),
                ]
            ),
        )

        outside = self.root / "outside-skill"
        outside.mkdir()
        (outside / "SKILL.md").write_text("outside")
        skills = self.workspace / ".claude/skills"
        skills.mkdir(parents=True)
        (skills / "outside").symlink_to(outside, target_is_directory=True)
        tree = runtime.request(self.state, "context_fs", {"operation": "tree"})
        self.assertEqual(tree["unsupported_paths"], [".claude/skills/outside"])
        self.assertNotIn(".claude/skills/outside/SKILL.md", tree["entries"])

    def test_context_sync_records_generation_without_copying_file_bodies(self):
        spec = importlib.util.spec_from_file_location(
            "central_client", RUNTIME.with_name("client.py")
        )
        client = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(client)

        (self.workspace / "CLAUDE.md").write_text("project instructions")
        central = self.root / "central"
        config = self.root / "config"
        central.mkdir()
        config.mkdir()
        target = self.root / "target.json"
        target.write_text(
            json.dumps(
                {"central_workspace": str(central), "central_config": str(config)}
            )
        )

        def context_request(_client, method, params=None):
            return runtime.request(self.state, method, params or {})

        with patch.object(client.RemoteClient, "call", context_request):
            first = client.sync_context(target)
            self.assertTrue(first["changed"])
            self.assertEqual(list(central.iterdir()), [])
            tree_path = target.with_name("context-tree.json")
            first_tree = tree_path.read_bytes()
            first_tree_mtime = tree_path.stat().st_mtime_ns
            unchanged = client.sync_context(target)
            self.assertFalse(unchanged["changed"])
            self.assertEqual(tree_path.read_bytes(), first_tree)
            self.assertEqual(tree_path.stat().st_mtime_ns, first_tree_mtime)
            (self.workspace / "CLAUDE.md").write_text("updated instructions")
            changed = client.sync_context(target)
            self.assertTrue(changed["changed"])
            self.assertNotEqual(changed["generation"], first["generation"])

    def test_disconnected_executor_is_an_error(self):
        self.stop()
        with self.assertRaises(OSError):
            runtime.request(
                self.state,
                "invoke",
                {
                    "id": "after-disconnect",
                    "tool": "Write",
                    "args": {
                        "file_path": str(self.workspace / "forbidden.txt"),
                        "content": "wrong",
                    },
                },
            )
        self.assertFalse((self.workspace / "forbidden.txt").exists())

    def test_a_foreground_command_past_its_timeout_becomes_a_task_the_room_can_see(
        self,
    ):
        # At the caller's deadline the command is left running and handed
        # back as a task, which goes on to finish.
        result = self.invoke(
            "Bash",
            {"command": "touch started; sleep 2; printf done", "timeout": 500},
            "foreground",
        )
        self.assertIn("backgroundTaskId", result)
        self.assertFalse((self.workspace / "done").exists())
        self.assertEqual(
            self.finished_task(result["backgroundTaskId"]),
            {"output": "done", "exit": 0},
        )

    def test_remote_command_hook_can_prevent_a_write(self):
        config = self.workspace / ".claude"
        config.mkdir()
        (config / "settings.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Write",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "printf denied >&2; exit 2",
                                    }
                                ],
                            }
                        ]
                    }
                }
            )
        )
        response = runtime.request(
            self.state,
            "invoke",
            {
                "id": "denied-write",
                "tool": "Write",
                "args": {
                    "file_path": str(self.workspace / "forbidden.txt"),
                    "content": "wrong",
                },
            },
        )
        self.assertIn("denied", response["error"])
        self.assertFalse((self.workspace / "forbidden.txt").exists())

    def test_context_expands_remote_imports(self):
        (self.workspace / "CLAUDE.md").write_text("@instructions.md\n")
        (self.workspace / "instructions.md").write_text("IMPORTED_REMOTE_INSTRUCTIONS")
        self.assertIn(
            "IMPORTED_REMOTE_INSTRUCTIONS",
            runtime.request(self.state, "context")["instructions"],
        )

    def test_remote_shell_search(self):
        (self.workspace / "find-me.txt").write_text("REMOTE_SEARCH_MARKER\n")
        glob = self.invoke("Bash", {"command": "rg --files -g '*.txt'"})
        self.assertIn("find-me.txt", json.dumps(glob))
        grep = self.invoke(
            "Bash",
            {"command": "rg REMOTE_SEARCH_MARKER ."},
        )
        self.assertIn("REMOTE_SEARCH_MARKER", json.dumps(grep))


def test_prepare_adds_the_teammate_sync_hook_exactly_once():
    """seed 的 settings.json 可能来自任一架构、任何年代：中央会话这条路的
    发现层在这里确定性地补一份，而且重开多少次都只有一份。"""
    from app.domain.agent.harness.claude_code.remote_execution import client

    hooks = {
        "SessionStart": [
            {
                "hooks": [
                    {"type": "command", "command": "cheese-hook"},
                    {"type": "command", "command": "cheese sync-agents"},
                ]
            }
        ]
    }
    client._ensure_sync_agents_hook(hooks)
    client._ensure_sync_agents_hook(hooks)
    for event in ("SessionStart", "UserPromptSubmit"):
        commands = [
            hook["command"] for group in hooks[event] for hook in group["hooks"]
        ]
        assert commands.count("cheese sync-agents || true") == 1
    assert hooks["SessionStart"][0]["hooks"][0]["command"] == "cheese-hook"


if __name__ == "__main__":
    unittest.main(verbosity=2)
