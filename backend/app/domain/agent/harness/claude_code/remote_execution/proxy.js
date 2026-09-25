const execution = __EXECUTION_CONFIG__;
const native = new Set(["Read", "Edit", "Write", "NotebookEdit"]);

// The session sees the project at the executor's own path (`client.py`
// `enter`), so paths need no respelling. Its skills are the exception: the
// build reads them from this host's config directory, and they are the
// project's, on the executor.
const skills = execution.central_config + "/skills/";
const projectSkills = execution.session_workspace + "/.claude/skills/";

function remotePath(path) {
  return path.startsWith(skills) ? projectSkills + path.slice(skills.length) : path;
}

// A Bash command's output is on this host, where the build wrote it: a
// background task's output file under the session's temp directory, a large
// result under the transcript's tool-results. Reading one is a local read, as
// it is in a native session; the PreToolUse guard admits nothing else.
function ownOutput(path) {
  if (typeof path !== "string" || path.split("/").includes("..")) return false;
  return path.startsWith(execution.central_tmp + "/")
    || (path.startsWith(execution.central_config + "/projects/") && path.includes("/tool-results/"));
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
    try {
      const stat = await $.fs.stat(path, { resolve: false });
      if (stat.kind === "file" && stat.size > SEND_USER_FILE_MAX_BYTES) {
        upload_error = `file is over the ${SEND_USER_FILE_MAX_BYTES / (1024 * 1024)}MB limit`;
      } else if (stat.kind === "file") {
        data_b64 = (await $.fs.read(path, { as: "bytes" })).base64;
      }
    } catch {
      // Not readable here (or over $.fs.read's own transfer cap): the
      // transport reads it from the executor and applies the real limit.
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
    // The pinned executor's `mcp serve` does not expose these tools. Never
    // fall through to a search on the conversation host.
    if (tool === "Glob" || tool === "Grep") {
      return { deny: `${tool} is unavailable on the executor; use Bash with rg or find to search the work machine.` };
    }
    // Both isolation modes build on this host: "worktree" creates `.claude/`
    // inside the working directory, a read-only view of a project that lives on
    // the executor, and "remote" is unavailable to this build, which then falls
    // back to "worktree". Either way the spawn dies on EROFS, and the subagent
    // never exists. A subagent without it already runs its tools remotely.
    if (tool === "Agent" && args.isolation) {
      return { deny: `Agent isolation "${args.isolation}" is unavailable here because the project lives on the work machine; omit isolation, since the subagent's file and shell tools already run there. Only when the work is itself a deliverable to track and review, create it with \`cheese_task\`, prepare its directory with \`cheese worktree <id>\`, and give the subagent that directory.` };
    }
    if (tool === "mcp__native__chat_send" || tool === "mcp__native__platform_request" || tool.startsWith("mcp__native__cheese_")) {
      try {
        const response = await $.mcp.call("native", tool.slice("mcp__native__".length), {
          ...args, id: tool_use_id, session_id: await $.session.id(),
        });
        if (response.isError) return { deny: JSON.stringify(response.content) };
        let outcome = JSON.parse(response.content[0].text);
        if (outcome.receipt_path) {
          outcome = JSON.parse(await $.fs.read(outcome.receipt_path, { as: "text" }));
        }
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
    if (tool === "Read" && ownOutput(args.file_path)) return next(e);
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

  on("skill.prompt", async ($, e, next) => {
    const result = await next(e);
    return { text: result.text.split(skills).join(projectSkills) };
  });
}
