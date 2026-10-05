// 平台给 pi 的那一份 extension。
//
// pi is somebody else's agent, and every one of these is written for a person
// sitting at a terminal talking to it alone. A Cheese room is neither: the
// output of a turn is not published anywhere, the people in it are several,
// and the work outlives the turn that started it. What this file adds is the
// part of that pi has no notion of — and nothing else. pi's loop, its tools,
// its context handling are its own.
//
// pi runs on the session host and the project is on the room's machine, so
// pi's own tools — read, write, edit, bash, ls, find, grep — are pi's, with the file and
// process operations under them handed to the runner, which runs them there
// (`machine.py`, #1106). pi's loop is unchanged; where its hands are is not.
//
// Written by the runner into the session's state directory and named with
// `--extension`, the same way the room's skills and system prompt are: they
// are assembled per room, and the session host has no copy to point at.

import {
  createBashTool,
  createEditTool,
  createFindTool,
  createGrepTool,
  createLsTool,
  createReadTool,
  createWriteTool,
  DEFAULT_MAX_BYTES,
  formatSize,
  truncateHead,
  truncateLine,
} from "@earendil-works/pi-coding-agent";
import * as fs from "node:fs";
import * as net from "node:net";
import * as path from "node:path";

type ToolSpec = {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
};

type Manifest = {
  socket: string;
  state: string;
  // False for a session with nothing to work on but a conversation (a
  // person's 芝士): no machine behind it, and only the tools listed.
  hands?: boolean;
  // A session without hands that reads the room's machine (a document's 芝士):
  // pi's read, ls, find and grep on the room's checkout, and nothing else of it.
  reading?: boolean;
  workspace: string;
  jobs: string;
  tools: ToolSpec[];
  unavailable: string;
  mcp: ToolSpec[];
  notice: string;
  subagents: boolean;
};

const HOME = process.env.CHEESE_PI_EXTENSION ?? "";

function manifest(): Manifest {
  const empty: Manifest = {
    socket: "",
    state: "",
    workspace: "",
    jobs: "",
    tools: [],
    unavailable: "the runner wrote no manifest",
    mcp: [],
    notice: "",
    subagents: false,
  };
  if (!HOME) return empty;
  try {
    return JSON.parse(fs.readFileSync(path.join(HOME, "platform.json"), "utf8"));
  } catch (error: any) {
    return { ...empty, unavailable: String(error?.message ?? error) };
  }
}

// One line out, one line back, a fresh connection each time — the runner's
// socket answers one request per connection and closes. Calls are independent,
// so parallel tool calls need no queue of our own.
function ask(socket: string, method: string, params: unknown): Promise<any> {
  return new Promise((resolve, reject) => {
    const connection = net.connect(socket);
    let received = "";
    connection.setEncoding("utf8");
    connection.on("error", reject);
    connection.on("data", (chunk: string) => {
      received += chunk;
    });
    connection.on("close", () => {
      if (!received) return reject(new Error("the session runner said nothing"));
      let answer: any;
      try {
        answer = JSON.parse(received);
      } catch {
        return reject(new Error(`unreadable answer: ${received.slice(0, 400)}`));
      }
      if (answer.error) return reject(new Error(answer.error));
      resolve(answer.result);
    });
    connection.write(JSON.stringify({ method, params }) + "\n");
  });
}

function text(body: string) {
  return { content: [{ type: "text" as const, text: body }] };
}

// --- the platform's own tools ------------------------------------------------
//
// pi has no MCP client, so these arrive as extension tools. The catalog is
// the runner's, read from the platform's own file: the platform's tool table
// (the same one the other harnesses serve) plus the CLI commands that run on
// the room's machine as a process.

function registerPlatformTools(pi: any, spec: Manifest) {
  for (const tool of spec.tools) {
    pi.registerTool({
      name: tool.name,
      description: tool.description,
      parameters: tool.inputSchema,
      async execute(id: string, params: any) {
        const result = await ask(spec.socket, "cli", {
          id,
          tool: tool.name,
          arguments: params ?? {},
          cwd: spec.workspace,
        });
        const body = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
        if (result.status !== 0) {
          return {
            ...text(body || `${tool.name} 以状态 ${result.status} 结束`),
            isError: true,
          };
        }
        return text(body || "done");
      },
    });
  }
}

// --- the project's MCP servers ----------------------------------------------
//
// The runner is pi's MCP client (mcp.py): it listed every server's tools —
// the machine's stdio servers and the platform's remote ones — under the name
// the other harnesses give them, `mcp__<server>__<tool>`. A call goes back to
// the runner, which sends it to the server that owns the tool, with the
// project's PreToolUse and PostToolUse hooks run around it on the machine.
//
// A failed call is thrown, not returned: pi marks a tool result as an error
// only when `execute` throws, whatever the returned object says.

function registerMcpTools(pi: any, spec: Manifest) {
  for (const tool of spec.mcp ?? []) {
    pi.registerTool({
      name: tool.name,
      description: tool.description,
      parameters: tool.inputSchema,
      async execute(id: string, params: any) {
        const result = await ask(spec.socket, "mcp", {
          id,
          tool: tool.name,
          arguments: params ?? {},
        });
        if (result.isError) {
          const said = result.content
            .map((item: any) => (item.type === "text" ? item.text : `[${item.type}]`))
            .join("\n")
            .trim();
          throw new Error(said || `${tool.name} failed`);
        }
        return { content: result.content };
      },
    });
  }
}

