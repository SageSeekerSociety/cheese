/** Create tasks through the API, then verify that people can find and open each
 * task in the channel overview and project board. */
import { spawn } from "node:child_process";
import { once } from "node:events";
import { closeSync, openSync } from "node:fs";
import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { api, apiToken, apiLogin, openFirstProject, projectIdOf } from "./helpers";


/** 一间空房间。每条用例一间，互不干扰。 */
async function freshRoom(page: Page, title: string) {
  const project_id = await projectIdOf(page);
  const room = (await api(page, "post", "/topics", { project_id, title })) as {
    id: string;
  };
  return room.id;
}

/** 一个任务：alice 建的，她负责，并且已经开始（审阅人也是她）。 */
async function dispatch(page: Page, roomId: string, title: string) {
  const task = (await api(page, "post", `/topics/${roomId}/tasks`, {
    title,
  })) as { id: string };
  return (await api(page, "post", `/topics/${task.id}/start`, {
    reviewer_handle: "alice",
  })) as { id: string; number: number; branch_name?: string };
}

test.describe("房间里的任务", () => {
  test.beforeEach(async ({ page }) => {
    await apiLogin(page);
    await openFirstProject(page);
  });

  test("任务出现在房间总览里，点一下就进它自己的页面", async ({ page }) => {
    const projectId = await projectIdOf(page);
    const stamp = Date.now();
    const roomId = await freshRoom(page, `派活 ${stamp}`);
    const first = await dispatch(page, roomId, `第一件事 ${stamp}`);
    await dispatch(page, roomId, `第二件事 ${stamp}`);

    await page.goto(`/projects/${projectId}/topics/${roomId}?tab=overview`);
    const progress = page.getByTestId("channel-overview");
    await expect(progress).toBeVisible();

    // 两条都在「进行中」里，每条带一个状态圆点。不断言是哪个状态：这一条钉的是
    // 「有没有」，具体哪个状态由 lib/board.spec.ts 逐条钉。
    await expect(progress.getByTestId("channel-task")).toHaveCount(2);
    await expect(progress.getByText(`第一件事 ${stamp}`)).toBeVisible();
    await expect(progress.getByText(`第二件事 ${stamp}`)).toBeVisible();

    // 点条目就去这个任务的页面 —— 总览里的一行必须是个入口，不然它只是一张表。
    await progress.getByText(`第一件事 ${stamp}`).click();
    await expect(page).toHaveURL(new RegExp(`/tasks/${first.number}(\\?|$)`));
    // 进来的是这个任务：页头写着它的标题。
    await expect(page.locator(".task-header__title")).toHaveText(
      `第一件事 ${stamp}`,
    );
  });

  test("侧栏上的项目名就是回看板的入口，进去是整个项目的视角", async ({
    page,
  }) => {
    const stamp = Date.now();
    const roomId = await freshRoom(page, `跨房间 ${stamp}`);
    await dispatch(page, roomId, `跨房间的活 ${stamp}`);

    // 从侧栏那个常驻入口进去，而不是直接敲地址：这一条要钉的一半正是「找得到」。
    // 看板就是项目首页，所以侧栏上点项目名就到，不再单占一行。
    await page.locator(".rail-header__home").click();
    // 路由名和路径仍是 running：改地址会打断所有已经发出去的链接，改的只是这块
    // 界面叫什么。
    await expect(page).toHaveURL(/\/projects\/[^/]+\/running/);

    const view = page.locator(".board");
    // 这一页只有一个标题，写在和侧栏对齐的那条页头上：它说这一页是看板，项目名在
    // 侧栏顶上。板里不再另起标题，列头自己已经说明了它是什么。
    await expect(page.locator(".app-page__title")).toHaveText("看板");
    await expect(view.locator("h1, h2")).toHaveCount(0);
    // 板是按列排的，列本身要在 —— 这一页从一张平表变成看板，列就是那个变化。
    // 最右边那一列是「做出了什么」：三列任务从左到右是一条流水线，产物接在后面。
    await expect(view.locator(".board-col")).not.toHaveCount(0);
    await expect(view.locator(".board-col--made")).toContainText("做出了什么");
    // 板要答的是「该谁动」，所以它得说出各列各有几件；一件都没有的时候要明说，
    // 否则一块空板读起来就是「这个项目没活」——而项目里可能有几百条。
    await expect(view).toContainText(/未开始|进行中|检查中|待处理|暂无任务/);
  });

  test("板上计数在活到货之前不写 0", async ({ page }) => {
    const projectId = await projectIdOf(page);
    const stamp = Date.now();
    const roomId = await freshRoom(page, `计数 ${stamp}`);
    await dispatch(page, roomId, `计数的活 ${stamp}`);

    // 把这一页读活的那次请求按住，等断言完再放行：冷加载那一秒正是这一条要看的
    // 窗口，而按住了才不靠时序去赌。列表一格都没有的时候列头写 0，一秒后再跳到真
    // 值 —— 那个 0 会被读成「我的活没了」。
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    await page.route(`**/projects/${projectId}/tasks`, async (route) => {
      await gate;
      await route.continue();
    });

    try {
      await page.goto(`/projects/${projectId}/running`);
      const view = page.locator(".board");
      await expect(view).toBeVisible();
      // 四条任务列的计数槽都已经就位，而这一帧任务还在路上（列里是骨架）。
      const counts = page.locator(".board-col[data-column] .board-col__count");
      await expect(counts).toHaveCount(4);
      await expect(page.locator(".board-col__skel").first()).toBeVisible();
      for (const slot of await counts.all()) {
        await expect(slot).not.toHaveText(/\d/);
      }
    } finally {
      release();
    }

    // 放行之后，真实的数才出现 —— 出现了就说明上面那一帧确实还没有数。
    await expect(
      page.locator('[data-column="building"] .board-col__count'),
    ).toHaveText(/\d/);
    await expect(page.locator(".board-col__skel")).toHaveCount(0);
  });
});

