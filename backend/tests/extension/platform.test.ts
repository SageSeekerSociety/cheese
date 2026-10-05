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
import * as path from "node:path";
import { after, describe, it } from "node:test";

// One double for this seam, shared with harness-contract.test.ts: two
// hand-written stand-ins for the same interface drift, and the one that
// drifted goes on passing.
import {
  beforeAgentStart,
  CATALOG,
  cleanup,
  load,
  machine,
  runner,
  scratch,
} from "./pi-double.ts";

after(cleanup);

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

  it("chat_send 以提示里的名字注册，调用送到 runner 的就是这个名字", async () => {
    // The room's system prompt says `chat_send` on every turn, and the catalog
    // carries the platform's tool under that name — one name, one tool.
    const socket = await runner(() => ({
      result: { status: 0, stdout: "sent", stderr: "" },
    }));
    const { pi } = await load({ socket: socket.address, workspace: "/work" });

    const answer = await pi.call("chat_send", { content: "第一版好了" }, { cwd: "/work" });
    assert.match(answer.content[0].text, /sent/);
    assert.ok(!pi.tools.has("cheese_chat_send"), "no second name for the same tool");
    assert.deepEqual(
      socket.asked.map((request) => request.params.tool),
      ["chat_send"],
    );
    assert.equal(socket.asked[0].params.cwd, "/work", "the checkout on the machine");
    socket.close();
  });

  it("命令失败时带回它自己说的话", async () => {
    // Only the half this file owns: what the CLI wrote on stderr is what the
    // model gets to read. That a non-zero status is `isError` is the fixture
    // set's (a-tool-that-failed.json, driven by harness-contract.test.ts), and
    // asserting it here too would be a second declaration of one rule.
    const socket = await runner(() => ({
      result: { status: 2, stdout: "", stderr: "没有这个话题" },
    }));
    const { pi } = await load({ socket: socket.address });

    const answer = await pi.call("cheese_doc_get", {});
    assert.match(answer.content[0].text, /没有这个话题/);
    socket.close();
  });
});

describe("项目的 MCP 服务器", () => {
  const TRACKER = {
    name: "mcp__tracker__whoami",
    description: "Say which credential reached the server.",
    inputSchema: { type: "object", properties: { note: { type: "string" } } },
  };

  it("runner 列出的每个工具都注册成 pi 的工具，调用送回 runner 的 mcp", async () => {
    const socket = await runner(() => ({
      result: { content: [{ type: "text", text: "reached" }], isError: false },
    }));
    const { pi } = await load({ socket: socket.address, mcp: [TRACKER] });

    assert.deepEqual(pi.tools.get(TRACKER.name).parameters, TRACKER.inputSchema);
    const answer = await pi.call(TRACKER.name, { note: "hi" });
    assert.deepEqual(answer.content, [{ type: "text", text: "reached" }]);
    assert.deepEqual(socket.asked, [
      {
        method: "mcp",
        params: { id: "call-1", tool: TRACKER.name, arguments: { note: "hi" } },
      },
    ]);
    socket.close();
  });

  it("服务器说失败的调用抛出去，pi 才会把它标成错误", async () => {
    // pi 0.85.1 sets a tool result's error flag only when `execute` throws; a
    // returned `isError` is ignored, and the model would read a failure as an
    // answer.
    const socket = await runner(() => ({
      result: { content: [{ type: "text", text: "no such issue" }], isError: true },
    }));
    const { pi } = await load({ socket: socket.address, mcp: [TRACKER] });

    await assert.rejects(pi.call(TRACKER.name, {}), /no such issue/);
    socket.close();
  });
});