// --- the project's hooks around pi's own tools --------------------------------
//
// Claude Code fires the project's PreToolUse and PostToolUse hooks around every
// tool call, and on Codex the executor runs them around every call it makes. pi
// fires none, so every call of pi's own (read, bash, edit, write, the
// background shell) asks the runner before it runs and after it returns, and
// the room's machine runs the hooks under the name each one is written for
// (hooks.py).
//
// Two kinds of call are not asked about here. An MCP tool's hooks run around
// the call itself (mcp.py). A platform tool runs none, as on the executor,
// which runs no project hook around the platform's own tools.
//
// A PreToolUse block stops the call and pi hands the model its reason as the
// tool's error; an `updatedInput` is written into the call's input, which pi
// runs as mutated. A handler that throws — the runner unreachable — blocks
// the call too: pi's rule, and the one a guard needs. PostToolUse runs only
// after a call that succeeded, as Claude Code's does, and a block from it is
// added to the result, since the call has already happened.

function applyProjectHooks(pi: any, spec: Manifest) {
  const skipped = new Set([...spec.tools, ...(spec.mcp ?? [])].map((tool) => tool.name));

  pi.on("tool_call", async (event: any) => {
    if (skipped.has(event.toolName)) return;
    const answer = await ask(spec.socket, "hooks", {
      event: "PreToolUse",
      tool: event.toolName,
      id: event.toolCallId,
      input: event.input,
      cwd: spec.workspace,
    });
    if (answer.denied !== undefined) return { block: true, reason: answer.denied };
    for (const key of Object.keys(event.input)) {
      if (!(key in answer.input)) delete event.input[key];
    }
    Object.assign(event.input, answer.input);
  });

  pi.on("tool_result", async (event: any) => {
    if (skipped.has(event.toolName) || event.isError) return;
    const answer = await ask(spec.socket, "hooks", {
      event: "PostToolUse",
      tool: event.toolName,
      id: event.toolCallId,
      input: event.input,
      cwd: spec.workspace,
      result: { content: event.content },
    });
    if (answer.denied === undefined) return;
    return {
      content: [
        ...event.content,
        { type: "text", text: `PostToolUse hook: ${answer.denied}` },
      ],
    };
  });
}

// --- where the session is, and what the repository says ----------------------
//
// pi tells the model the directory it runs in, which on the session host holds
// nothing of the project. The project is at `workspace` on the room's machine,
// and that is the directory every tool here works in, so that is the one the
// model is told.
//
// What the repository says about itself (its AGENTS.md, CLAUDE.md and rules) is
// read on the machine by the runner (repository.py) and given to every turn.
//
// Both are said through pi's own prompt sections, never by rewriting the
// rendered text. pi 1.0 renders the system prompt from
// `event.systemPromptOptions` and diffs it against what the transcript already
// holds, so a section we set is a delta pi records and the model reads; a
// string substitution across the rendered prompt, which is what this did for
// pi 0.85.1, is a guess about wording pi owns — and 1.0 changed that wording
// ("Current working directory: <dir>" became a `<cwd>` block), which made the
// rewrite a no-op that said nothing.

/** The prompt options of a `before_agent_start`, or nothing with the reason
 *  written where a launch failure is read.
 *
 * A pi that does not carry them cannot be told where the session is, and every
 * tool below works on the room's machine — so a directory left as the session
 * host's is a session working in the wrong place. Saying so on stderr is the
 * point: this is the shape that broke silently once. */
function promptOptions(event: any): any {
  const options = event.systemPromptOptions;
  if (options) return options;
  process.stderr.write("[cheese] before_agent_start carries no systemPromptOptions\n");
  return undefined;
}

function placeTheSession(pi: any, spec: Manifest) {
  pi.on("before_agent_start", async (event: any) => {
    const options = promptOptions(event);
    if (!options) return;
    if (spec.workspace) options.cwd = spec.workspace;
    const { context } = spec.socket ? await ask(spec.socket, "context", {}) : { context: "" };
    // Appended as a section of its own, which pi renders after the rest and
    // records as a delta: the prompt before it is identical on every turn of
    // the session, and that is what a provider cache matches on.
    if (context) options.sections.repository = context;
    else delete options.sections.repository;
  });
}

// --- pi's own tools, with their hands on the room's machine --------------------
//
// pi's read, write, edit, ls and find are pi's — their schemas, their limits,
// their output — built on the file operations below, each of which is one
// request to the runner and one call the machine's executor answers itself
// (`machine_files.py`). A path the model gives is resolved against the
// workspace, as pi resolves it against its own directory.

function files(spec: Manifest) {
  const asked = (operation: string, filePath: string, more: object = {}) =>
    ask(spec.socket, "files", { operation, path: filePath, ...more });
  const readFile = async (filePath: string) =>
    Buffer.from((await asked("read", filePath)).data, "base64");
  const writeFile = async (filePath: string, content: string) => {
    await asked("write", filePath, { data: Buffer.from(content, "utf8").toString("base64") });
  };
  return {
    read: {
      readFile,
      access: async (filePath: string) => {
        await asked("access", filePath);
      },
      detectImageMimeType: async (filePath: string) =>
        (await asked("image", filePath)).type ?? null,
    },
    write: {
      writeFile,
      mkdir: async (dir: string) => {
        await asked("mkdir", dir);
      },
    },
    edit: {
      readFile,
      writeFile,
      access: async (filePath: string) => {
        await asked("access", filePath, { write: true });
      },
    },
    ls: listing(asked),
    find: {
      exists: async (filePath: string) => (await asked("stat", filePath)).exists,
      glob: async (pattern: string, cwd: string, options: { limit: number }) =>
        (await asked("glob", cwd, { pattern, limit: options.limit })).paths,
    },
  };
}

