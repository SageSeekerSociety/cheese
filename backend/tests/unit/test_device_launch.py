"""Device screen launcher: hooks settings + self-contained launch command."""

import json

from app.domain.agent import device_launch


def test_hooks_settings_wire_command_hook_to_forwarder():
    s = device_launch.hooks_settings()
    assert s["skipDangerousModePermissionPrompt"] is True
    # Every perception hook forwards via the `cheese-hook` COMMAND hook.
    events = ("SessionStart", "PreToolUse", "PostToolUse", "MessageDisplay", "Stop")
    for event in events:
        entry = s["hooks"][event][0]
        assert entry["hooks"][0] == {"type": "command", "command": "cheese-hook"}


def test_build_screen_launch_shapes_command_and_env():
    command, env, cheeselet = device_launch.build_screen_launch(
        hook_url="http://h/sandbox/hooks/T",
        hook_token="scoped-tok",
        home_dir="/dev/home",
        work_dir="/dev/work",
        model="glm-5.2",
        extra_env={"ANTHROPIC_BASE_URL": "http://gw"},
    )
    assert command[0] == "bash" and command[1] == "-lc"
    script = command[2]
    # Self-contained launcher: writes settings + forwarder, then execs claude.
    assert 'cat > "$HOME/.claude/settings.json"' in script
    assert "cheese-hook" in script
    assert "exec claude --dangerously-skip-permissions" in script
    # Env carries the hook wiring, home/work, model, and the gateway var.
    assert env["CHEESE_HOOK_URL"] == "http://h/sandbox/hooks/T"
    assert env["CHEESE_TOKEN"] == "scoped-tok"
    assert env["CHEESE_HOME"] == "/dev/home" and env["CHEESE_WORK"] == "/dev/work"
    assert env["CLAUDE_MODEL"] == "glm-5.2"
    assert env["ANTHROPIC_BASE_URL"] == "http://gw"
    # The base gates are valid JSON the launcher's node parses.
    gates = json.loads(env["CHEESE_CLAUDE_GATES"])
    assert gates["hasCompletedOnboarding"] is True
    # The minimal cheeselet only drives input (no state inference).
    assert "cheese.expose('prompt'" in cheeselet


def test_forwarder_posts_hook_json_with_token():
    script = device_launch.build_launch_script()
    assert "X-Cheese-Token: $CHEESE_TOKEN" in script
    assert "--data-binary @-" in script
    # Per-project trust is pre-accepted for the RESOLVED work dir (canonicalized).
    assert "pwd -P" in script
    assert "hasTrustDialogAccepted: true" in script