describe("项目的 hooks 管着 pi 自己的工具", () => {
  const TRACKER = {
    name: "mcp__tracker__whoami",
    description: "Say which credential reached the server.",
    inputSchema: { type: "object", properties: {} },
  };

  it("PreToolUse 拒了的命令不会跑，模型读到的是 hook 给的理由", async () => {
    const socket = await runner(() => ({ result: { denied: "PROJECT_POLICY: no" } }));
    const { pi } = await load({ socket: socket.address, workspace: "/work" });
    const ran: any[] = [];

    const answer = await pi.run("bash", { command: "rm -rf /" }, (input) => {
      ran.push(input);
      return { content: [{ type: "text", text: "gone" }] };
    }, { cwd: "/work" });

    assert.deepEqual(ran, [], "a denied call never reaches the tool");
    assert.equal(answer.isError, true);
    assert.equal(answer.content[0].text, "PROJECT_POLICY: no");
    assert.deepEqual(socket.asked, [
      {
        method: "hooks",
        params: {
          event: "PreToolUse",
          tool: "bash",
          id: "call-1",
          input: { command: "rm -rf /" },
          cwd: "/work",
        },
      },
    ]);
    socket.close();
  });

  it("放行的调用跑 hook 改过的参数，跑完带着结果再问一次 PostToolUse", async () => {
    const socket = await runner((request) =>
      request.params.event === "PreToolUse"
        ? { result: { input: { path: "notes.md" } } }
        : { result: { input: request.params.input } },
    );
    const { pi } = await load({ socket: socket.address, workspace: "/work" });
    const ran: any[] = [];

    const answer = await pi.run("read", { path: "old.md", limit: 5 }, (input) => {
      ran.push({ ...input });
      return { content: [{ type: "text", text: "the notes" }] };
    });

    assert.deepEqual(ran, [{ path: "notes.md" }], "the input the hook returned, whole");
    assert.deepEqual(answer, { content: [{ type: "text", text: "the notes" }], isError: false });
    assert.deepEqual(socket.asked[1].params, {
      event: "PostToolUse",
      tool: "read",
      id: "call-1",
      input: { path: "notes.md" },
      cwd: "/work",
      result: { content: [{ type: "text", text: "the notes" }] },
    });
    socket.close();
  });

  it("PostToolUse 拒了，调用已经发生，理由加进结果里", async () => {
    const socket = await runner((request) =>
      request.params.event === "PreToolUse"
        ? { result: { input: request.params.input } }
        : { result: { denied: "PROJECT_POLICY: review this" } },
    );
    const { pi } = await load({ socket: socket.address });

    const answer = await pi.run("write", { path: "a", content: "b" }, () => ({
      content: [{ type: "text", text: "written" }],
    }));

    assert.deepEqual(answer.content, [
      { type: "text", text: "written" },
      { type: "text", text: "PostToolUse hook: PROJECT_POLICY: review this" },
    ]);
    socket.close();
  });

  it("问不到 runner 时调用不跑", async () => {
    const { pi } = await load({ socket: "/nonexistent/runner.sock" });
    const ran: any[] = [];

    const answer = await pi.run("bash", { command: "echo hi" }, (input) => {
      ran.push(input);
      return { content: [] };
    });

    assert.deepEqual(ran, []);
    assert.equal(answer.isError, true);
  });

  it("失败的调用不跑 PostToolUse", async () => {
    const socket = await runner((request) => ({ result: { input: request.params.input } }));
    const { pi } = await load({ socket: socket.address });

    await pi.run("bash", { command: "false" }, () => {
      throw new Error("exit 1");
    });

    assert.deepEqual(
      socket.asked.map((request) => request.params.event),
      ["PreToolUse"],
    );
    socket.close();
  });

  it("平台工具和 MCP 工具不在这里问：前者不跑 hooks，后者由 runner 在调用两边跑", async () => {
    const socket = await runner(() => ({
      result: { status: 0, stdout: "ok", stderr: "" },
    }));
    const { pi } = await load({ socket: socket.address, mcp: [TRACKER] });

    for (const name of ["chat_send", TRACKER.name]) {
      await pi.run(name, {}, () => ({ content: [{ type: "text", text: "ok" }] }));
    }

    assert.deepEqual(socket.asked, []);
    socket.close();
  });
});

