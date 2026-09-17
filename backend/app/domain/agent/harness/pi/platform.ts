// 平台给 pi 的那一份 extension。
//
// pi is somebody else's agent, and every one of these is written for a person
// sitting at a terminal talking to it alone. A Cheese room is neither: the
// output of a turn is not published anywhere, the people in it are several,
// and the work outlives the turn that started it. What this file adds is the
// part of that pi has no notion of — and nothing else. pi's loop, its tools,
// its context handling are its own.
//
// Written by the runner into the session's state directory and named with
// `--extension`, the same way the room's skills and system prompt are: they
// are assembled per room, and the machine has no copy to point at.

import { spawn } from "node:child_process";
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
  python: string;
  background: string;
  jobs: string;
  tools: ToolSpec[];
  unavailable: string;
  notice: string;
};

// What a room is published with. Everything else a turn produces stays on the
// machine, so a turn that never calls this said nothing to anybody.
//
// Two names for it, and not by choice here: the room's system prompt says
// `chat_send` on every turn, while the CLI catalog can only ever call the
// command what it is — `cheese chat send`. The other harness resolves this by
// serving both, so a pi room that served only one would be the same
// instruction failing for one teammate and working for the other. The prompt's
// name is the one anything here asks for.
const PUBLISH = "chat_send";
const PUBLISH_COMMAND = "cheese_chat_send";

const HOME = process.env.CHEESE_PI_EXTENSION ?? "";

