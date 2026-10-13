"""The PreToolUse guard on the native file tools a central session runs.

The remote execution plugin (`proxy.js`) sends those tools to the executor; a
call that still reaches the build here was not handled there, and running it
would act on the session host. The guard admits the few calls that belong on
this host and refuses the rest (`client.py` registers it, mode `guard`).
"""

import json
import os
from pathlib import Path


def guard_decision(config, call, native_tools):
    """None to let ``call`` run here, or the hook's deny decision."""
    if (
        own_output(config, call)
        or own_memory(config, call, native_tools)
        or probed(config, call)
    ):
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "Central execution is disabled; "
            "the remote execution plugin did not handle this call",
        }
    }


def own_output(config, call):
    """A Read of what the build wrote about a command on this host: a
    background task's output file, or a large result it persisted. That read
    is local in a native session too; any other native call is refused."""
    if call.get("tool_name") != "Read":
        return False
    path = os.path.realpath(str((call.get("tool_input") or {}).get("file_path", "")))
    temporary = os.path.realpath(config.get("central_tmp", "")) + os.sep
    projects = os.path.realpath(config["central_config"]) + os.sep + "projects" + os.sep
    return (
        bool(config.get("central_tmp"))
        and path.startswith(temporary)
        or (
            path.startswith(projects)
            and os.sep + "tool-results" + os.sep in path[len(projects) :]
        )
    )


def probed(config, call):
    """The session's own Read of a project file, which `proxy.js` lets through
    to have the session offer the skills whose `paths` name it; the executor
    answers the call. Only the path the plugin named for it is admitted."""
    if call.get("tool_name") != "Read":
        return False
    try:
        named = json.loads(
            (Path(config["target_file"]).parent / "read-probe.json").read_text()
        ).get("path")
    except (OSError, ValueError, KeyError):
        return False
    return bool(named) and (call.get("tool_input") or {}).get("file_path") == named


def own_memory(config, call, native_tools):
    """A file tool on the session's memory tree, which lives on this host
    (`prepare`'s `central_memory`). The plugin has already spelled the path
    out; anything that resolves outside the tree is not admitted."""
    if call.get("tool_name") not in native_tools or not config.get("central_memory"):
        return False
    tool_input = call.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    memory = os.path.realpath(config["central_memory"]) + os.sep
    return os.path.realpath(str(path)).startswith(memory)
