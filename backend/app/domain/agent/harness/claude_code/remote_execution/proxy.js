const execution = __EXECUTION_CONFIG__;
// The native server's platform tools: every row of `PLATFORM_TOOLS` in the
// shipped cheese.py, written in with the config (`release.hook_module`).
// Membership decides, never a name prefix: `todo_write` carries none, and a
// call that misses this set reaches the server without its `id`.
const platformTools = new Set(__PLATFORM_TOOLS__.map((name) => "mcp__native__" + name));
const native = new Set(["Read", "Edit", "Write", "NotebookEdit"]);

// The session sees the project at the executor's own path (`client.py`
// `enter`), so paths need no respelling. Its skills are the exception: the
// build reads them all from this host's config directory. A project's are the
// project's, on the executor; one the platform shipped is in the executor's
// own config directory (`client.py` `skill_paths`, the same mapping).
const skills = execution.central_config + "/skills/";
const projectSkills = execution.session_workspace + "/.claude/skills/";
const shipped = new Set(execution.shipped_skills || []);
// Skills the session names by an entry that is not the project's root skill of
// that name — a subdirectory's, or one written without its `paths` — with where
// the executor holds each and what Claude Code adds to its description. They
// change while the session runs (`release.touch_skills`), so they are read
// each time (`client.py` `skill_places` reads the same file).
const sessionDir = (execution.target_file || "").replace(/[^/]*$/, "");
const placesFile = sessionDir && sessionDir + "skill-places.json";
// The one native Read this plugin lets through the session's PreToolUse guard
// (`client.py` `probed`): its look at a file before the executor reads it.
const probeFile = sessionDir && sessionDir + "read-probe.json";

async function skillPlaces($) {
  if (!placesFile) return { places: {}, scoped: false };
  try {
    return JSON.parse(await $.fs.read(placesFile, { as: "text" }));
  } catch {
    return { places: {}, scoped: false };
  }
}

