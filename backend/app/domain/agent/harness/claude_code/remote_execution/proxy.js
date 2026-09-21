const execution = __EXECUTION_CONFIG__;
const native = new Set([
  "Read", "Edit", "Write", "Bash", "NotebookEdit", "TaskStop",
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

export function register(on) {
  on("tool.call", async ($, e, next) => {
    // agentId identifies the caller; native MCP tools reject it as an argument.
    const { tool, tool_use_id, agentId, ...args } = e;
    if (tool === "mcp__native__chat_send" || tool === "mcp__native__platform_request" || tool.startsWith("mcp__native__cheese_")) {
      try {
        const response = await $.mcp.call("native", tool.slice("mcp__native__".length), {
          ...args, id: tool_use_id, session_id: await $.session.id(),
        });
        if (response.isError) return { deny: JSON.stringify(response.content) };
        const outcome = JSON.parse(response.content[0].text);
        if (outcome.deny) return outcome;
        return { result: [{ type: "text", text: outcome.result.stdout }, { type: "text", text: outcome.result.stderr }] };
      } catch (error) {
        return { deny: "Cheese tool failed: " + String(error) };
      }
    }
    if (native.has(tool)) {
      for (const field of ["file_path", "path", "notebook_path"]) {
        if (typeof args[field] === "string") args[field] = remotePath(args[field]);
      }
      try {
        const response = await $.mcp.call("native", "invoke", {
          id: tool_use_id, tool, args, session_id: await $.session.id(),
        });
        if (response.isError) return { deny: JSON.stringify(response.content) };
        const outcome = JSON.parse(response.content[0].text);
        // 一个 TaskStop 的 id 有两个主人：执行机上后台跑着的那条命令，和这条会话
        // 里起着的一条子线程。执行器只认前者——它答「不认识」的那个 id 就是后者，
        // 让回给 harness 自己停（结论 43「父线程能停掉它」）。判据是执行器认不认
        // 得，不是 id 长什么样：两种 id 都是机器自己发的，长得一样。
        if (tool === "TaskStop" && outcome.deny === "Unknown remote task") return next();
        if (outcome.result?.type === "image") {
          outcome.result.file.base64 = response.content.find(block => block.type === "image").source.data;
        }
        return outcome;
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
