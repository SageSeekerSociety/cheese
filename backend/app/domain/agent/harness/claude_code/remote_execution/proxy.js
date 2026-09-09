const execution = __EXECUTION_CONFIG__;
const native = new Set([
  "Read", "Edit", "Write", "Bash", "NotebookEdit", "TaskOutput", "TaskStop",
]);

function remotePath(path) {
  if (path.startsWith(execution.central_config + "/skills/")) {
    return execution.workspace + "/.claude/skills/" + path.slice((execution.central_config + "/skills/").length);
  }
  const root = execution.central_workspace;
  return path === root || path.startsWith(root + "/")
    ? execution.workspace + path.slice(root.length)
    : path;
}

async function publish($, event, e, args, response) {
    if (!execution.central_hooks?.[event]?.length) return {};
    const payload = {
      hook_event_name: event, session_id: await $.session.id(),
      tool_name: e.tool, tool_use_id: e.tool_use_id, tool_input: args,
      cwd: execution.workspace,
    };
    if (event === "PostToolUse") payload.tool_response = response;
    const result = await $.process.run([...execution.helper, "event", execution.target_file], {
      stdin: JSON.stringify(payload),
    });
    if (result.exitCode !== 0) throw new Error(result.stderr);
    return JSON.parse(result.stdout || "{}");
}

export function register(on) {
  on("tool.call", async ($, e, next) => {
    const { tool, tool_use_id, ...args } = e;
    if (native.has(tool)) {
      for (const field of ["file_path", "path", "notebook_path"]) {
        if (typeof args[field] === "string") args[field] = remotePath(args[field]);
      }
      try {
        const decision = await publish($, "PreToolUse", e, args);
        if (decision.deny) return decision;
        const input = decision.hookSpecificOutput?.updatedInput || args;
        const response = await $.process.run([...execution.helper, "invoke", execution.target_file], {
          stdin: JSON.stringify({id: tool_use_id, tool, args: input}),
          timeoutMs: 600000,
        });
        if (response.exitCode !== 0) return { deny: response.stderr };
        const receipt = JSON.parse(response.stdout);
        if (receipt.error) return { deny: receipt.error };
        const value = receipt.value;
        await publish($, "PostToolUse", e, input, value);
        return { result: value };
      } catch (error) {
        return { deny: "Remote execution failed: " + String(error) };
      }
    }
    for (const server of execution.mcp_servers || []) {
      const prefix = "mcp__" + server + "__";
      if (tool.startsWith(prefix)) {
        try {
          const response = await $.mcp.call(server, tool.slice(prefix.length), args);
          if (response.isError) return { deny: JSON.stringify(response.content) };
          return { result: response.content };
        } catch (error) {
          return { deny: "Remote MCP failed: " + String(error) };
        }
      }
    }
    return next(e);
  });

  on("prompt.section", async ($, e, next) => {
    const result = await next(e);
    return { ...result, text: result.text === null ? null : result.text.split(execution.central_workspace).join(execution.workspace) };
  });
  on("skill.prompt", async ($, e, next) => {
    const result = await next(e);
    return { text: result.text.split(execution.central_config + "/skills/").join(execution.workspace + "/.claude/skills/")
      .split(execution.central_workspace).join(execution.workspace) };
  });
}
