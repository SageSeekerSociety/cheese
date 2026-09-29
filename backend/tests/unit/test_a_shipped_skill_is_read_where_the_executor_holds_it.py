"""A skill the platform ships is read, and run, where the executor holds it.

A room's session finds every skill in its config dir on the session host. The
project's are links into the project view, and on the executor they are in the
project's `.claude/skills`. The platform's own (and a way of working the
project saved) the launch writes into that config dir, and into the
executor's config dir too: that is where a file named beside one of them is,
and where a command that runs its script has to find it.
"""

import base64
import subprocess

from app.domain.agent.harness.claude_code.remote_execution import client, release

TARGET = {
    "central_config": "/config",
    "central_workspace": "/view",
    "workspace": "/work",
    "session_workspace": "/work",
    "executor_config": "/home/room/.claude",
    "shipped_skills": ["documents"],
}


def _proxy(program: str) -> None:
    source = release.hook_module(
        (client.Path(client.__file__).with_name("proxy.js")).read_text(),
        TARGET,
        [],
    )
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            program,
            base64.b64encode(source.encode()).decode(),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_the_skill_text_names_each_skill_where_the_executor_holds_it():
    _proxy("""
        import assert from 'node:assert/strict';
        const {register} = await import(
          'data:text/javascript;base64,' + process.argv[1]);
        const handlers = {};
        register((event, handler) => {handlers[event] = handler});
        const text = 'Base directory for this skill: /config/skills/documents\\n'
          + 'See /config/skills/greet/reference.md';
        const {text: seen} = await handlers['skill.prompt'](
          {}, {}, async () => ({text}));
        assert.equal(seen,
          'Base directory for this skill: /home/room/.claude/skills/documents\\n'
          + 'See /work/.claude/skills/greet/reference.md');
    """)


def test_a_file_beside_a_shipped_skill_is_read_from_the_executors_config_dir():
    _proxy("""
        import assert from 'node:assert/strict';
        const {register} = await import(
          'data:text/javascript;base64,' + process.argv[1]);
        const handlers = {};
        register((event, handler) => {handlers[event] = handler});
        const read = [];
        const api = {
          session: {id: async () => 'session'},
          // No reply is owed in this session.
          env: {get: async () => undefined},
          mcp: {call: async (server, tool, args) => {
            read.push(args.args.file_path);
            return {content: [{type: 'text', text: JSON.stringify({result: {}})}]};
          }},
        };
        for (const file_path of [
          '/config/skills/documents/references/word.md',
          '/config/skills/greet/reference.md',
        ]) {
          await handlers['tool.call'](api, {tool: 'Read', tool_use_id: 'r', file_path});
        }
        assert.deepEqual(read, [
          '/home/room/.claude/skills/documents/references/word.md',
          '/work/.claude/skills/greet/reference.md',
        ]);
    """)


def test_a_command_runs_a_shipped_skills_script_from_the_executors_config_dir():
    command = (
        "python3 /config/skills/documents/scripts/office.py text a.docx"
        " && sh '/config/skills/greet/scripts/hello.sh'"
    )
    assert client.skill_paths(TARGET, command) == (
        "python3 /home/room/.claude/skills/documents/scripts/office.py text a.docx"
        " && sh '/work/.claude/skills/greet/scripts/hello.sh'"
    )