// pi's ls stats every entry it lists. The listing already says which entries
// are directories, so those stats are answered from it rather than each being
// a command on the machine. What it says holds for one ls: each starts by
// asking whether its directory exists, and that forgets the last one's.
function listing(asked: (operation: string, filePath: string) => Promise<any>) {
  const known = new Map<string, { exists: boolean; directory?: boolean }>();
  const stat = async (filePath: string) => {
    const seen = known.get(filePath) ?? (await asked("stat", filePath));
    known.set(filePath, seen);
    return seen;
  };
  return {
    exists: async (filePath: string) => {
      known.clear();
      return (await stat(filePath)).exists;
    },
    stat: async (filePath: string) => {
      const seen = await stat(filePath);
      if (!seen.exists) throw new Error(`ENOENT: no such file or directory, stat '${filePath}'`);
      return { isDirectory: () => Boolean(seen.directory) };
    },
    readdir: async (dir: string) => {
      const { entries } = await asked("list", dir);
      for (const [name, directory] of entries) {
        known.set(path.join(dir, name), { exists: true, directory });
      }
      return entries.map(([name]: [string, boolean]) => name);
    },
  };
}

// pi's grep takes no operations for the search itself: it runs ripgrep where
// pi runs. So the search is run on the machine, and what it found is written
// out the way pi's grep writes it — the same lines, limits and notices.
const GREP_MAX_LINE_LENGTH = 500; // pi's own, which `truncateLine` cuts to

function grepOnTheMachine(spec: Manifest) {
  const tool = createGrepTool(spec.workspace);
  return {
    ...tool,
    async execute(_id: string, params: any, signal: any) {
      if (signal?.aborted) throw new Error("Operation aborted");
      const limit = Math.max(1, params.limit ?? 100);
      const found = await ask(spec.socket, "files", {
        operation: "grep",
        path: path.resolve(spec.workspace, params.path || "."),
        pattern: params.pattern,
        glob: params.glob,
        ignoreCase: params.ignoreCase,
        literal: params.literal,
        context: params.context,
        limit,
      });
      if (signal?.aborted) throw new Error("Operation aborted");
      if (found.matches.length === 0) {
        return { content: [{ type: "text", text: "No matches found" }], details: undefined };
      }
      let linesTruncated = false;
      const lines: string[] = [];
      for (const match of found.matches) {
        if (match.lines.length === 0) {
          lines.push(`${match.path}:${match.line}: (unable to read file)`);
          continue;
        }
        for (const [number, text] of match.lines) {
          const cut = truncateLine(text);
          if (cut.wasTruncated) linesTruncated = true;
          lines.push(
            number === match.line
              ? `${match.path}:${number}: ${cut.text}`
              : `${match.path}-${number}- ${cut.text}`,
          );
        }
      }
      const truncation = truncateHead(lines.join("\n"), { maxLines: Number.MAX_SAFE_INTEGER });
      let output = truncation.content;
      const details: any = {};
      const notices: string[] = [];
      if (found.limited) {
        notices.push(`${limit} matches limit reached. Use limit=${limit * 2} for more, or refine pattern`);
        details.matchLimitReached = limit;
      }
      if (truncation.truncated) {
        notices.push(`${formatSize(DEFAULT_MAX_BYTES)} limit reached`);
        details.truncation = truncation;
      }
      if (linesTruncated) {
        notices.push(`Some lines truncated to ${GREP_MAX_LINE_LENGTH} chars. Use read tool to see full lines`);
        details.linesTruncated = true;
      }
      if (notices.length > 0) output += `\n\n[${notices.join(". ")}]`;
      return {
        content: [{ type: "text", text: output }],
        details: Object.keys(details).length > 0 ? details : undefined,
      };
    },
  };
}

// The shell under pi's bash: a command started on the machine and read from an
// offset until it ends. Stopping it — the turn aborted, its timeout reached —
// signals it there; it is read to its end either way, so what it printed on the
// way out is not lost.
function shell(spec: Manifest) {
  return {
    async exec(command: string, cwd: string, options: any): Promise<{ exitCode: number | null }> {
      if (options.signal?.aborted) throw new Error("aborted");
      const { id } = await ask(spec.socket, "shell", { operation: "start", command, cwd });
      let stopped: "aborted" | "timeout" | null = null;
      const stop = (why: "aborted" | "timeout") => {
        if (stopped) return;
        stopped = why;
        ask(spec.socket, "shell", { operation: "signal", id, signal: 9 }).catch(() => {});
      };
      const onAbort = () => stop("aborted");
      options.signal?.addEventListener?.("abort", onAbort, { once: true });
      const timer = options.timeout
        ? setTimeout(() => stop("timeout"), options.timeout * 1000)
        : null;
      try {
        let offset = 0;
        for (;;) {
          const read = await ask(spec.socket, "shell", { operation: "read", id, offset, wait: 1 });
          const data = Buffer.from(read.data, "base64");
          if (data.length) options.onData(data);
          offset = read.offset;
          if (read.lost) throw new Error("执行机上的这条命令丢了：执行服务在它结束前重启过");
          if (read.exit === undefined) continue;
          if (stopped === "aborted") throw new Error("aborted");
          if (stopped === "timeout") throw new Error(`timeout:${options.timeout}`);
          return { exitCode: read.exit };
        }
      } finally {
        if (timer) clearTimeout(timer);
        options.signal?.removeEventListener?.("abort", onAbort);
      }
    },
  };
}

// pi hands every tool its own directory with the call and a tool prefers it to
// the one it was built with, so the call is handed the workspace instead.
function inWorkspace(spec: Manifest, tool: any) {
  return {
    ...tool,
    execute: (id: string, params: any, signal: any, onUpdate: any, ctx: any) =>
      tool.execute(id, params, signal, onUpdate, { ...ctx, cwd: spec.workspace }),
  };
}