test("同名文件按任务打开，换到另一件任务再回来草稿仍在", async ({
  page,
}, testInfo) => {
  // This case also clones and pushes two worktrees before exercising the UI.
  test.setTimeout(120_000);
  await apiLogin(page);
  await openFirstProject(page);
  const project = await projectIdOf(page);
  const room = await freshRoom(page, `文件来源 ${Date.now()}`);
  const first = await dispatch(page, room, "调整登录样式");
  const second = await dispatch(page, room, "修复登录校验");
  // The fixture commits through the native CLI and serves file requests through
  // an enrolled device, leaving the browser and backend paths unmodified.
  const log = openSync(testInfo.outputPath("machine.log"), "w");
  const machine = spawn(
    "uv",
    ["run", "python", "-m", "scripts.e2e_task_machine"],
    {
      cwd: "../backend",
      stdio: ["pipe", "pipe", log],
    },
  );
  closeSync(log);
  const exited = once(machine, "exit");
  try {
    const ready = new Promise<void>((resolve, reject) => {
      machine.once("error", reject);
      machine.once("exit", (code) =>
        reject(new Error(`Task machine exited: ${code}; see machine.log`)),
      );
      machine.stdout.on("data", (chunk) => {
        if (chunk.toString().includes("ready\n")) resolve();
      });
    });
    machine.stdin.end(
      JSON.stringify({
        api: `http://127.0.0.1:${process.env.E2E_BACKEND_PORT ?? "8081"}`,
        token: await apiToken(page),
        project,
        room,
        home: testInfo.outputPath("machine"),
        tasks: [
          { id: first.id, content: "first task\n" },
          { id: second.id, content: "second task\n" },
        ],
      }),
    );
    await ready;
    await page.setViewportSize({ width: 1440, height: 1000 });
    const taskPage = (task: { id: string }) =>
      `/projects/${project}/topics/${room}/tasks/${task.id}?tab=changes`;
    await page.goto(taskPage(first));
    const panel = page.locator(".panel-changes");
    // 草稿记在这一次打开的页面里：换任务走侧栏，不重新载入页面。
    const switchTo = async (title: string) => {
      await page.locator(".rail-task", { hasText: title }).click();
      await page.getByRole("tab", { name: /改动/ }).click();
    };
    // 「改动」只看这一件任务：清单里是它改过的文件，开着的是第一份。
    await expect(panel.locator(".changes-bar__path")).toHaveText(
      "src/login.txt",
    );
    await panel.getByRole("button", { name: "编辑", exact: true }).click();
    await panel.locator(".monaco-editor .view-lines").click();
    await page.keyboard.press("ControlOrMeta+A");
    await page.keyboard.type("my unsaved draft");
    await expect(
      panel.getByRole("button", { name: "保存", exact: true }),
    ).toBeEnabled();
    await switchTo("修复登录校验");
    await expect(panel).toContainText("second task");
    await switchTo("调整登录样式");
    await expect(panel.locator(".monaco-editor")).toContainText(
      "my unsaved draft",
    );
    await expect(
      panel.getByRole("button", { name: "保存", exact: true }),
    ).toBeEnabled();
    await page.emulateMedia({ colorScheme: "dark" });
    // The evidence is this editor panel; a full-page capture can stall in Chromium.
    await panel.screenshot({
      path: testInfo.outputPath("task-files-draft-dark.png"),
    });
    await panel.getByRole("button", { name: "保存", exact: true }).click();
    await expect(
      panel.getByRole("button", { name: "保存", exact: true }),
    ).toBeDisabled();
    const saved = await api(
      page,
      "get",
      `/projects/${project}/file?path=src/login.txt&topic=${room}&task=${first.id}`,
    );
    expect(saved.content).toBe("my unsaved draft");
    const untouched = await api(
      page,
      "get",
      `/projects/${project}/file?path=src/login.txt&topic=${room}&task=${second.id}`,
    );
    expect(untouched.content).toBe("second task\n");
  } finally {
    if (machine.exitCode === null) machine.kill();
    await exited;
  }
});
