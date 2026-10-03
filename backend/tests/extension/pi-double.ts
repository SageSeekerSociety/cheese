// pi, reduced to the one interface it hands an extension.
//
// Shared by both test files in this directory so the seam has ONE double. Two
// hand-written stand-ins for the same interface drift, and the one that drifted
// goes on passing — which is the whole reason the wire frames and the harness
// contract are fixtures rather than literals typed out twice.
//
// Nothing here reaches inside the extension: everything is driven through the
// default export, which is all pi ever calls.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as net from "node:net";
import * as os from "node:os";
import * as path from "node:path";
import { register } from "node:module";

// The extension builds its bash on pi's own (`createBashTool`), imported from
// pi's package, which only pi can resolve. Here that name is the stand-in beside
// this file; everything else resolves as it always does.
const PI_PACKAGE = new URL("./pi-coding-agent.ts", import.meta.url).href;
register(
  "data:text/javascript," +
    encodeURIComponent(
      "export async function resolve(specifier, context, next) {" +
        `  if (specifier === "@earendil-works/pi-coding-agent")` +
        `    return { url: ${JSON.stringify(PI_PACKAGE)}, shortCircuit: true };` +
        "  return next(specifier, context);" +
        "}",
    ),
);

const SOURCE = new URL(
  "../../app/domain/agent/harness/pi/platform.ts",
  import.meta.url,
).href;

export const NOTICE = "【平台】以下是平台自动发出的指令，不是任何人手打的话：";

type Handler = (event: any, ctx: any) => any;

/** What pi hands an extension, reduced to what this one actually uses. */
export class FakePi {
  tools = new Map<string, any>();
  handlers = new Map<string, Handler[]>();
  sent: { message: any; options: any }[] = [];

  registerTool(definition: any) {
    this.tools.set(definition.name, definition);
  }

  on(event: string, handler: Handler) {
    const existing = this.handlers.get(event) ?? [];
    existing.push(handler);
    this.handlers.set(event, existing);
  }

  sendMessage(message: any, options: any) {
    this.sent.push({ message, options });
  }

  async emit(event: string, payload: any = {}, ctx: any = {}) {
    let answer: any;
    for (const handler of this.handlers.get(event) ?? []) {
      answer = (await handler(payload, ctx)) ?? answer;
    }
    return answer;
  }

  /** One tool call as pi 1.0.0 makes it (`agent-loop.ts`, `agent-session.ts`):
   *  `tool_call` first, where a `block` or a throw stops the call and its
   *  reason becomes the error the model reads, and a mutated input is what
   *  runs; then the tool; then `tool_result`, whose patches replace fields of
   *  the result. `tool` stands in for the tool's own `execute`. */
  async run(
    name: string,
    input: any,
    tool: (input: any) => any,
    ctx: any = {},
    id = "call-1",
  ) {
    let verdict: any;
    try {
      for (const handler of this.handlers.get("tool_call") ?? []) {
        verdict = await handler({ type: "tool_call", toolName: name, toolCallId: id, input }, ctx);
        if (verdict?.block) break;
      }
    } catch (error: any) {
      return { content: [{ type: "text", text: error.message }], isError: true };
    }
    if (verdict?.block) {
      return {
        content: [{ type: "text", text: verdict.reason || "Tool execution was blocked" }],
        isError: true,
      };
    }
    let result: any;
    let isError = false;
    try {
      result = await tool(input);
    } catch (error: any) {
      result = { content: [{ type: "text", text: error.message }] };
      isError = true;
    }
    const event = { type: "tool_result", toolName: name, toolCallId: id, input, ...result, isError };
    for (const handler of this.handlers.get("tool_result") ?? []) {
      Object.assign(event, (await handler({ ...event }, ctx)) ?? {});
    }
    return { content: event.content, isError: event.isError };
  }

  async call(name: string, params: any, ctx: any = {}) {
    const tool = this.tools.get(name);
    assert.ok(tool, `${name} was never registered`);
    return tool.execute("call-1", params, undefined, undefined, ctx);
  }
}

/** The `before_agent_start` event as pi 1.0.0 raises it
 *  (`core/extensions/runner.ts`): the prompt is rendered from the mutable
 *  `systemPromptOptions` the handler is given, and `systemPrompt` is a getter
 *  that re-renders it.
 *
 * The getter throws rather than answering, and that is the point of this
 * function. Against 0.85.1 the extension rewrote that rendered text, matching
 * on wording pi owns; 1.0 changed the wording and the rewrite became a no-op
 * that said nothing. A handler that goes back to reading the rendered prompt
 * fails here, where it is visible, instead of in a room where it is not.
 * (`buildSystemPromptOptions` in pi fills in the same defaults; a handler sees
 * the normalized shape, never holes.) */
export function beforeAgentStart(cwd: string, options: any = {}) {
  const systemPromptOptions = {
    selectedTools: [],
    toolSnippets: {},
    toolGuidelines: {},
    promptGuidelines: [],
    appendSystemPrompt: "",
    sections: {},
    contextFiles: [],
    skills: [],
    ...options,
    cwd,
  };
  return {
    type: "before_agent_start",
    prompt: "",
    systemPromptOptions,
    get systemPrompt(): never {
      throw new Error(
        "the extension read the rendered system prompt; pi owns its wording, " +
          "so work through systemPromptOptions",
      );
    },
  };
}

