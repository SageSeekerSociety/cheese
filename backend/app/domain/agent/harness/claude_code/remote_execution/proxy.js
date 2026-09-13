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

function directChatSend(tool, args) {
  if (tool !== "Bash" || typeof args.command !== "string") return null;
  const command = args.command.trim();
  const match = command.match(/^cheese\s+chat\s+send\s+(['"])([^'"\n]*)\1$/);
  if (!match || /[;&|<>`$]/.test(command)) return null;
  const api = (process.env.CHEESE_API || "").replace(/\/$/, "");
  const token = process.env.CHEESE_TOKEN || "";
  const topic = process.env.CHEESE_TOPIC || "";
  if (!api || !token || !topic || !match[2].trim()) return null;
  const requestId = crypto.randomUUID();
  return fetch(`${api}/topics/${topic}/messages`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Cheese-Token": token,
      ...(process.env.CHEESE_TURN ? {"X-Cheese-Turn": process.env.CHEESE_TURN} : {}),
    },
    body: JSON.stringify({content: match[2], request_id: requestId}),
  }).then(async response => {
    if (!response.ok) throw new Error(`chat send failed: HTTP ${response.status}`);
    const payload = await response.json();
    return {result: {
      stdout: JSON.stringify(payload.data ?? payload),
      stderr: `[cheese] request_id=${requestId}`,
      interrupted: false,
      noOutputExpected: false,
      returnCodeInterpretation: "Exit code 0",
    }};
  });
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
          const direct = await directChatSend(tool, args);
          if (direct) return direct;
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
