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
  tools: ToolSpec[];
  unavailable: string;
  notice: string;
};

// What a room is published with. Everything else a turn produces stays on the
// machine, so a turn that never calls this said nothing to anybody.
const PUBLISH = "cheese_chat_send";

const HOME = process.env.CHEESE_PI_EXTENSION ?? "";

function manifest(): Manifest {
  const empty: Manifest = {
    socket: "",
    state: "",
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
  for (const tool of spec.tools) {
    pi.registerTool({
      name: tool.name,
      description: tool.description,
      parameters: tool.inputSchema,
      async execute(_id: string, params: any, _signal: any, _update: any, ctx: any) {
        const result = await ask(spec.socket, "cli", {
          tool: tool.name,
          arguments: params ?? {},
          cwd: ctx?.cwd,
        });
        const body = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
        if (result.status !== 0) {
          return {
            ...text(body || `${tool.name} exited with status ${result.status}`),
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
      // it and the calls after it start the count again.
      if (result.toolName === PUBLISH && !result.isError) since = 0;
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
  // Nothing to ask for if the room has no way to publish: a reminder naming a
  // tool that is not registered is worse than silence.
  if (spec.tools.some((tool) => tool.name === PUBLISH) && spec.notice) {
    watchForSilence(pi, spec);
  }
  else if (spec.unavailable) {
    // Said where a launch failure is read, not swallowed: a room whose platform
    // tools are all missing looks from the inside exactly like a room that was
    // never given any, and the agent will conclude it must shell out.
    process.stderr.write(`[cheese] no platform tools: ${spec.unavailable}\n`);
  }
}