function manifest(): Manifest {
  const empty: Manifest = {
    socket: "",
    state: "",
    python: "",
    background: "",
    jobs: "",
    tools: [],
    unavailable: "the runner wrote no manifest",
    notice: "",
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
// pi has no MCP, so without these the agent's only way to reach the platform is
// to type the CLI into a shell — which is how it works today, and why the room's
// skill tells it to call tools that are not there. The catalog comes from the
// CLI installed on this machine (the runner asks its argparse tree), so a tool
// exists here exactly when the command exists there.

function registerPlatformTools(pi: any, spec: Manifest) {
  const named = spec.tools.flatMap((tool) =>
    tool.name === PUBLISH_COMMAND
      ? [
          { ...tool, command: tool.name },
          { ...tool, command: tool.name, name: PUBLISH },
        ]
      : [{ ...tool, command: tool.name }],
  );
  for (const tool of named) {
    pi.registerTool({
      name: tool.name,
      description: tool.description,
      parameters: tool.inputSchema,
      async execute(_id: string, params: any, _signal: any, _update: any, ctx: any) {
        const result = await ask(spec.socket, "cli", {
          // The CLI only knows the command's own name; `chat_send` is the name
          // the room's prompt uses for it.
          tool: tool.command,
          arguments: params ?? {},
          cwd: ctx?.cwd,
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

// --- what the repository says about itself -----------------------------------
//
// pi discovers AGENTS.md and CLAUDE.md by walking from the working directory up
// to `/`, which on a machine somebody lent us goes through their home. So the
// discovery is off (`--no-context-files`) and this reads the one directory a
// room is entitled to: the checkout it was given.
//
// It has to be read, not skipped. A repository that Cheese hosts must never
// have to change in order to be hosted, and its CLAUDE.md is how it says what
// it needs — the other harness reads one, and a pi room that did not would be
// the same repository being told different things by two teammates.

const CONTEXT_FILES = ["AGENTS.md", "CLAUDE.md"];

// Big enough for any of these written to be read by a person, small enough that
// a generated file checked in under one of these names cannot displace the room.
const CONTEXT_LIMIT = 64 * 1024;

function repositoryContext(cwd: string): string {
  const parts: string[] = [];
  const seen = new Set<string>();
  for (const name of CONTEXT_FILES) {
    let body: string;
    try {
      body = fs.readFileSync(path.join(cwd, name), "utf8");
    } catch {
      continue;
    }
    // The two names are often one file: a repository that keeps CLAUDE.md and
    // symlinks AGENTS.md at it should not have it read into the turn twice.
    if (!body.trim() || seen.has(body)) continue;
    seen.add(body);
    const kept =
      body.length > CONTEXT_LIMIT
        ? body.slice(0, CONTEXT_LIMIT) + `\n\n[truncated at ${CONTEXT_LIMIT} bytes]`
        : body;
    parts.push(`## ${name}\n\n${kept}`);
  }
  if (!parts.length) return "";
  return `# 这个仓库自己的说明（${cwd}）\n\n${parts.join("\n\n")}`;
}

function carryRepositoryContext(pi: any) {
  pi.on("before_agent_start", async (event: any, ctx: any) => {
    const context = repositoryContext(ctx.cwd);
    if (!context) return;
    // Appended, never prepended: everything before it is the same on every turn
    // of the session and is what a provider cache matches on. Read fresh each
    // turn, so an edit to the file is in effect on the next one — and a turn
    // where nothing changed produces the identical string.
    return { systemPrompt: `${event.systemPrompt}\n\n${context}` };
  });
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
// What holds the command is a separate process with its own session (see
// background.py). This side only ever names paths: whoever holds the pty master
// decides whether the job survives pi, and it must.

const SETTLE_MS = 700; // long enough for a command that fails at once to say so
const READ_LIMIT = 24 * 1024;
// Switching to the alternate screen is a program announcing it is drawing a
// display rather than printing a transcript. Looked for in the raw bytes,
// before the stripping below removes the evidence.
const ALTERNATE_SCREEN = /\x1b\[\?1049h/;

type Job = { id: string; dir: string };

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

function describe(dir: string, id: string): string {
  const meta = JSON.parse(readFile(path.join(dir, "meta.json")) || "{}");
  const over = finished(dir);
  const state = over ? `exited ${over.status}` : "running";
  return `${id}  ${state}  ${meta.label || meta.command || ""}`;
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

function tell(spec: Manifest, id: string, request: unknown): Promise<any> {
  return new Promise((resolve, reject) => {
    const connection = net.connect(path.join(jobDir(spec, id), "sock"));
    let received = "";
    connection.setEncoding("utf8");
    connection.on("error", () =>
      reject(new Error(`${id} is not running any more`)),
    );
    connection.on("data", (chunk: string) => {
      received += chunk;
    });
    connection.on("close", () => {
      const answer = received ? JSON.parse(received) : { ok: false };
      if (!answer.ok) return reject(new Error(answer.error || "the job refused"));
      resolve(answer);
    });
    connection.write(JSON.stringify(request) + "\n");
  });
}

function registerBackgroundTools(pi: any, spec: Manifest) {
  const started = new Map<string, Job>();
  let counter = 0;

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
    async execute(_id: string, params: any, _signal: any, _update: any, ctx: any) {
      const id = `job-${++counter}-${Date.now().toString(36)}`;
      const dir = jobDir(spec, id);
      const child = spawn(
        spec.python,
        [
          spec.background,
          "--dir", dir,
          "--cwd", ctx?.cwd ?? spec.state,
          "--label", params.label ?? "",
          "--command", params.command,
        ],
        { detached: true, stdio: "ignore" },
      );
      child.unref();
      await new Promise((done) => setTimeout(done, SETTLE_MS));
      started.set(id, { id, dir });
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
      const body = params.newline === false ? params.text : `${params.text}\n`;
      await tell(spec, params.id, { write: body });
      await new Promise((done) => setTimeout(done, SETTLE_MS));
      const { body: answer } = drain(jobDir(spec, params.id));
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
      await tell(spec, params.id, { signal: params.force ? 9 : 15 });
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

  return started;
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

// --- going quiet ------------------------------------------------------------
//
// pi was built for one person watching a terminal, where working IS the
// visible output. A room sees none of it: tool calls are not published, so an
// agent that works for forty calls without publishing has, from the room's
// side, done nothing and said nothing. Nobody can tell that from stuck.
//
// The reminder rides on `context` rather than on a message, because it must
// not accumulate: `context` is a per-call copy pi does not persist, so a
// session that went quiet ten times does not end up carrying ten notices in
// its history forever.

// Tool calls, not turns: a single turn can hold twenty of them. Ten is a guess
// that has to be some number — few enough that a room is not left wondering,
// many enough that ordinary work (read a file, run the tests, read the failure)
// is never interrupted to announce itself.
const QUIET_LIMIT = 10;

function watchForSilence(pi: any, spec: Manifest) {
  let since = 0;
  pi.on("turn_end", async (event: any) => {
    for (const result of event.toolResults ?? []) {
      // In order, so a publish halfway through a turn clears what came before
      // it and the calls after it start the count again. Either name counts:
      // they are one command, and which one the model reached for says nothing
      // about whether the room heard it.
      const published =
        result.toolName === PUBLISH || result.toolName === PUBLISH_COMMAND;
      if (published && !result.isError) since = 0;
      else since += 1;
    }
  });
  pi.on("context", async (event: any) => {
    if (since < QUIET_LIMIT) return;
    const body =
      `你已经连续调用了 ${since} 次工具，其间没有向房间发过消息。` +
      `房间里的人看不到工具调用，只能看到你用 ${PUBLISH} 发出的内容，` +
      `所以他们现在无从判断你在做什么、是否还在进行。` +
      `请先用 ${PUBLISH} 说明当前进展和接下来要做的事，然后继续。`;
    return {
      messages: [
        ...event.messages,
        {
          role: "user",
          content: [{ type: "text", text: `${spec.notice}\n${body}` }],
        },
      ],
    };
  });
}

export default function (pi: any) {
  const spec = manifest();
  carryRepositoryContext(pi);
  if (spec.tools.length) registerPlatformTools(pi, spec);
  if (spec.background && spec.python) {
    registerBackgroundTools(pi, spec);
    announceExits(pi, spec);
  }
  // Nothing to ask for if the room has no way to publish: a reminder naming a
  // tool that is not registered is worse than silence.
  // Asked of the catalog, which carries the command's own name — `chat_send`
  // is a name this file adds and would answer for itself.
  if (spec.tools.some((tool) => tool.name === PUBLISH_COMMAND) && spec.notice) {
    watchForSilence(pi, spec);
  }
  else if (spec.unavailable) {
    // Said where a launch failure is read, not swallowed: a room whose platform
    // tools are all missing looks from the inside exactly like a room that was
    // never given any, and the agent will conclude it must shell out.
    process.stderr.write(`[cheese] no platform tools: ${spec.unavailable}\n`);
  }
}
