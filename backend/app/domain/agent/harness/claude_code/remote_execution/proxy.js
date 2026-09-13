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

function directChatContent(tool, args) {
  if (tool !== "Bash" || typeof args.command !== "string") return null;
  const command = args.command.trim();
  const match = command.match(/^cheese\s+chat\s+send\s+(['"])([^'"\n]*)\1$/);
  if (!match || /[;&|<>`$]/.test(command)) return null;
  return match[2].trim() ? match[2] : null;
}

export function register(on) {
  on("tool.call", async ($, e, next) => {
    const { tool, tool_use_id, ...args } = e;
    if (native.has(tool)) {
      for (const field of ["file_path", "path", "notebook_path"]) {
        if (typeof args[field] === "string") args[field] = remotePath(args[field]);
      }
      if (tool === "Bash") {
        try {
          const content = directChatContent(tool, args);
          if (content) {
            const response = await $.mcp.call("native", "publish_chat", {content});
            if (response.isError) throw new Error(JSON.stringify(response.content));
            return {result: {
              stdout: response.content?.[0]?.text || "{}",
              stderr: "[cheese] published via resident transport",
              interrupted: false,
              noOutputExpected: false,
              returnCodeInterpretation: "Exit code 0",
            }};
          }
        } catch (error) {
          return { deny: "Direct chat publication failed: " + String(error) };
        }
      }
      try {
        const response = await $.mcp.call("native", "invoke", {
          id: tool_use_id, tool, args, session_id: await $.session.id(),
        });
        if (response.isError) return { deny: JSON.stringify(response.content) };
        return JSON.parse(response.content[0].text);
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