const rubbish: string[] = [];

// What a test leaves running when it fails before its own `close()`: a command
// `machine()` started, and the socket listening for the extension. Either one
// keeps this process alive, so a failed assertion would become a run that never
// exits until CI's job timeout ends it. `cleanup` stops both, commands first,
// since a running command also holds its output pipes open.
const stillRunning = new Set<number>();
const servers: net.Server[] = [];

export function scratch(): string {
  const made = fs.mkdtempSync(path.join(os.tmpdir(), "pi-ext-"));
  rubbish.push(made);
  return made;
}

/** Hand this to `after()` in every file that calls `scratch`, `load`, `runner`
 *  or `machine`. */
export function cleanup() {
  for (const group of stillRunning) {
    try {
      process.kill(-group, "SIGKILL");
    } catch {
      /* gone */
    }
  }
  stillRunning.clear();
  for (const server of servers) server.close();
  servers.length = 0;
  for (const directory of rubbish) {
    fs.rmSync(directory, { recursive: true, force: true });
  }
  rubbish.length = 0;
}

export const CATALOG = [
  {
    name: "chat_send",
    description: "发布消息",
    inputSchema: {
      type: "object",
      properties: { content: { type: "string" }, reply_to: { type: "string" } },
      required: ["content"],
    },
  },
  {
    name: "cheese_doc_get",
    description: "读实况文档",
    inputSchema: { type: "object", properties: { section: { type: "string" } } },
  },
];

/** A runner socket that records what it was asked and answers as told. */
export async function runner(answer: (request: any) => any) {
  const address = path.join(scratch(), "s.sock");
  const asked: any[] = [];
  const server = net.createServer((connection) => {
    let received = "";
    connection.on("data", (chunk) => {
      received += chunk;
      if (!received.includes("\n")) return;
      const request = JSON.parse(received);
      asked.push(request);
      connection.end(JSON.stringify(answer(request)) + "\n");
    });
  });
  await new Promise<void>((done) => server.listen(address, done));
  servers.push(server);
  return { address, asked, close: () => server.close() };
}

/** A runner whose `shell` is a machine: each command really runs (here, under
 * /bin/sh), its output read back from an offset the way the runner hands it
 * on. Anything else is answered by `other`. */
export async function machine(other: (request: any) => any = () => ({ result: {} })) {
  const { spawn } = await import("node:child_process");
  const commands = new Map<string, { out: Buffer; exit?: number; pid: number }>();
  let next = 0;
  return runner((request) => {
    if (request.method !== "shell") return other(request);
    const params = request.params;
    if (params.operation === "start") {
      const id = `c${++next}`;
      const child = spawn("/bin/sh", ["-c", params.command], {
        cwd: params.cwd,
        detached: true,
        stdio: ["ignore", "pipe", "pipe"],
      });
      const record: { out: Buffer; exit?: number; pid: number } = {
        out: Buffer.alloc(0),
        pid: child.pid as number,
      };
      const take = (data: Buffer) => {
        record.out = Buffer.concat([record.out, data]);
      };
      child.stdout.on("data", take);
      child.stderr.on("data", take);
      stillRunning.add(record.pid);
      child.on("close", (code, signal) => {
        stillRunning.delete(record.pid);
        record.exit = code ?? -(signal === "SIGKILL" ? 9 : 15);
      });
      commands.set(id, record);
      return { result: { id } };
    }
    const record = commands.get(params.id);
    if (!record) return { error: "unknown command" };
    if (params.operation === "signal") {
      try {
        process.kill(-record.pid, params.signal);
      } catch {
        /* gone */
      }
      return { result: { running: record.exit === undefined } };
    }
    const data = record.out.subarray(params.offset);
    return {
      result: {
        data: data.toString("base64"),
        offset: record.out.length,
        ...(record.exit === undefined ? {} : { exit: record.exit }),
      },
    };
  });
}

let loaded = 0;

/** Load the extension against a manifest, the way the runner writes one. */
export async function load(manifest: Partial<Record<string, unknown>> = {}) {
  const home = scratch();
  const jobs = manifest.jobs ?? path.join(home, "bg");
  fs.mkdirSync(jobs as string, { recursive: true });
  fs.writeFileSync(
    path.join(home, "platform.json"),
    JSON.stringify({
      socket: "",
      state: home,
      workspace: home,
      jobs,
      tools: CATALOG,
      unavailable: "",
      mcp: [],
      notice: NOTICE,
      ...manifest,
    }),
  );
  process.env.CHEESE_PI_EXTENSION = home;
  // The module reads its environment once, at load. A query string is what
  // makes a second load a second module rather than the cached first one.
  const module = await import(`${SOURCE}?load=${++loaded}`);
  const pi = new FakePi();
  module.default(pi);
  return { pi, home, jobs: jobs as string };
}