function registerMachineTools(pi: any, spec: Manifest) {
  const operations = files(spec);
  for (const tool of [
    createReadTool(spec.workspace, { operations: operations.read }),
    createWriteTool(spec.workspace, { operations: operations.write }),
    createEditTool(spec.workspace, { operations: operations.edit }),
    createLsTool(spec.workspace, { operations: operations.ls }),
    createFindTool(spec.workspace, { operations: operations.find }),
    grepOnTheMachine(spec),
  ]) {
    pi.registerTool(inWorkspace(spec, tool));
  }
  if (spec.jobs) registerYieldingBash(pi, spec);
  else pi.registerTool(inWorkspace(spec, createBashTool(spec.workspace, { operations: shell(spec) })));
}

// --- commands that outlive the turn ------------------------------------------
//
// pi's bash tool runs a command to completion and hands back its output, and pi
// says plainly it has no background shell. A room needs one: a dev server, a
// training run, a twenty-minute test suite, a debugger somebody wants to type
// into. A turn that blocks on any of those is a turn nobody in the room can
// talk to, and a turn that gives up on them is a room that cannot run its own
// project.
//
// The command runs on the room's machine with a terminal of its own (relay.py),
// and the runner copies what it prints into a directory per job on this host
// (jobs.py). This side starts, types into and stops a job through the runner,
// and reads only those files: a job outlives pi, and a reader that started
// after it did has nothing else to go on.

