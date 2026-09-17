// pi 那份 extension 的行为测试：从 pi 会给它的那一个接口进去。
//
// Everything here is driven through the extension's default export — the only
// thing pi ever calls — with a stand-in `pi` that records what was registered
// and replays the events pi raises. Nothing reaches inside: a test that read
// the module's internals would pass by construction and keep passing after the
// behaviour broke.
//
// Run with `node --test` and nothing else. No bundler, no transpiler, no
// node_modules: Node strips the types itself. That is not a convenience — a
// session machine runs this extension with no toolchain at all, and a test lane
// that needed one would be the first place that stopped being true.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as net from "node:net";
import * as os from "node:os";
import * as path from "node:path";
import { after, describe, it } from "node:test";

const SOURCE = new URL(
  "../../app/domain/agent/harness/pi/platform.ts",
  import.meta.url,
).href;

const NOTICE = "【平台】以下是平台自动发出的指令，不是任何人手打的话：";

type Handler = (event: any, ctx: any) => any;

/** What pi hands an extension, reduced to what this one actually uses. */
class FakePi {
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

function scratch(): string {
  const made = fs.mkdtempSync(path.join(os.tmpdir(), "pi-ext-"));
  rubbish.push(made);
  return made;
}

after(() => {
  for (const directory of rubbish) fs.rmSync(directory, { recursive: true, force: true });
});

const CATALOG = [
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
async function runner(answer: (request: any) => any) {
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
async function load(manifest: Partial<Record<string, unknown>> = {}) {
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

describe("平台工具", () => {
  it("目录里的每条命令都成为一个工具，参数表就是 CLI 自己的那份", async () => {
    const { pi } = await load();

    assert.ok(pi.tools.has("cheese_doc_get"));
    assert.deepEqual(
      pi.tools.get("cheese_doc_get").parameters,
      CATALOG[1].inputSchema,
      "a tool the model cannot fill in correctly is a tool it cannot call",
    );
  });

  it("chat_send 和 cheese_chat_send 都在，而且跑的是同一条命令", async () => {
    // The room's system prompt says `chat_send` on every turn; the CLI catalog
    // can only call the command what it is. Serving one of the two names is the
    // instruction working for one teammate and failing for the other.
    const socket = await runner(() => ({
      result: { status: 0, stdout: "sent", stderr: "" },
    }));
    const { pi } = await load({ socket: socket.address });

    for (const name of ["chat_send", "cheese_chat_send"]) {
      const answer = await pi.call(name, { content: "第一版好了" }, { cwd: "/work" });
      assert.match(answer.content[0].text, /sent/);
    }
    assert.deepEqual(
      socket.asked.map((request) => request.params.tool),
      ["cheese_chat_send", "cheese_chat_send"],
      "the alias has to run the command, not a command named after itself",
    );
    assert.equal(socket.asked[0].params.cwd, "/work", "the checkout, not the runner's cwd");
    socket.close();
  });

  it("命令失败时带回它自己说的话，并且算作错误", async () => {
    const socket = await runner(() => ({
      result: { status: 2, stdout: "", stderr: "没有这个话题" },
    }));
    const { pi } = await load({ socket: socket.address });

    const answer = await pi.call("cheese_doc_get", {});
    assert.equal(answer.isError, true);
    assert.match(answer.content[0].text, /没有这个话题/);
    socket.close();
  });
});

describe("仓库自己的说明", () => {
  const withRepo = async (files: Record<string, string>) => {
    const { pi } = await load();
    const cwd = scratch();
    for (const [name, body] of Object.entries(files)) {
      fs.writeFileSync(path.join(cwd, name), body);
    }
    return { pi, cwd };
  };

  it("追加在系统提示末尾，前面那一段一个字不动", async () => {
    // Where it goes is the point, not that it goes. Everything before it is
    // identical on every turn of the session and is what a provider cache
    // matches on; put in front, it invalidates the whole prompt each turn.
    const { pi, cwd } = await withRepo({ "CLAUDE.md": "# 本仓约定\n\n用 pnpm。\n" });
    const before = "平台系统提示：你在一个房间里。";

    const answer = await pi.emit("before_agent_start", { systemPrompt: before }, { cwd });

    assert.ok(answer.systemPrompt.startsWith(before), "the platform's prompt moved");
    assert.match(answer.systemPrompt, /用 pnpm。/);
    assert.ok(answer.systemPrompt.indexOf("用 pnpm。") > before.length);
  });

  it("没有说明文件就什么都不改", async () => {
    const { pi, cwd } = await withRepo({});
    assert.equal(await pi.emit("before_agent_start", { systemPrompt: "x" }, { cwd }), undefined);
  });

  it("两个文件是同一份内容时只读进来一次", async () => {
    // A repository that keeps CLAUDE.md and points AGENTS.md at it should not
    // pay for it twice on every turn.
    const body = "# 同一份\n\n只说一次。\n";
    const { pi, cwd } = await withRepo({ "CLAUDE.md": body, "AGENTS.md": body });

    const answer = await pi.emit("before_agent_start", { systemPrompt: "x" }, { cwd });
    assert.equal(answer.systemPrompt.split("只说一次。").length - 1, 1);
  });
});

describe("连续工具调用", () => {
  const quiet = async (pi: FakePi, count: number, name = "bash", isError = false) => {
    await pi.emit("turn_end", {
      toolResults: Array.from({ length: count }, () => ({ toolName: name, isError })),
    });
  };
  const injected = async (pi: FakePi) => {
    const answer = await pi.emit("context", { messages: [{ role: "user", content: [] }] });
    if (!answer) return null;
    return answer.messages.at(-1).content[0].text;
  };

  it("不到阈值不提醒", async () => {
    const { pi } = await load();
    await quiet(pi, 9);
    assert.equal(await injected(pi), null, "ordinary work must not be interrupted");
  });

  it("到阈值提醒一次，用的是提示里那个工具名", async () => {
    const { pi } = await load();
    await quiet(pi, 10);

    const said = await injected(pi);
    assert.ok(said?.startsWith(NOTICE), "it has to read as a platform instruction");
    assert.match(said, /chat_send/);
    assert.doesNotMatch(said, /cheese_chat_send/);
  });

  it("成功发布之后重新数起", async () => {
    const { pi } = await load();
    await quiet(pi, 10);
    assert.ok(await injected(pi));

    await quiet(pi, 1, "chat_send");
    assert.equal(await injected(pi), null, "the room has just been told what is going on");

    await quiet(pi, 9);
    assert.equal(await injected(pi), null);
    await quiet(pi, 1);
    assert.ok(await injected(pi), "silence since the last publish is what counts");
  });

  it("发布失败不算发过话", async () => {
    const { pi } = await load();
    await quiet(pi, 9);
    await quiet(pi, 1, "chat_send", true);
    assert.ok(await injected(pi), "a message nobody received is not a message");
  });

  it("房间没有发布工具时不提这件事", async () => {
    // A reminder naming a tool that is not registered is worse than silence.
    const { pi } = await load({ tools: [CATALOG[1]] });
    await quiet(pi, 30);
    assert.equal(await injected(pi), null);
  });
});

describe("读后台任务的输出", () => {
  const job = async (output: string) => {
    const loadedPi = await load({ python: "python3", background: "/bg.py" });
    const dir = path.join(loadedPi.jobs, "job-1-abc");
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "meta.json"), JSON.stringify({ command: "x" }));
    fs.writeFileSync(path.join(dir, "output"), output);
    return loadedPi.pi;
  };

  it("画面控制指令不进上下文，字留下", async () => {
    // Everything is asked for a dumb terminal, but a program that paints anyway
    // would otherwise spend the room's context on instructions to a screen
    // nobody is looking at.
    const pi = await job("\x1b[32mrunning tests\x1b[0m\r\n\x1b[1;35m>>> \x1b[0m42\r\n");

    const said = (await pi.call("bash_read", { id: "job-1-abc" })).content[0].text;
    assert.equal(said, "running tests\n>>> 42\n");
  });

  it("就地重写的那一行只留它最后的样子", async () => {
    const pi = await job("10%\r50%\r100% done\r\n");
    const said = (await pi.call("bash_read", { id: "job-1-abc" })).content[0].text;
    assert.equal(said, "100% done\n");
  });

  it("切到全屏的程序如实说出来，而不是把噪音贴进去", async () => {
    const pi = await job("starting\r\n\x1b[?1049h\x1b[2J\x1b[H");
    const said = (await pi.call("bash_read", { id: "job-1-abc" })).content[0].text;
    assert.match(said, /全屏/);
    assert.match(said, /bash_kill/);
  });

  it("读过的不再读第二遍", async () => {
    // A job outlives the session that started it; a reader that began at zero
    // every time would hand the room a megabyte it has already seen.
    const pi = await job("first\r\n");
    assert.equal((await pi.call("bash_read", { id: "job-1-abc" })).content[0].text, "first\n");
    const again = (await pi.call("bash_read", { id: "job-1-abc" })).content[0].text;
    assert.match(again, /没有新输出/);
  });

  it("任务号不是路径", async () => {
    const pi = await job("x");
    await assert.rejects(
      () => pi.call("bash_read", { id: "../../../etc" }),
      /no such job/,
    );
  });
});
