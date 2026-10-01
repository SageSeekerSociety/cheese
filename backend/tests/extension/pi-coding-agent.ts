// pi's own package, reduced to the two tools the extension builds its bash on.
//
// In a room the extension imports these from pi itself; under `node --test`
// there is no pi, so `pi-double.ts` resolves the package name here. This is a
// shell that runs the command and hands back what it printed and how it ended —
// the part the extension relies on, and nothing of pi's own formatting.

import { spawn } from "node:child_process";
import * as path from "node:path";

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

export function createLsTool(cwd: string, settings: { operations: any }) {
  return {
    name: "ls",
    label: "ls",
    description: "List a directory",
    parameters: { type: "object", properties: { path: { type: "string" } } },
    async execute(_id: string, params: any, _signal?: any, _update?: any, ctx?: any) {
      const dir = resolved(cwd, params.path || ".", ctx);
      if (!(await settings.operations.exists(dir))) throw new Error(`Path not found: ${dir}`);
      const names: string[] = await settings.operations.readdir(dir);
      const shown = [];
      for (const name of names.sort()) {
        const stat = await settings.operations.stat(path.join(dir, name));
        shown.push(stat.isDirectory() ? `${name}/` : name);
      }
      return { content: [{ type: "text", text: shown.join("\n") }] };
    },
  };
}

export function createFindTool(cwd: string, settings: { operations: any }) {
  return {
    name: "find",
    label: "find",
    description: "Find files",
    parameters: { type: "object", properties: { pattern: { type: "string" } } },
    async execute(_id: string, params: any, _signal?: any, _update?: any, ctx?: any) {
      const dir = resolved(cwd, params.path || ".", ctx);
      if (!(await settings.operations.exists(dir))) throw new Error(`Path not found: ${dir}`);
      const paths: string[] = await settings.operations.glob(params.pattern, dir, { limit: 1000 });
      return {
        content: [{ type: "text", text: paths.map((p) => p.slice(dir.length + 1)).join("\n") }],
      };
    },
  };
}

// pi's grep searches with ripgrep where pi runs, whatever operations it is
// given; the extension replaces its execute, so only its face is needed here.
export function createGrepTool(_cwd: string) {
  return {
    name: "grep",
    label: "grep",
    description: "Search file contents",
    parameters: { type: "object", properties: { pattern: { type: "string" } } },
    async execute(): Promise<never> {
      throw new Error("pi's own grep searched the session host");
    },
  };
}

export const DEFAULT_MAX_BYTES = 50 * 1024;

export function formatSize(bytes: number) {
  return `${bytes / 1024}KB`;
}

export function truncateHead(content: string, _options?: object) {
  const truncated = Buffer.byteLength(content) > DEFAULT_MAX_BYTES;
  return { content: truncated ? content.slice(0, DEFAULT_MAX_BYTES) : content, truncated };
}

export function truncateLine(line: string, maxChars = 500) {
  return line.length > maxChars
    ? { text: `${line.slice(0, maxChars)}... [truncated]`, wasTruncated: true }
    : { text: line, wasTruncated: false };
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
