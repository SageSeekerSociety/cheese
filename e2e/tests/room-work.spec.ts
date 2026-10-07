/** Create tasks through the API, then verify that people can find and open each
 * task in the channel overview and the project overview. */
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

  test("侧栏项目名下的「总览」打开项目总览，别的频道里的任务也在上面", async ({
    page,
  }) => {
    const projectId = await projectIdOf(page);
    const stamp = Date.now();
    const roomId = await freshRoom(page, `跨房间 ${stamp}`);
    await dispatch(page, roomId, `跨房间的活 ${stamp}`);

    // 从一个频道里出发，点侧栏那个常驻入口，而不是直接敲地址：这一条要钉的一半
    // 正是「找得到」。项目名下那一行的第一格就是总览。
    await page.goto(`/projects/${projectId}/topics/${roomId}`);
    await page
      .locator('[aria-label="项目页面"]')
      .getByText("总览", { exact: true })
      .click();
    await expect(page).toHaveURL(/\/projects\/[^/]+\/overview/);

    // 总览答的是「这个项目怎么样了」：四块都在。
    for (const section of [
      "overview-document",
      "overview-progress",
      "overview-people",
      "overview-made",
    ]) {
      await expect(page.getByTestId(section)).toBeVisible();
    }
    // 它是整个项目的视角：刚在另一个频道里开始的任务出现在「最近进展」里。
    await expect(page.getByTestId("overview-progress")).toContainText(
      `跨房间的活 ${stamp}`,
    );
  });

  test("全部任务上的计数在任务到货之前不写 0", async ({ page }) => {
    const projectId = await projectIdOf(page);
    const stamp = Date.now();
    const roomId = await freshRoom(page, `计数 ${stamp}`);
    await dispatch(page, roomId, `计数的活 ${stamp}`);

    // 把读任务的那次请求按住，等断言完再放行：冷加载那一秒正是这一条要看的窗口，
    // 按住了才不靠时序去赌。那一秒里写出来的 0 会被读成「我的活没了」。
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    // 只按住接口那一次：页面自己的地址也是 /projects/{id}/tasks，按住它页面就打不开。
    await page.route(`**/api/projects/${projectId}/tasks`, async (route) => {
      await gate;
      await route.continue();
    });

    const chips = page.locator(".tasks__chips button");
    try {
      await page.goto(`/projects/${projectId}/tasks`);
      await expect(chips).not.toHaveCount(0);
      for (const chip of await chips.all()) {
        await expect(chip).not.toHaveText(/\d/);
      }
    } finally {
      release();
    }

    // 放行之后，真实的数才出现：出现了就说明上面那一帧确实还没有数。
    await expect(chips.first()).toHaveText(/\d/);
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
