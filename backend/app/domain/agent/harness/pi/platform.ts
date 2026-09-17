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
};

const HOME = process.env.CHEESE_PI_EXTENSION ?? "";

function manifest(): Manifest {
  const empty: Manifest = {
    socket: "",
    state: "",
    tools: [],
    unavailable: "the runner wrote no manifest",
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

export default function (pi: any) {
  const spec = manifest();
  if (spec.tools.length) registerPlatformTools(pi, spec);
  else if (spec.unavailable) {
    // Said where a launch failure is read, not swallowed: a room whose platform
    // tools are all missing looks from the inside exactly like a room that was
    // never given any, and the agent will conclude it must shell out.
    process.stderr.write(`[cheese] no platform tools: ${spec.unavailable}\n`);
  }
}