function skillPaths(text, places = {}) {
  let out = "";
  let at = 0;
  for (;;) {
    const found = text.indexOf(skills, at);
    if (found < 0) return out + text.slice(at);
    const rest = text.slice(found + skills.length);
    const name = /^[^/\s'"`]+/.exec(rest);
    out += text.slice(at, found);
    if (!name) {
      out += skills;
    } else if (places[name[0]]) {
      out += places[name[0]].place;
    } else if (shipped.has(name[0])) {
      out += execution.executor_config
        ? execution.executor_config + "/skills/" + name[0]
        : skills + name[0];
    } else {
      out += projectSkills + name[0];
    }
    at = found + skills.length + (name ? name[0].length : 0);
  }
}

// A call that waited for its machine carries the platform's line about the
// wait (`client.py` `transport`): the model reads it after the result, as
// `context`; a refusal has no such place, so it leads the refusal's text.
function told(outcome) {
  if (!outcome.context || !outcome.deny) return outcome;
  return { deny: [...outcome.context, outcome.deny].join("\n") };
}

function remotePath(path, places) {
  return path.startsWith(skills) ? skillPaths(path, places) : path;
}

// A name Claude Code qualifies with a directory (`apps/web:deploy`) is spelled
// in the config dir with U+2215 for each "/" (`release.NAME_SLASH`).
function skillEntry(name) {
  const colon = name.lastIndexOf(":");
  return colon < 0 ? name : name.slice(0, colon).replaceAll("/", "\u2215") + name.slice(colon);
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

// The memory tree is the session's own, in its home on this host, where the
// runner reconciles it with the platform (`client.py` `prepare`,
// `central_memory`). A file tool on it runs here, however the agent spelled
// the path: `~/.cheese/memory/...` as the prompt names it, or under the home
// its shell reports, which is the executor's and holds no memory tree.
const MEMORY_TAIL = /(?:^|\/)\.cheese\/memory\/(.+)$/;

function memoryPath(path) {
  if (!execution.central_memory || typeof path !== "string") return null;
  const match = MEMORY_TAIL.exec(path);
  if (!match || match[1].split("/").includes("..")) return null;
  return execution.central_memory + "/" + match[1];
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
  return told(JSON.parse(response.content[0].text));
}

// The build runs a Bash call itself, and its shell prefix sends the command
// to the machine, so no executor call carries it past the project's
// `permissions.deny` as it carries the file tools (`runtime.py` `execute`);
// and the build never loads the project's settings to check them itself. So
// the machine that holds the project checks it first, by the same rules pi's
// and Codex's calls meet (`project_hooks.run` with `fire` false).
async function refused($, tool_use_id, tool, args) {
  try {
    const response = await $.mcp.call("native", "permission", {
      id: tool_use_id, session_id: await $.session.id(), tool, args,
    });
    if (response.isError) return { deny: JSON.stringify(response.content) };
    const outcome = JSON.parse(response.content[0].text);
    return outcome.deny ? told(outcome) : null;
  } catch (error) {
    return { deny: "Remote execution failed: " + String(error) };
  }
}

// A person's message the session has not answered yet (`driven/runner.py`,
// which decides what owes an answer, what answers it and what the refusal
// says). The runner names the file in CHEESE_REPLY_OWED (spelled out at the
// call: the loader takes only a literal variable name); its contents are all
// this side needs, so the rule is not restated here.
//
// The debt this session has already answered. Kept here rather than in the
// file: a reply and the next tool call can be in one assistant message, and
// this process sees the reply's call before the sibling's — the runner would
// only hear of it afterwards.
let answered = null;

async function owedReply($) {
  const path = await $.env.get("CHEESE_REPLY_OWED");
  if (!path || !(await $.fs.exists(path))) return null;
  try {
    const owed = JSON.parse(await $.fs.read(path, { as: "text" }));
    return owed && owed.id && owed.id !== answered ? owed : null;
  } catch {
    return null;
  }
}

// A subagent whose spawn asked for `isolation` ran anyway, with its tools on
// the executor like every other one: one working directory, shared with this
// session and with its siblings. Said out loud rather than dropped silently —
// a caller that asked for isolation is usually about to run several at once
// over the same files. Only a text result is annotated; anything else passes
// through as it came.
//
// Note what this does not reach: the pinned build launches a subagent in the
// background and answers the caller itself, so the caller sees its own
// "launched successfully" line and never this one. The annotation is what a
// caller sees where the proxy's result *is* the tool result. The spawn is not
// refused either way, which is the part that matters to the caller.
function ignoringIsolation(result) {
  if (!result || !Array.isArray(result.result)) return result;
  return {
    ...result,
    result: [
      {
        type: "text",
        text: "isolation was ignored: this subagent shares the working directory with the session and with its siblings.",
      },
      ...result.result,
    ],
  };
}

export function register(on) {
  on("tool.call", async ($, e, next) => {
    // agentId identifies the caller; native MCP tools reject it as an argument.
    const { tool, tool_use_id, agentId, ...args } = e;
    // Only the session itself answers the room. A subagent reports to it and
    // is never held back; ToolSearch is how a deferred chat_send is reached.
    if (agentId === undefined && tool !== "ToolSearch") {
      const owed = await owedReply($);
      if (owed) {
        const name = tool.startsWith("mcp__native__") ? tool.slice("mcp__native__".length) : tool;
        if (!owed.answers.includes(name)) return { deny: owed.reason };
        answered = owed.id;
        // And where the runner reads it, to know the turn may end (`insist`).
        await $.fs.write(owed.answered, owed.id);
      }
    }
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
    //
    // The parameter is dropped, not refused. Refusing it did not stop callers:
    // the field comes from the build's own Agent schema, so a session that hit
    // the refusal could only drop a field it had just been handed, and one
    // spent a whole turn re-issuing the same spawn — 140 calls, one success in
    // the turn. Dropping the field is what the refusal used to ask for, and it
    // leaves the spawn the caller wanted. The result says what was dropped: a
    // caller that asked for isolation is usually about to run several
    // subagents at once.
    if (tool === "Agent" && args.isolation) {
      const { isolation, ...spawn } = e;
      return ignoringIsolation(await next(spawn));
    }
    if (platformTools.has(tool)) {
      try {
        const response = await $.mcp.call("native", tool.slice("mcp__native__".length), {
          ...args, id: tool_use_id, session_id: await $.session.id(),
        });
        if (response.isError) return { deny: JSON.stringify(response.content) };
        let outcome = JSON.parse(response.content[0].text);
        if (outcome.receipt_path) {
          outcome = JSON.parse(await $.fs.read(outcome.receipt_path, { as: "text" }));
        }
        if (outcome.deny) return told(outcome);
        // Only a non-empty half becomes a block. A `text` block holding the
        // empty string is not harmless padding: a provider that validates text
        // content rejects the WHOLE request over it (Moonshot's Anthropic
        // endpoint answers 400 "Invalid request: text content is empty"), and
        // the block then stays in the conversation — so one platform tool,
        // whose receipt is always `{"stdout": text, "stderr": ""}`, would wedge
        // every later turn in that room.
        const result = [outcome.result.stdout, outcome.result.stderr]
          .filter((text) => typeof text === "string" && text !== "")
          .map((text) => ({ type: "text", text }));
        return outcome.context ? { result, context: outcome.context } : { result };
      } catch (error) {
        return { deny: "Cheese tool failed: " + String(error) };
      }
    }
    if (tool === "Read" && ownOutput(args.file_path)) return next(e);
    if (tool === "Skill" && typeof args.skill === "string" && args.skill.includes("/")) {
      return next({ ...e, skill: skillEntry(args.skill) });
    }
    if (native.has(tool)) {
      for (const field of ["file_path", "notebook_path"]) {
        const local = memoryPath(args[field]);
        if (local) return next({ ...e, [field]: local });
      }
      const known = await skillPlaces($);
      if (tool === "Read" && known.scoped) {
        // The session's own Read, whose result is not used: before it opens
        // the file it offers any skill whose `paths` names it, in this very
        // result, as a native session does (Write and Edit cannot be looked
        // at this way; `release.touch_skills` covers them). Bounded, since the
        // file is looked up in the view, and the view asks the executor.
        await $.fs.write(probeFile, JSON.stringify({ path: args.file_path }));
        await Promise.race([
          next(e).catch(() => undefined),
          $.clock.sleep(5000),
        ]);
        await $.fs.write(probeFile, "{}");
      }
      for (const field of ["file_path", "path", "notebook_path"]) {
        if (typeof args[field] === "string") args[field] = remotePath(args[field], known.places);
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
        return told(outcome);
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
    // The machine's stdio servers and the teammate's type's (`agent_mcp`, whose
    // definitions the bridge hands the machine with each call).
    for (const server of [...(execution.mcp_servers || []), ...Object.keys(execution.agent_mcp || {})]) {
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
    if (tool === "Bash") {
      const denied = await refused($, tool_use_id, tool, args);
      if (denied) return denied;
    }
    return next(e);
  });

  on("skill.prompt", async ($, e, next) => {
    const result = await next(e);
    return { text: skillPaths(result.text, (await skillPlaces($)).places) };
  });

  // A subdirectory's skill is listed with the directory it applies to, as
  // Claude Code lists it (`release.link_forwarded_user_context`).
  on("prompt.attachment", async ($, e, next) => {
    const result = await next(e);
    if (e.type !== "skill_listing") return result;
    const { places } = await skillPlaces($);
    const text = result.text.replace(/^- ([^:\n]+(?::[^:\s]+)?): (.*)$/gm, (line, name, rest) =>
      places[name]?.note ? `- ${name}: ${rest}${places[name].note}` : line);
    return { text };
  });
}
