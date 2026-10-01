// pi's own package, reduced to the two tools the extension builds its bash on.
//
// In a room the extension imports these from pi itself; under `node --test`
// there is no pi, so `pi-double.ts` resolves the package name here. This is a
// shell that runs the command and hands back what it printed and how it ended —
// the part the extension relies on, and nothing of pi's own formatting.

import { spawn } from "node:child_process";

type Options = {
  onData: (data: Buffer) => void;
  signal?: AbortSignal;
  timeout?: number;
  env?: NodeJS.ProcessEnv;
};

export function createLocalBashOperations() {
  return {
    exec(command: string, cwd: string, options: Options): Promise<{ exitCode: number | null }> {
      return new Promise((resolve, reject) => {
        const child = spawn("/bin/sh", ["-c", command], {
          cwd,
          env: options.env ?? process.env,
          detached: true,
          stdio: ["ignore", "pipe", "pipe"],
        });
        child.stdout.on("data", options.onData);
        child.stderr.on("data", options.onData);
        const stop = () => {
          try {
            process.kill(-(child.pid as number), "SIGKILL");
          } catch {
            /* already gone */
          }
        };
        options.signal?.addEventListener("abort", stop, { once: true });
        child.on("error", reject);
        child.on("close", (code) => {
          options.signal?.removeEventListener("abort", stop);
          resolve({ exitCode: code });
        });
      });
    },
  };
}

// read, write and edit as pi builds them on their operations, reduced to what
// the extension relies on: the path is resolved against the directory the call
// is handed (pi prefers it to the one the tool was built with), and every byte
// goes through the operations.
function resolved(cwd: string, file: string, ctx: any) {
  return file.startsWith("/") ? file : `${ctx?.cwd || cwd}/${file}`;
}

export function createReadTool(cwd: string, settings: { operations: any }) {
  return {
    name: "read",
    label: "read",
    description: "Read a file",
    parameters: { type: "object", properties: { path: { type: "string" } } },
    async execute(_id: string, params: any, _signal?: any, _update?: any, ctx?: any) {
      const file = resolved(cwd, params.path, ctx);
      await settings.operations.access(file);
      const data: Buffer = await settings.operations.readFile(file);
      return { content: [{ type: "text", text: data.toString("utf8") }], details: {} };
    },
  };
}

export function createWriteTool(cwd: string, settings: { operations: any }) {
  return {
    name: "write",
    label: "write",
    description: "Write a file",
    parameters: { type: "object", properties: { path: { type: "string" } } },
    async execute(_id: string, params: any, _signal?: any, _update?: any, ctx?: any) {
      const file = resolved(cwd, params.path, ctx);
      await settings.operations.mkdir(file.replace(/\/[^/]*$/, ""));
      await settings.operations.writeFile(file, params.content);
      return { content: [{ type: "text", text: `Successfully wrote to ${params.path}` }] };
    },
  };
}

export function createEditTool(cwd: string, settings: { operations: any }) {
  return {
    name: "edit",
    label: "edit",
    description: "Edit a file",
    parameters: { type: "object", properties: { path: { type: "string" } } },
    async execute(_id: string, params: any, _signal?: any, _update?: any, ctx?: any) {
      const file = resolved(cwd, params.path, ctx);
      await settings.operations.access(file);
      let text = (await settings.operations.readFile(file)).toString("utf8");
      for (const edit of params.edits) text = text.replace(edit.oldText, edit.newText);
      await settings.operations.writeFile(file, text);
      return { content: [{ type: "text", text: "edited" }] };
    },
  };
}

export function createBashTool(cwd: string, settings: { operations?: any } = {}) {
  const operations = settings.operations ?? createLocalBashOperations();
  return {
    name: "bash",
    label: "bash",
    description: "Run a shell command",
    parameters: {
      type: "object",
      properties: { command: { type: "string" }, timeout: { type: "number" } },
      required: ["command"],
    },
    async execute(_id: string, params: any, signal?: AbortSignal) {
      const printed: Buffer[] = [];
      const { exitCode } = await operations.exec(params.command, cwd, {
        onData: (data: Buffer) => printed.push(data),
        signal,
      });
      const text = Buffer.concat(printed).toString("utf8");
      return {
        content: [{ type: "text", text: exitCode ? `${text}\nexit code ${exitCode}` : text }],
        details: {},
      };
    },
  };
}