const READ_LIMIT = 24 * 1024;
// How long a typed line waits for its answer before the job's output is read.
const ANSWER_MS = 700;
// Switching to the alternate screen is a program announcing it is drawing a
// display rather than printing a transcript. Looked for in the raw bytes,
// before the stripping below removes the evidence.
const ALTERNATE_SCREEN = /\x1b\[\?1049h/;

function jobDir(spec: Manifest, id: string): string {
  // Names come from us, never from the model, so a job id cannot address a path.
  if (!/^[a-z0-9-]+$/.test(id)) throw new Error(`no such job: ${id}`);
  return path.join(spec.jobs, id);
}

function readFile(where: string): string {
  try {
    return fs.readFileSync(where, "utf8");
  } catch {
    return "";
  }
}

function finished(dir: string): { status: number; at: number } | null {
  const body = readFile(path.join(dir, "exit"));
  return body ? JSON.parse(body) : null;
}

function meta(dir: string): any {
  try {
    return JSON.parse(readFile(path.join(dir, "meta.json")) || "{}");
  } catch {
    return {};
  }
}

// What the runner wrote when it could not start a job or stopped following it.
// Empty for every job that ran, which is why it can be reported wherever it is
// not empty: a job that printed nothing and a job that never started are the
// same silence until this file is read.
function failure(dir: string): string {
  return readFile(path.join(dir, "error")).trim();
}

// `exit` is written by the runner that copies the job, so it is missing both
// while a job runs and after that runner is gone — and a reader with only that
// file to go on calls the second one `running`, forever. The pid settles it.
function alive(pid: unknown): boolean {
  if (typeof pid !== "number") return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function describe(dir: string, id: string): string {
  const info = meta(dir);
  const over = finished(dir);
  const state = over ? `exited ${over.status}` : alive(info.pid) ? "running" : "lost";
  return `${id}  ${state}  ${info.label || info.command || ""}`;
}

// Everything is asked for a dumb terminal, so most of this never arrives — but
// a program that emits colour or cursor motion regardless would otherwise spend
// the room's context on instructions to a screen nobody is looking at. What is
// kept is the text: a terminal's `\r\n` corrected to the newline a reader
// expects, and a line rewritten in place by a carriage return — a progress bar —
// left as the line it ended up being.
//
// Spelled `\x1b` rather than written out: a source file holding raw control
// bytes is one that greps as binary and diffs as noise.
const OSC = /\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g;
const CSI = /\x1b\[[0-9;?]*[ -/]*[@-~]/g;
const ESCAPE = /\x1b[@-Z\\-_]/g;
const CONTROL = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g;

function readable(raw: Buffer): string {
  return raw
    .toString("utf8")
    .replace(/\r\n/g, "\n")
    .replace(OSC, "")
    .replace(CSI, "")
    .replace(ESCAPE, "")
    .replace(/[^\n]*\r(?!\n)/g, "")
    .replace(CONTROL, "");
}

function drain(
  dir: string,
  from?: number,
): { body: string; at: number; drawing: boolean } {
  const output = path.join(dir, "output");
  let size = 0;
  try {
    size = fs.statSync(output).size;
  } catch {
    return { body: "", at: 0, drawing: false };
  }
  const cursorFile = path.join(dir, "cursor");
  // The cursor is on disk, not in this process: a job outlives the session that
  // started it, and a restarted pi that re-read everything from zero would
  // hand the room a megabyte it has already seen.
  const start = from ?? Number(readFile(cursorFile) || 0);
  const begin = Math.max(0, Math.min(start, size));
  const skipped = Math.max(0, size - begin - READ_LIMIT);
  const handle = fs.openSync(output, "r");
  try {
    const length = Math.min(READ_LIMIT, size - begin - skipped);
    const buffer = Buffer.alloc(Math.max(0, length));
    if (buffer.length) fs.readSync(handle, buffer, 0, buffer.length, begin + skipped);
    fs.writeFileSync(cursorFile, String(size));
    const prefix = skipped ? `[${skipped} bytes skipped]\n` : "";
    return {
      body: prefix + readable(buffer),
      at: size,
      drawing: ALTERNATE_SCREEN.test(buffer.toString("latin1")),
    };
  } finally {
    fs.closeSync(handle);
  }
}

function registerBackgroundTools(pi: any, spec: Manifest) {
  pi.registerTool({
    name: "bash_start",
    description:
      "Run a shell command in the background, on its own terminal, and return " +
      "immediately with a job id. The command keeps running between turns and " +
      "survives a restart of this session; use it for servers, builds, long " +
      "test runs, and line-oriented interactive programs (python, psql, gdb) " +
      "that bash_write then types into. Read its output with bash_read. " +
      "Full-screen terminal programs (vim, top, tmux) cannot be driven this " +
      "way — run their non-interactive equivalent instead.",
    parameters: {
      type: "object",
      properties: {
        command: { type: "string", description: "Shell command to run" },
        label: { type: "string", description: "Short name for this job" },
      },
      required: ["command"],
    },
    async execute(_id: string, params: any) {
      let id: string;
      try {
        ({ id } = await ask(spec.socket, "job_start", {
          command: params.command,
          label: params.label ?? "",
          cwd: spec.workspace,
        }));
      } catch (cause: any) {
        return { ...text(`没能起来：${cause?.message ?? cause}`), isError: true };
      }
      // The runner waited long enough for a command that fails at once to say
      // so; a command that is merely slow to print its banner is running.
      const dir = jobDir(spec, id);
      const over = finished(dir);
      const first = drain(dir).body;
      return text(
        `started ${id}\n` +
          (over ? `已经结束，退出码 ${over.status}\n` : "") +
          (first ? `\n${first}` : "(还没有输出)"),
      );
    },
  });

  pi.registerTool({
    name: "bash_read",
    description:
      "Read what a background job has printed since the last read. Returns " +
      "nothing when it has printed nothing new, which for a healthy server is " +
      "the normal answer.",
    parameters: {
      type: "object",
      properties: {
        id: { type: "string", description: "Job id from bash_start" },
        from: {
          type: "integer",
          description: "Byte offset to read from; omit to continue where the last read stopped",
        },
      },
      required: ["id"],
    },
    async execute(_call: string, params: any) {
      const dir = jobDir(spec, params.id);
      const { body, drawing } = drain(dir, params.from);
      const over = finished(dir);
      const why = failure(dir);
      if (why) {
        return { ...text(`${params.id} 出错了：\n${why}`), isError: true };
      }
      const screen = drawing
        ? "\n\n[这个程序切到了全屏界面，之后它输出的是画面控制指令，读不出内容。" +
          "用 bash_kill 结束它，改用非交互的等价命令。]"
        : "";
      if (!body) {
        return text(over ? `${params.id} 已结束，退出码 ${over.status}` : "(没有新输出)");
      }
      return text(
        body + screen + (over ? `\n\n[已结束，退出码 ${over.status}]` : ""),
      );
    },
  });

  pi.registerTool({
    name: "bash_write",
    description:
      "Type into a running background job, as at its terminal. A newline is " +
      "added unless you pass newline=false. Read the answer with bash_read.",
    parameters: {
      type: "object",
      properties: {
        id: { type: "string", description: "Job id from bash_start" },
        text: { type: "string", description: "What to type" },
        newline: { type: "boolean", description: "Append a newline (default true)" },
      },
      required: ["id", "text"],
    },
    async execute(_call: string, params: any) {
      if (adopted.has(params.id)) {
        return {
          ...text(`${params.id} 是从前台转到后台的命令，没有终端可以输入。`),
          isError: true,
        };
      }
      const dir = jobDir(spec, params.id);
      const over = finished(dir);
      if (over) throw new Error(`${params.id} 已结束，退出码 ${over.status}`);
      const body = params.newline === false ? params.text : `${params.text}\n`;
      await ask(spec.socket, "job_write", { id: params.id, text: body });
      await new Promise((done) => setTimeout(done, ANSWER_MS));
      const { body: answer } = drain(dir);
      return text(answer || "(没有新输出)");
    },
  });

  pi.registerTool({
    name: "bash_kill",
    description: "Stop a background job and everything it started.",
    parameters: {
      type: "object",
      properties: {
        id: { type: "string", description: "Job id from bash_start" },
        force: { type: "boolean", description: "SIGKILL instead of SIGTERM" },
      },
      required: ["id"],
    },
    async execute(_call: string, params: any) {
      const moved = adopted.get(params.id);
      if (moved) {
        moved.abort();
        return text(`${params.id} 已收到停止信号`);
      }
      const over = finished(jobDir(spec, params.id));
      if (over) return text(`${params.id} 已经结束，退出码 ${over.status}`);
      await ask(spec.socket, "job_signal", { id: params.id, signal: params.force ? 9 : 15 });
      return text(`${params.id} 已收到停止信号`);
    },
  });

  pi.registerTool({
    name: "bash_list",
    description: "Every background job this room has started, and whether it is still running.",
    parameters: { type: "object", properties: {} },
    async execute() {
      let names: string[];
      try {
        names = fs.readdirSync(spec.jobs).sort();
      } catch {
        names = [];
      }
      if (!names.length) return text("没有后台任务。");
      return text(names.map((id) => describe(path.join(spec.jobs, id), id)).join("\n"));
    },
  });
}

// An exit is news, and it arrives while the turn that started the job is long
// over. `followUp` waits for the agent to stop rather than cutting into its
// tool calls; with no `triggerTurn` it never starts a run of its own, so a job
// that ends at three in the morning does not wake the room up — it is simply
// there in front of whoever asks next.
function announceExits(pi: any, spec: Manifest) {
  const reported = new Set<string>();
  let timer: any = null;

  const sweep = () => {
    let names: string[];
    try {
      names = fs.readdirSync(spec.jobs);
    } catch {
      return;
    }
    for (const id of names) {
      if (reported.has(id)) continue;
      const over = finished(path.join(spec.jobs, id));
      if (!over) continue;
      reported.add(id);
      const { body } = drain(path.join(spec.jobs, id));
      pi.sendMessage(
        {
          customType: "cheese-background",
          content:
            `${spec.notice}\n后台任务 ${describe(path.join(spec.jobs, id), id)}。` +
            (body ? `\n最后的输出：\n${body}` : "") ,
          display: true,
        },
        { deliverAs: "followUp" },
      );
    }
  };

  pi.on("session_start", async () => {
    // Anything that ended while this session was not running is history, not
    // news: report it only if somebody asks with bash_list.
    try {
      for (const id of fs.readdirSync(spec.jobs)) {
        if (finished(path.join(spec.jobs, id))) reported.add(id);
      }
    } catch {
      /* no jobs yet */
    }
    // Started here rather than in the factory: pi runs the factory in
    // invocations that never open a session, and a timer left in one of those
    // is a process that will not exit.
    if (!timer) timer = setInterval(sweep, 2000);
  });

  pi.on("session_shutdown", async () => {
    if (timer) clearInterval(timer);
    timer = null;
  });
}

// --- a person waiting for an answer ------------------------------------------
//
// The runner writes down when a person's message is waiting on an answer
// (driven/runner.py, which owns the rule: what owes one, what answers it, what
// the refusal says, when it lapses) and names the file in this variable. Until
// the session has answered, every tool but those reading the room is refused
// with the runner's words. A subagent is a pi of its own, started without this variable
// (subagents.py), so every call held here is the session's own: a subagent
// reports to the agent that started it, not to the room.

const REPLY_OWED_ENV = "CHEESE_REPLY_OWED";

type Debt = {
  id: string;
  answers: string[];
  reads: string[];
  reason: string;
  answered: string;
};

// What the runner's file says is owed right now, answered or not.
function debtOnFile(): Debt | null {
  const file = process.env[REPLY_OWED_ENV] ?? "";
  if (!file) return null;
  try {
    const debt = JSON.parse(fs.readFileSync(file, "utf8"));
    return debt?.id ? debt : null;
  } catch {
    return null;
  }
}

// Kept in this process: a reply and the next call can be siblings in one
// message, and pi preflights them in order, so the reply's call is seen here
// before the runner could hear of it.
let answered: string | null = null;

// A debt on file that the session has not answered yet.
function unanswered(): Debt | null {
  const debt = debtOnFile();
  return debt && debt.id !== answered ? debt : null;
}

function holdToAnswering(pi: any) {
  pi.on("tool_call", async (event: any) => {
    const debt = unanswered();
    if (!debt) return;
    // Reading the room is on the way to answering it, and answers nothing.
    if (!debt.answers.includes(event.toolName)) {
      if (debt.reads.includes(event.toolName)) return;
      return { block: true, reason: debt.reason };
    }
    answered = debt.id;
    // And where the runner reads it, to know the turn may end (`insist`).
    fs.writeFileSync(debt.answered, debt.id);
  });
}

// --- a message while a command runs -----------------------------------------
//
// pi hands the model a message only between tool calls, and its bash waits for
// its command to end — so a person writing during a twenty-minute build waited
// twenty minutes. This is pi's bash with one thing added, the Ctrl+B of the
// other harnesses: when the runner writes down a new debt (a person has just
// written, `driven/runner.py`), the call returns what the command printed so
// far and the command goes on as a background job, read with bash_read and
// stopped with bash_kill like any other. It lives only as long as pi does, as a
// backgrounded command does in Claude Code; a job meant to outlive the session
// is still bash_start's.

const YIELD_POLL_MS = 200;
// Foreground commands moved to the background, by job id: stopping one aborts
// the call pi's own shell is still running it under.
const adopted = new Map<string, AbortController>();

function registerYieldingBash(pi: any, spec: Manifest) {
  const machine = shell(spec);
  let counter = 0;
  pi.registerTool({
    ...createBashTool(spec.workspace, { operations: machine }),
    async execute(id: string, params: any, signal: any, onUpdate: any) {
      const own = new AbortController();
      const forward = () => own.abort();
      signal?.addEventListener?.("abort", forward, { once: true });
      const printed: Buffer[] = [];
      let sink: fs.WriteStream | null = null;
      let ended: { status: number | null } | null = null;
      let onEnd: ((status: number | null) => void) | null = null;
      const operations = {
        exec: (command: string, cwd: string, options: any) =>
          machine
            .exec(command, cwd, {
              ...options,
              signal: own.signal,
              onData: (data: Buffer) => {
                options.onData(data);
                if (sink) sink.write(data);
                else printed.push(data);
              },
            })
            .then(
              (outcome: any) => {
                ended = { status: outcome.exitCode };
                onEnd?.(outcome.exitCode);
                return outcome;
              },
              (error: any) => {
                // Stopped (bash_kill, the turn aborted) or out of reach: it
                // has ended all the same, and a job reads as ended by its file.
                ended = { status: null };
                onEnd?.(null);
                throw error;
              },
            ),
      };
      const running = createBashTool(spec.workspace, { operations }).execute(
        id,
        params,
        own.signal,
        onUpdate,
      );
      // Unanswered: no tool starts while one is (`holdToAnswering`), so this
      // command was already running when the person wrote.
      let timer: any = null;
      const spoken = new Promise<null>((resolve) => {
        timer = setInterval(() => {
          if (unanswered()) resolve(null);
        }, YIELD_POLL_MS);
      });
      try {
        const done = await Promise.race([running.then((result: any) => ({ result })), spoken]);
        if (done) return done.result;
      } finally {
        clearInterval(timer);
      }
      // The model no longer waits on it; its ending is written down instead.
      signal?.removeEventListener?.("abort", forward);
      running.catch(() => {});
      const job = `job-fg-${++counter}-${Date.now().toString(36)}`;
      const dir = jobDir(spec, job);
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(
        path.join(dir, "meta.json"),
        JSON.stringify({ command: params.command, label: "", pid: process.pid }),
      );
      fs.writeFileSync(path.join(dir, "output"), Buffer.concat(printed));
      sink = fs.createWriteStream(path.join(dir, "output"), { flags: "a" });
      adopted.set(job, own);
      onEnd = (status) => {
        adopted.delete(job);
        sink?.end(() =>
          fs.writeFileSync(
            path.join(dir, "exit"),
            JSON.stringify({ status: status ?? -1, at: Date.now() / 1000 }),
          ),
        );
      };
      if (ended) onEnd((ended as { status: number | null }).status);
      const { body } = drain(dir, 0);
      return text(
        (body ? `${body}\n\n` : "") +
          `[有人在房间里发来了消息，这条命令转到后台继续跑，任务 ${job}。` +
          "先看那条消息；输出用 bash_read 读，用 bash_kill 停掉。]",
      );
    },
  });
}

// --- subagents ---------------------------------------------------------------
//
// pi has no subagents; these three tools are the platform's (subagents.py).
// `Task` starts one: a second pi beside this one, with its hands on the same
// machine and in the same checkout (the runner's), whose model
// the platform admits as it does every native subagent's, and whose every entry
// the runner writes into the session's log under the thread label its prompt
// carries. `SendMessage` says more to one still running, `TaskStop` stops one
// and leaves its siblings running. They are named and shaped as Claude Code's
// are, so the room's instructions for delegating read the same on both.
//
// A `Task` in the foreground waits for its subagent's conclusion and returns
// it. When a person writes meanwhile, the call stops waiting, as the shell does
// (registerYieldingBash): the subagent goes on in the background, and the
// runner tells the session when it ends.

const SUBAGENT_HANDS = (id: string) =>
  `补充要求用 SendMessage(to="${id}")，停掉它用 TaskStop(task_id="${id}")。`;

function registerSubagentTools(pi: any, spec: Manifest) {
  pi.registerTool({
    name: "Task",
    description:
      "Start a subagent: a separate agent with its own context, working in this " +
      "checkout, that returns its conclusion when it finishes. Give it a " +
      "self-contained prompt, including any thread label the work was given. " +
      "`model` picks the model it runs (the project's subagent default when " +
      "omitted). With run_in_background it runs while you go on, and you are " +
      "told when it ends; otherwise this call waits for its conclusion. " +
      "SendMessage gives a running subagent more instructions; TaskStop stops it.",
    parameters: {
      type: "object",
      properties: {
        description: { type: "string", description: "A short (3-5 word) description of the task" },
        prompt: { type: "string", description: "The task for the subagent to perform" },
        model: {
          type: "string",
          description: "The model it runs; omit for the project's subagent default",
        },
        run_in_background: { type: "boolean", description: "Run it in the background" },
        subagent_type: {
          type: "string",
          enum: ["general-purpose"],
          description: "The kind of subagent; general-purpose is the only one here",
        },
      },
      required: ["description", "prompt"],
    },
    async execute(_id: string, params: any, signal: any) {
      const background = params.run_in_background === true;
      const started = await ask(spec.socket, "subagent_spawn", {
        prompt: params.prompt,
        description: params.description ?? "",
        model: params.model ?? null,
        background,
      });
      const id: string = started.agent_id;
      if (background) {
        return {
          ...text(
            `已在后台起了分身 ${id}（模型 ${started.model}）。它结束时平台会告诉你；` +
              SUBAGENT_HANDS(id),
          ),
          details: { agent_id: id, model: started.model },
        };
      }
      let timer: any = null;
      const spoken = new Promise<null>((resolve) => {
        timer = setInterval(() => {
          if (unanswered()) resolve(null);
        }, YIELD_POLL_MS);
      });
      const aborted = new Promise<null>((resolve) => {
        if (signal?.aborted) resolve(null);
        signal?.addEventListener?.("abort", () => resolve(null), { once: true });
      });
      let ended: any = null;
      try {
        ended = await Promise.race([
          ask(spec.socket, "subagent_wait", { agent_id: id }),
          spoken,
          aborted,
        ]);
      } finally {
        clearInterval(timer);
      }
      if (ended) {
        return {
          content: [
            { type: "text" as const, text: ended.text || "（分身没有留话）" },
            { type: "text" as const, text: `agentId: ${id}（${ended.status}）` },
          ],
          details: {
            agent_id: id,
            model: started.model,
            status: ended.status,
            report: ended.text,
            description: params.description ?? "",
          },
        };
      }
      if (signal?.aborted) {
        await ask(spec.socket, "subagent_stop", { agent_id: id }).catch(() => {});
        throw new Error(`${id} 随这一轮一起停了`);
      }
      await ask(spec.socket, "subagent_background", { agent_id: id });
      return {
        ...text(
          `[有人在房间里发来了消息，分身 ${id} 转到后台继续做，结束时平台会告诉你。` +
            `先看那条消息；${SUBAGENT_HANDS(id)}]`,
        ),
        details: { agent_id: id, model: started.model },
      };
    },
  });

  pi.registerTool({
    name: "SendMessage",
    description:
      "Send a message to a subagent that is still running: more instructions, " +
      "or a change of what it should do. It reads it before its next step.",
    parameters: {
      type: "object",
      properties: {
        to: { type: "string", description: "The subagent's id, as Task returned it" },
        message: { type: "string", description: "What to tell it" },
      },
      required: ["to", "message"],
    },
    async execute(_id: string, params: any) {
      await ask(spec.socket, "subagent_send", { agent_id: params.to, message: params.message });
      return text(`已发给 ${params.to}`);
    },
  });

  pi.registerTool({
    name: "TaskStop",
    description: "Stop one subagent. Any others keep running.",
    parameters: {
      type: "object",
      properties: {
        task_id: { type: "string", description: "The subagent's id, as Task returned it" },
      },
      required: ["task_id"],
    },
    async execute(_id: string, params: any) {
      const answer = await ask(spec.socket, "subagent_stop", { agent_id: params.task_id });
      return text(answer.stopped ? `${params.task_id} 已停下` : `${params.task_id} 已经结束了`);
    },
  });
}

// --- a session with no hands ----------------------------------------------------
//
// A person's 芝士 has no project and no machine: its conversation and the tools
// listed are all it has. pi is started with only those tools enabled, so its own
// read, bash, edit and write are not there to be left in place, and nothing here
// reaches for a machine, a repository, hooks, subagents or background jobs.
// pi still names the directory it runs in, which on the session host is nothing
// of the person's; that section goes.

function withoutHands(pi: any, spec: Manifest) {
  pi.on("before_agent_start", async (event: any) => {
    const options = promptOptions(event);
    if (!options) return;
    // Emptied rather than dropped: which sections exist is pi's to decide
    // (`buildSystemPromptSections` writes `cwd` for every session), and what
    // this session has to say about its directory is that it has none.
    // One that reads the room's machine is in the room's checkout.
    options.cwd = spec.reading ? spec.workspace : "";
  });
  registerPlatformTools(pi, spec);
  if (spec.reading) registerReadingTools(pi, spec);
}

// The tools of pi's that only read, on the room's machine, with the project's
// own checks around them as around the room agent's (a `permissions.deny` on a
// file keeps it from this session too).
function registerReadingTools(pi: any, spec: Manifest) {
  const operations = files(spec);
  for (const tool of [
    createReadTool(spec.workspace, { operations: operations.read }),
    createLsTool(spec.workspace, { operations: operations.ls }),
    createFindTool(spec.workspace, { operations: operations.find }),
    grepOnTheMachine(spec),
    gitOnTheMachine(spec),
  ]) {
    pi.registerTool(inWorkspace(spec, tool));
  }
  applyProjectHooks(pi, spec);
}

// The checkout's history: git's reading commands, run by the machine's
// executor without taking git's locks (`machine_git.py`). The session names one
// and fills in revisions and a path; it never writes a command line.
function gitOnTheMachine(spec: Manifest) {
  return {
    name: "git",
    label: "git",
    description:
      "读房间工作目录的 git 记录（只读，不会改动仓库）。command 选一种：" +
      "status（工作区状态）；log（提交记录，可给 revision、path、limit）；" +
      "diff（工作目录相对 base 的改动，包括还没提交的；base 默认 HEAD，" +
      "since_branched 为 true 时从 base 分出来的地方算起，看这个分支相对主干改了什么）；" +
      "show（某次提交，revision 默认 HEAD）；blame（某个文件每行最后是哪次提交改的，要给 path）。" +
      "stat 为 true 时只列改了哪些文件和行数。",
    parameters: {
      type: "object",
      properties: {
        command: { type: "string", enum: ["status", "log", "diff", "show", "blame"] },
        revision: { type: "string", description: "分支名、标签或提交号" },
        base: { type: "string", description: "diff 的比较对象，如 main、origin/main" },
        since_branched: { type: "boolean" },
        path: { type: "string", description: "只看这个文件或目录" },
        limit: { type: "number", description: "log 最多列几条，默认 20" },
        stat: { type: "boolean" },
      },
      required: ["command"],
    },
    async execute(_id: string, params: any) {
      const answer = await ask(spec.socket, "git", params ?? {});
      const body = answer.output?.trim() || "（没有输出）";
      return text(answer.truncated ? `${body}\n\n（输出太长，后面的截掉了；用 path 或 stat 缩小范围）` : body);
    },
  };
}

export default function (pi: any) {
  const spec = manifest();
  if (spec.hands === false) return withoutHands(pi, spec);
  placeTheSession(pi, spec);
  holdToAnswering(pi);
  if (spec.tools.length) registerPlatformTools(pi, spec);
  else if (spec.unavailable) {
    // Said where a launch failure is read, not swallowed: a room whose platform
    // tools are all missing looks from the inside exactly like a room that was
    // never given any, and the agent will conclude it must shell out.
    process.stderr.write(`[cheese] no platform tools: ${spec.unavailable}\n`);
  }
  registerMcpTools(pi, spec);
  // Always, runner or no runner: pi's own tools left in place would work on the
  // session host, which holds nothing of the project and everything of every
  // other room's. Without a runner to reach they fail, which is the truth.
  registerMachineTools(pi, spec);
  if (spec.socket) applyProjectHooks(pi, spec);
  if (spec.socket && spec.subagents) registerSubagentTools(pi, spec);
  if (spec.jobs) {
    registerBackgroundTools(pi, spec);
    announceExits(pi, spec);
  }
  // No reminder to publish lives here. A room that has heard nothing for a while
  // is reminded by the platform (ChatService.remind_silent_turns), which steers
  // the same notice into a running turn on every harness.
}