describe("会话在哪、仓库自己说了什么", () => {
  it("模型被告知的工作目录是执行机上的项目，不是 pi 自己跑在哪", async () => {
    const socket = await runner(() => ({ result: { context: "" } }));
    const { pi } = await load({ socket: socket.address, workspace: "/machine/room" });
    const event = beforeAgentStart("/session-host/x");

    const answer = await pi.emit("before_agent_start", event);

    assert.equal(event.systemPromptOptions.cwd, "/machine/room");
    // pi renders the prompt from these options; a handler that returned a
    // `systemPrompt` would be replacing pi's own text again, which is how the
    // wording went out from under us once.
    assert.equal(answer, undefined, "the prompt is pi's to render, not ours to replace");
    socket.close();
  });

  it("仓库的说明是它自己的 section，平台的提示词一个字不动", async () => {
    // A section of its own is what pi 1.0 can record as a delta: everything
    // before it is identical on every turn of the session and is what a
    // provider cache matches on. Rewriting the rendered prompt instead would
    // invalidate the whole of it each turn, and match on wording pi owns.
    const socket = await runner(() => ({ result: { context: "# 本仓约定\n\n用 pnpm。" } }));
    const { pi, home } = await load({ socket: socket.address });
    const event = beforeAgentStart(home);

    const answer = await pi.emit("before_agent_start", event);

    assert.equal(event.systemPromptOptions.sections.repository, "# 本仓约定\n\n用 pnpm。");
    assert.equal(answer, undefined, "the platform's own prompt is not replaced");
    socket.close();
  });

  it("仓库什么都没说、目录也对，就什么都不改", async () => {
    const socket = await runner(() => ({ result: { context: "" } }));
    const { pi, home } = await load({ socket: socket.address });
    const event = beforeAgentStart(home);

    await pi.emit("before_agent_start", event);

    assert.equal(event.systemPromptOptions.cwd, home);
    assert.deepEqual(event.systemPromptOptions.sections, {});
    socket.close();
  });

  it("没有结构化提示词的 pi：当场说在 stderr 上，不静默地什么都不做", async () => {
    // What 0.85.1's wording change did to the old rewrite, made visible: a
    // build that does not carry the options cannot be told where the session
    // is, and every tool here works on the room's machine.
    const socket = await runner(() => ({ result: { context: "" } }));
    const { pi } = await load({ socket: socket.address, workspace: "/machine/room" });
    const written: string[] = [];
    const stderr = process.stderr.write;
    process.stderr.write = ((chunk: any) => {
      written.push(String(chunk));
      return true;
    }) as typeof process.stderr.write;
    try {
      await pi.emit("before_agent_start", { type: "before_agent_start", prompt: "" });
    } finally {
      process.stderr.write = stderr;
    }
    assert.match(written.join(""), /no systemPromptOptions/);
    socket.close();
  });
});

describe("没有手的那条路：人自己的芝士，没有机器", () => {
  it("会话机上的目录不会被说给模型", async () => {
    const { pi } = await load({ hands: false, workspace: "/machine/room" });
    const event = beforeAgentStart("/session-host/x");

    await pi.emit("before_agent_start", event);

    // Emptied, not named: what this session has to say about a directory is
    // that it has none. The rendered prompt is checked against the real binary
    // in tests/unit/test_personal_sessions.py.
    assert.equal(event.systemPromptOptions.cwd, "");
  });
});

