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
  CATALOG,
  cleanup,
  load,
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
    const { pi } = await load({ socket: socket.address });

    const answer = await pi.call("chat_send", { content: "第一版好了" }, { cwd: "/work" });
    assert.match(answer.content[0].text, /sent/);
    assert.ok(!pi.tools.has("cheese_chat_send"), "no second name for the same tool");
    assert.deepEqual(
      socket.asked.map((request) => request.params.tool),
      ["chat_send"],
    );
    assert.equal(socket.asked[0].params.cwd, "/work", "the checkout, not the runner's cwd");
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

  it("CLAUDE.local.md 和 .claude/rules 也收，和 remote_execution 同一套", async () => {
    // The other harness reads all of these; a pi room that did not would have
    // the same repository telling two teammates different things.
    const { pi, cwd } = await withRepo({
      "CLAUDE.md": "# 主约定\n\n主。\n",
      "CLAUDE.local.md": "# 本机补充\n\n本机。\n",
    });
    fs.mkdirSync(path.join(cwd, ".claude", "rules"), { recursive: true });
    fs.writeFileSync(path.join(cwd, ".claude", "rules", "code.md"), "# 代码规则\n\n规则。\n");

    const answer = await pi.emit("before_agent_start", { systemPrompt: "x" }, { cwd });
    assert.match(answer.systemPrompt, /主。/);
    assert.match(answer.systemPrompt, /本机。/);
    assert.match(answer.systemPrompt, /规则。/);
    assert.match(answer.systemPrompt, /\.claude[\\/]rules[\\/]code\.md/);
  });

  it("@相对引用展开成正文，绝对路径的原样留着", async () => {
    const { pi, cwd } = await withRepo({
      "CLAUDE.md": "# 入口\n\n@rules/inner.md\n\n另见 @/etc/passwd。\n",
    });
    fs.mkdirSync(path.join(cwd, "rules"), { recursive: true });
    fs.writeFileSync(path.join(cwd, "rules", "inner.md"), "内层内容。\n");

    const answer = await pi.emit("before_agent_start", { systemPrompt: "x" }, { cwd });
    assert.match(answer.systemPrompt, /内层内容。/);
    assert.match(answer.systemPrompt, /@\/etc\/passwd/);
  });

  it("settings.json 不进提示词——它是可执行配置，不是约定", async () => {
    const { pi, cwd } = await withRepo({
      "CLAUDE.md": "# 约定\n\n正文。\n",
    });
    fs.mkdirSync(path.join(cwd, ".claude"), { recursive: true });
    fs.writeFileSync(
      path.join(cwd, ".claude", "settings.json"),
      JSON.stringify({ hooks: { PostToolUse: [{ command: "rm -rf /" }] } }),
    );

    const answer = await pi.emit("before_agent_start", { systemPrompt: "x" }, { cwd });
    assert.doesNotMatch(answer.systemPrompt, /PostToolUse/);
    assert.doesNotMatch(answer.systemPrompt, /settings\.json/);
  });

  it("超预算时截断并写明是哪一份文件", async () => {
    const { pi, cwd } = await withRepo({
      "CLAUDE.md": "# 大文件\n\n" + "字".repeat(70 * 1024),
    });

    const answer = await pi.emit("before_agent_start", { systemPrompt: "x" }, { cwd });
    assert.match(answer.systemPrompt, /truncated at 65536 bytes/);
    assert.match(answer.systemPrompt, /CLAUDE\.md/);
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

// 任务起不来的时候，这一侧是唯一会说话的一侧。
//
// The guardian daemonizes, so from the moment it forks it has no stdout, no
// stderr and nobody waiting on it. Everything it fails to do reaches the room
// only if these tools go looking — and until they did, a guardian that died in
// its first milliseconds was reported as a job that had started and simply not
// printed anything yet, by every one of them, forever.
describe("后台任务起不来的时候", () => {
  /** A stand-in guardian: whatever this shell leaves in the job directory. */
  async function guarded(script: string) {
    const home = scratch();
    const guardian = path.join(home, "guardian.sh");
    fs.writeFileSync(guardian, `#!/bin/sh\ndir="$2"\nmkdir -p "$dir"\n${script}\n`);
    fs.chmodSync(guardian, 0o755);
    return load({ python: "/bin/sh", background: guardian });
  }

  it("看守进程留下的原因，就是这个工具的回答", async () => {
    const { pi } = await guarded(`printf 'AF_UNIX path too long' > "$dir/error"`);

    const answer = await pi.call("bash_start", { command: "sleep 30" });
    assert.equal(answer.isError, true);
    assert.match(answer.content[0].text, /AF_UNIX path too long/);
  });

  it("看守进程什么都没留下时，也说出来", async () => {
    const { pi } = await guarded("exit 1");

    const answer = await pi.call("bash_start", { command: "sleep 30" });
    assert.equal(answer.isError, true);
    assert.match(answer.content[0].text, /没能起来/);
  });

  it("只是起得慢，不算起不来", async () => {
    // python3 在一台负载满的机器上冷启动可以超过这段等待，而机器正忙恰恰是有人
    // 把活放到后台去的时候。把慢说成死，模型会再起一遍 —— 两个 dev server 抢
    // 同一个端口，正是这套东西本来要避免的那种残留。
    const { pi } = await guarded(`sleep 3 &`);

    const answer = await pi.call("bash_start", { command: "sleep 30" });
    assert.notEqual(answer.isError, true);
    assert.match(answer.content[0].text, /started/);
  });

  it("看守进程不在了的任务，不叫 running", async () => {
    // `exit` is written by the guardian, so a guardian that was killed outright
    // leaves none — and a reader going by that file alone waits for an answer
    // that is never coming.
    const { pi, jobs } = await load({ python: "python3", background: "/bg.py" });
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
    const { pi, jobs } = await load({ python: "python3", background: "/bg.py" });
    const dir = path.join(jobs, "job-1-over");
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(
      path.join(dir, "meta.json"),
      JSON.stringify({ command: "x", sock: path.join(dir, "never-bound.sock") }),
    );
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
      JSON.stringify({ id, answers: ["chat_send", "cheese_ask"], reason: "REPLY_FIRST" }),
    );
  }

  function fresh() {
    process.env.CHEESE_REPLY_OWED = path.join(scratch(), "reply-owed.json");
  }

  it("先回话，别的工具在那之前都被拒", async () => {
    fresh();
    const { pi } = await load();
    owe("m1");

    const refused = await pi.emit("tool_call", { toolName: "bash", input: {} });
    assert.deepEqual(refused, { block: true, reason: "REPLY_FIRST" });
    assert.equal(await pi.emit("tool_call", { toolName: "chat_send", input: {} }), undefined);
    assert.equal(await pi.emit("tool_call", { toolName: "bash", input: {} }), undefined);
  });

  it("正在跑的命令转到后台，模型马上拿回控制，命令照样跑完", async () => {
    fresh();
    const { pi, jobs } = await load({ python: "python3", background: "/bg.py" });

    const started = Date.now();
    const call = pi.call("bash", { command: "echo EARLY; sleep 3; echo LATE" });
    await new Promise((done) => setTimeout(done, 500));
    owe("m1");
    const answer = await call;

    assert.ok(Date.now() - started < 2500, "the model waited for the command");
    const said = answer.content[0].text;
    assert.match(said, /EARLY/);
    assert.match(said, /转到后台/);
    const job = /任务 (job-[a-z0-9-]+)/.exec(said)?.[1] as string;
    assert.match((await pi.call("bash_list", {})).content[0].text, /running/);

    await new Promise((done) => setTimeout(done, 3500));
    assert.match(fs.readFileSync(path.join(jobs, job, "output"), "utf8"), /LATE/);
    assert.match((await pi.call("bash_list", {})).content[0].text, /exited 0/);
  });

  it("转到后台的命令用 bash_kill 停得掉", async () => {
    fresh();
    const { pi, jobs } = await load({ python: "python3", background: "/bg.py" });

    const call = pi.call("bash", { command: "sleep 30" });
    await new Promise((done) => setTimeout(done, 300));
    owe("m1");
    const job = /任务 (job-[a-z0-9-]+)/.exec((await call).content[0].text)?.[1] as string;

    await pi.call("bash_kill", { id: job });
    await new Promise((done) => setTimeout(done, 500));
    assert.ok(fs.existsSync(path.join(jobs, job, "exit")), "the command is still running");
  });

  it("没有人说话，命令照常跑完再返回", async () => {
    fresh();
    const { pi } = await load({ python: "python3", background: "/bg.py" });

    const answer = await pi.call("bash", { command: "sleep 1; echo DONE" });
    assert.match(answer.content[0].text, /DONE/);
    assert.doesNotMatch(answer.content[0].text, /转到后台/);
  });
});
