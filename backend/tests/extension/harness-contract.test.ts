// The TypeScript half of the harness contract.
//
// backend/tests/fixtures/harness-contract/ holds one scenario per file and
// backend/tests/contract/test_harness_contract.py reads the same directory over
// in Python. The backend and this extension never import each other — one is
// Python on the platform, the other TypeScript on a session machine — so the
// files are the only thing that can hold them to the same rule. Neither side
// can be made green by editing the other, which is the whole point.
//
// Two jobs here. The first is to read the WHOLE set, every scenario, and hold
// it to vocabulary.json: a scenario that steps outside the closed word list is
// red on this side whether or not it says anything about pi. The second is to
// drive the extension for the scenarios that name it — which, today, is the
// rule that a call that failed is not a call that returned (#1096), the thing
// this side says with `isError` and the backend says with PostToolUseFailure.
//
// Run with `node --test` and nothing else: no bundler, no node_modules, and no
// JSON schema library. A session machine runs this extension with no toolchain
// at all, and a test lane that needed one would be the first place that
// stopped being true.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { after, describe, it } from "node:test";

import { NOTICE, cleanup, load, runner } from "./pi-double.ts";

after(cleanup);

const FIXTURE_DIR = path.join(
  path.dirname(new URL(import.meta.url).pathname),
  "../fixtures/harness-contract",
);

function read(name: string): any {
  return JSON.parse(fs.readFileSync(path.join(FIXTURE_DIR, name), "utf8"));
}

const VOCABULARY = read("vocabulary.json");
const NAMES = fs
  .readdirSync(FIXTURE_DIR)
  .filter((name) => name.endsWith(".json") && name !== "vocabulary.json")
  .sort();
const SCENARIOS = NAMES.map((name) => ({ name, ...read(name) }));

// QUIET_LIMIT in platform.ts. Ten tool calls with nothing published is when the
// extension asks the agent to say something.
const QUIET = 10;

describe("夹具本身", () => {
  it("目录里有东西可读", () => {
    // An empty directory passes every loop below it. This is the one assertion
    // that says the two languages are reading the same batch rather than one of
    // them reading nothing.
    assert.ok(SCENARIOS.length >= 6, `only ${SCENARIOS.length} scenarios found`);
  });

  for (const scenario of SCENARIOS) {
    it(`${scenario.name} 只用词汇表里的词`, () => {
      assert.ok(scenario.why?.trim(), "a scenario has to say why it is contract");
      assert.ok(scenario.readers?.length, "a scenario nobody reads checks nothing");
      const drives = "drive" in scenario;
      const translates = "harnesses" in scenario;
      assert.ok(drives !== translates, "a scenario is one shape or the other");
      if (drives) {
        assert.ok(
          VOCABULARY.verbs.includes(scenario.verb) || scenario.xfail,
          `${scenario.verb} is not one of the six verbs`,
        );
        return;
      }
      for (const [harness, cell] of Object.entries<any>(scenario.harnesses)) {
        const keys = Object.keys(cell);
        assert.deepEqual(keys.length, 1, `${harness} answers twice`);
        if (keys[0] === "difference") {
          assert.ok(
            cell.difference in VOCABULARY.differences,
            `${cell.difference} is not a difference code`,
          );
        } else {
          assert.equal(keys[0], "records", `${harness}: ${keys[0]}`);
        }
      }
      for (const event of scenario.events) {
        const word = VOCABULARY.events[event.kind];
        assert.ok(word, `${event.kind} is not a word the room has`);
        for (const field of word.required) {
          assert.ok(field in event, `${event.kind} is missing ${field}`);
        }
      }
    });
  }

  it("点名 extension 的场景，都真被这里读过", () => {
    // A reader that quietly skips what it cannot handle is a reader that stops
    // covering the set the day somebody adds to it.
    const named = SCENARIOS.filter((s) => s.readers.includes("extension"));
    assert.ok(named.length >= 2, "nothing here is held to the fixtures");
    for (const scenario of named) {
      assert.ok(scenario.extension, `${scenario.name} names us and says nothing`);
      assert.equal(typeof scenario.extension.status, "number");
      assert.equal(typeof scenario.extension.is_error, "boolean");
      assert.equal(typeof scenario.extension.resets_silence, "boolean");
    }
  });
});

describe("失败的调用和返回的调用不是同一件事", () => {
  for (const scenario of SCENARIOS.filter((s) => s.readers.includes("extension"))) {
    const spec = scenario.extension;

    it(`${scenario.name}：退出码 ${spec.status} 的平台工具 isError=${spec.is_error}`, async () => {
      const socket = await runner(() => ({
        result: { status: spec.status, stdout: "", stderr: "说不出话" },
      }));
      // Closed even when the assertion fails: a listening server keeps the
      // event loop alive, so a red test would hang the lane instead of
      // reporting itself.
      try {
        const { pi } = await load({ socket: socket.address });
        const answer = await pi.call(spec.tool, { content: "第一版好了" }, {});
        assert.equal(answer.isError ?? false, spec.is_error);
      } finally {
        socket.close();
      }
    });

    it(`${scenario.name}：这样一次 ${spec.tool} ${spec.resets_silence ? "算" : "不算"}对房间说过话`, async () => {
      // The room sees no tool calls, only what was published. So whether this
      // call counts as having spoken is the same question as whether it
      // failed — and the two must be answered the same way.
      const { pi } = await load();
      await pi.emit("turn_end", {
        toolResults: Array.from({ length: QUIET }, () => ({
          toolName: "bash",
          isError: false,
        })),
      });
      await pi.emit("turn_end", {
        toolResults: [{ toolName: spec.tool, isError: spec.is_error }],
      });

      const answer = await pi.emit("context", {
        messages: [{ role: "user", content: [] }],
      });
      const said = answer ? answer.messages.at(-1).content[0].text : null;
      if (spec.resets_silence) {
        assert.equal(said, null, "the room has just been told what is going on");
      } else {
        assert.ok(said?.startsWith(NOTICE), "a message nobody received is not a message");
      }
    });
  }
});