describe("pi 自己的工具，手在执行机上", () => {
  it("写和读都经由 runner，路径按执行机上的项目来算", async () => {
    const files = new Map<string, Buffer>();
    const socket = await runner((request) => {
      const { operation, path: file, data } = request.params;
      if (operation === "write") files.set(file, Buffer.from(data, "base64"));
      if (operation === "read") return { result: { data: files.get(file)?.toString("base64") } };
      if (operation === "access" && !files.has(file)) return { error: "ENOENT" };
      return { result: {} };
    });
    const { pi } = await load({ socket: socket.address, workspace: "/machine/room" });

    await pi.call("write", { path: "plan.md", content: "# 计划\n" }, { cwd: "/session-host" });
    const read = await pi.call("read", { path: "plan.md" }, { cwd: "/session-host" });

    assert.equal(files.get("/machine/room/plan.md")?.toString(), "# 计划\n");
    assert.match(read.content[0].text, /# 计划/);
    assert.ok(
      socket.asked.every((request) => request.method === "files"),
      "nothing of pi's own reached this host's files",
    );
    socket.close();
  });

  it("看目录、找文件、搜内容也都经由 runner，在执行机上的项目里", async () => {
    const socket = await runner((request) => {
      const { operation, path: where } = request.params;
      if (operation === "stat") {
        return { result: { exists: true, directory: !where.endsWith(".md") } };
      }
      if (operation === "list") return { result: { entries: [["src", true], ["README.md", false]] } };
      if (operation === "glob") return { result: { paths: [`${where}/src/app.py`] } };
      if (operation === "grep") {
        return {
          result: {
            matches: [{ path: "src/app.py", line: 2, lines: [[2, "NEEDLE = 1"]] }],
            limited: false,
          },
        };
      }
      return { error: `unexpected ${operation}` };
    });
    const { pi } = await load({ socket: socket.address, workspace: "/machine/room" });
    const at = { cwd: "/session-host" };

    const listed = await pi.call("ls", {}, at);
    const found = await pi.call("find", { pattern: "*.py" }, at);
    const grepped = await pi.call("grep", { pattern: "NEEDLE" }, at);

    assert.equal(listed.content[0].text, "README.md\nsrc/");
    assert.equal(found.content[0].text, "src/app.py");
    assert.equal(grepped.content[0].text, "src/app.py:2: NEEDLE = 1");
    assert.ok(socket.asked.every((request) => request.method === "files"));
    assert.ok(
      socket.asked.every((request) => request.params.path.startsWith("/machine/room")),
      "every look was at the project on the machine",
    );
    socket.close();
  });

  it("没有 runner 可问时，工具失败，而不是落到这台机器上", async () => {
    const { pi } = await load({ socket: "/nonexistent/runner.sock" });
    await assert.rejects(pi.call("write", { path: "x", content: "y" }));
  });
});

describe("连续工具调用", () => {
  it("多少次都不由 extension 插话：提醒房间的是平台", async () => {
    // One reminder, sent by the platform to every harness alike. A second one
    // produced here would reach pi rooms only, and reach them twice.
    const { pi } = await load();
    for (let turn = 0; turn < 5; turn++) {
      await pi.emit("turn_end", {
        toolResults: Array.from({ length: 10 }, () => ({
          toolName: "bash",
          isError: false,
        })),
      });
    }
    const messages = [{ role: "user", content: [] }];
    const answer = await pi.emit("context", { messages });
    assert.deepEqual(answer?.messages ?? messages, messages);
  });
});

describe("读后台任务的输出", () => {
  const job = async (output: string) => {
    const loadedPi = await load();
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

// 任务起不来的时候，这一侧是唯一会说话的一侧。
//
// The runner starts a job on the room's machine and copies what it prints to
// the files these tools read. What it could not do reaches the room only if
// these tools say it — a job that never started and a server that has not
// printed its banner yet look alike from the files alone.
describe("后台任务起不来的时候", () => {
  it("runner 说出的原因，就是这个工具的回答", async () => {
    const socket = await runner(() => ({ error: "python3: command not found" }));
    const { pi } = await load({ socket: socket.address });

    const answer = await pi.call("bash_start", { command: "sleep 30" });
    assert.equal(answer.isError, true);
    assert.match(answer.content[0].text, /python3: command not found/);
    socket.close();
  });

  it("没有人在跟的任务，不叫 running", async () => {
    // `exit` is written by the runner that copies the job, so a runner that is
    // gone leaves none — and a reader going by that file alone waits for an
    // answer that is never coming.
    const { pi, jobs } = await load();
    const dir = path.join(jobs, "job-1-gone");
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(
      path.join(dir, "meta.json"),
      JSON.stringify({ command: "sleep 30", pid: 0x400000 }),
    );

    const said = (await pi.call("bash_list", {})).content[0].text;
    assert.doesNotMatch(said, /running/);
  });

  it("打字进一个已经结束的任务，得到的是它结束了", async () => {
    const { pi, jobs } = await load();
    const dir = path.join(jobs, "job-1-over");
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "meta.json"), JSON.stringify({ command: "x" }));
    fs.writeFileSync(path.join(dir, "exit"), JSON.stringify({ status: 3, at: 0 }));

    await assert.rejects(
      () => pi.call("bash_write", { id: "job-1-over", text: "hello" }),
      /退出码 3/,
    );
  });
});

describe("有人发来消息的时候", () => {
  function owe(id: string) {
    const file = process.env.CHEESE_REPLY_OWED as string;
    fs.writeFileSync(
      file,
      JSON.stringify({
        id,
        answers: ["chat_send", "cheese_ask"],
        reads: ["cheese_chat_list"],
        reason: "REPLY_FIRST",
        answered: `${file}.answered`,
      }),
    );
  }

  function fresh() {
    process.env.CHEESE_REPLY_OWED = path.join(scratch(), "reply-owed.json");
  }

  // A job has ended once the extension writes its `exit` file. How long after
  // the command that is depends on the machine, and a loaded one takes longer
  // than any fixed wait; the bound only turns a hang into a failure. It stays
  // under the 30 s of the `sleep 30` below, so that one ending by itself is
  // not mistaken for being stopped.
  async function ended(dir: string): Promise<boolean> {
    const deadline = Date.now() + 20_000;
    while (!fs.existsSync(path.join(dir, "exit"))) {
      if (Date.now() > deadline) return false;
      await new Promise((done) => setTimeout(done, 50));
    }
    return true;
  }

  it("先回话，别的工具在那之前都被拒", async () => {
    fresh();
    const { pi } = await load();
    owe("m1");

    const refused = await pi.emit("tool_call", { toolName: "bash", input: {} });
    assert.deepEqual(refused, { block: true, reason: "REPLY_FIRST" });
    assert.equal(await pi.emit("tool_call", { toolName: "chat_send", input: {} }), undefined);
    assert.equal(await pi.emit("tool_call", { toolName: "bash", input: {} }), undefined);
    // Written down where the runner reads it, so the turn may end.
    const answered = `${process.env.CHEESE_REPLY_OWED}.answered`;
    assert.equal(fs.readFileSync(answered, "utf8"), "m1");
  });

  it("读房间的记录不被拒，也不算回了话", async () => {
    fresh();
    const { pi } = await load();
    owe("m1");

    assert.equal(await pi.emit("tool_call", { toolName: "cheese_chat_list", input: {} }), undefined);
    const refused = await pi.emit("tool_call", { toolName: "bash", input: {} });
    assert.deepEqual(refused, { block: true, reason: "REPLY_FIRST" });
    assert.ok(!fs.existsSync(`${process.env.CHEESE_REPLY_OWED}.answered`));
  });

  it("正在跑的命令转到后台，模型马上拿回控制，命令照样跑完", async () => {
    fresh();
    const socket = await machine();
    const { pi, jobs } = await load({ socket: socket.address });

    const started = Date.now();
    const call = pi.call("bash", { command: "echo EARLY; sleep 3; echo LATE" });
    await new Promise((done) => setTimeout(done, 1500));
    owe("m1");
    const answer = await call;

    assert.ok(Date.now() - started < 3000, "the model waited for the command");
    const said = answer.content[0].text;
    assert.match(said, /EARLY/);
    assert.match(said, /转到后台/);
    const job = /任务 (job-[a-z0-9-]+)/.exec(said)?.[1] as string;
    assert.match((await pi.call("bash_list", {})).content[0].text, /running/);

    assert.ok(await ended(path.join(jobs, job)), "the command never finished");
    assert.match(fs.readFileSync(path.join(jobs, job, "output"), "utf8"), /LATE/);
    assert.match((await pi.call("bash_list", {})).content[0].text, /exited 0/);
    socket.close();
  });

  it("转到后台的命令用 bash_kill 停得掉", async () => {
    fresh();
    const socket = await machine();
    const { pi, jobs } = await load({ socket: socket.address });

    const call = pi.call("bash", { command: "sleep 30" });
    await new Promise((done) => setTimeout(done, 1000));
    owe("m1");
    const job = /任务 (job-[a-z0-9-]+)/.exec((await call).content[0].text)?.[1] as string;

    await pi.call("bash_kill", { id: job });
    assert.ok(await ended(path.join(jobs, job)), "the command is still running");
    socket.close();
  });

  it("没有人说话，命令照常跑完再返回", async () => {
    fresh();
    const socket = await machine();
    const { pi } = await load({ socket: socket.address });

    const answer = await pi.call("bash", { command: "sleep 1; echo DONE" });
    assert.match(answer.content[0].text, /DONE/);
    assert.doesNotMatch(answer.content[0].text, /转到后台/);
    socket.close();
  });
});
