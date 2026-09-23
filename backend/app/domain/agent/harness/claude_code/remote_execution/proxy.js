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

// The model is told the executor's spelling of every path (prompt.section
// rewrites them), while $.fs sees this host — where a forwarded workspace sits
// at central_workspace. Try both so a SendUserFile path works whichever
// spelling the model used, and whichever side of the mount the name resolves on.
function localPaths(path) {
  const seen = [path];
  const root = execution.central_workspace;
  const remote = execution.workspace;
  if (path === remote || path.startsWith(remote + "/")) {
    seen.push(root + path.slice(remote.length));
  } else if (!path.startsWith("/") && root) {
    seen.push(root + "/" + path);
  }
  return seen;
}

const SEND_USER_FILE_MAX_BYTES = 10 * 1024 * 1024;

async function sendUserFile($, tool_use_id, args) {
  const files = [];
  for (const entry of args.files || []) {
    if (typeof entry !== "string") {
      return {
        deny: "SendUserFile cannot deliver a pre-resolved {file_uuid, file_name, size, is_image} object; pass a file path instead",
      };
    }
    const path = entry;
    const name = path.replace(/\\/g, "/").split("/").pop() || "file";
    let data_b64;
    let upload_error;
    for (const candidate of localPaths(path)) {
      try {
        const stat = await $.fs.stat(candidate, { resolve: false });
        if (stat.kind !== "file") continue;
        if (stat.size > SEND_USER_FILE_MAX_BYTES) {
          upload_error = `file is over the ${SEND_USER_FILE_MAX_BYTES / (1024 * 1024)}MB limit`;
          break;
        }
        data_b64 = (await $.fs.read(candidate, { as: "bytes" })).base64;
        break;
      } catch (error) {
        // $.fs.read refuses anything over its own transfer cap; the transport
        // still reads that file from the executor and applies the real limit.
        if (String(error).includes("byte limit")) break;
      }
    }
    files.push({
      path,
      name,
      ...(data_b64 === undefined ? {} : { data_b64 }),
      ...(upload_error === undefined ? {} : { upload_error }),
    });
  }
  const response = await $.mcp.call("native", "send_user_file", {
    ...args,
    files,
    id: tool_use_id,
    session_id: await $.session.id(),
  });
  if (response.isError) return { deny: JSON.stringify(response.content) };
  return JSON.parse(response.content[0].text);
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
        // Only a non-empty half becomes a block. A `text` block holding the
        // empty string is not harmless padding: a provider that validates text
        // content rejects the WHOLE request over it (Moonshot's Anthropic
        // endpoint answers 400 "Invalid request: text content is empty"), and
        // the block then stays in the conversation — so one platform_request,
        // whose receipt is always `{"stdout": body, "stderr": ""}`, would wedge
        // every later turn in that room.
        const result = [outcome.result.stdout, outcome.result.stderr]
          .filter((text) => typeof text === "string" && text !== "")
          .map((text) => ({ type: "text", text }));
        return { result };
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
        let outcome = JSON.parse(response.content[0].text);
        if (outcome.receipt_path) {
          const receipt = await $.fs.read(outcome.receipt_path, { as: "text" });
          outcome = JSON.parse(receipt);
        }
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
    // SendUserFile is the build's own upload of a local file to whoever is
    // watching. Left alone it POSTs to Anthropic's /api/oauth/file_upload,
    // which this deployment's scoped token cannot authenticate — the tool
    // reports 401 and the room never sees the file. Deliver through the room's
    // own route instead, so the caller gets the result shape it expects.
    if (tool === "SendUserFile") {
      try {
        return await sendUserFile($, tool_use_id, args);
      } catch (error) {
        return { deny: "Cheese delivery failed: " + String(error) };
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
