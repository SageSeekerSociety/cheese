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

export const SOURCE = new URL(
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

  async call(name: string, params: any, ctx: any = {}) {
    const tool = this.tools.get(name);
    assert.ok(tool, `${name} was never registered`);
    return tool.execute("call-1", params, undefined, undefined, ctx);
  }
}

const rubbish: string[] = [];

export function scratch(): string {
  const made = fs.mkdtempSync(path.join(os.tmpdir(), "pi-ext-"));
  rubbish.push(made);
  return made;
}

/** Hand this to `after()` in every file that calls `scratch` or `load`. */
export function cleanup() {
  for (const directory of rubbish) {
    fs.rmSync(directory, { recursive: true, force: true });
  }
  rubbish.length = 0;
}

export const CATALOG = [
  {
    name: "cheese_chat_send",
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
  return { address, asked, close: () => server.close() };
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
      python: "",
      background: "",
      jobs,
      tools: CATALOG,
      unavailable: "",
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
