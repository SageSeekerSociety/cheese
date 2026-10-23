/** Create tasks through the API, then verify that people can find and open each
 * task in the channel overview and the project overview. */
import { spawn } from "node:child_process";
import { once } from "node:events";
import { closeSync, openSync } from "node:fs";
import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import {
  api,
  apiToken,
  apiLogin,
  openFirstProject,
  projectIdOf,
} from "./helpers";

/** 一间空房间。每条用例一间，互不干扰。 */
async function freshRoom(page: Page, title: string) {
  const project_id = await projectIdOf(page);
  const room = (await api(page, "post", "/topics", { project_id, title })) as {
    id: string;
  };
  return room.id;
}

/** 一个任务：alice 建的，她负责。建出来就已经开始，审阅人因此也是她。 */
async function dispatch(page: Page, roomId: string, title: string) {
  return (await api(page, "post", `/topics/${roomId}/tasks`, {
    title,
  })) as { id: string; number: number; branch_name?: string };
}

test.describe("房间里的任务", () => {
  test.beforeEach(async ({ page }) => {
    await apiLogin(page);
    await openFirstProject(page);
  });

  test("任务出现在房间总览里，点一下在右半边打开，整页打开进它自己的页面", async ({
    page,
  }) => {
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

    // 总览里的一行必须是个入口，不然它只是一张表：任务和支线一样在频道页右半边
    // 打开，页头写着它的标题。
    await progress.getByText(`第一件事 ${stamp}`).click();
    await expect(page).toHaveURL(new RegExp(`/threads/${first.id}(\\?|$)`));
    const pane = page.getByTestId("thread-pane");
    await expect(pane.getByRole("heading", { level: 2 })).toHaveText(
      `第一件事 ${stamp}`,
    );
    // 「整页打开」进它自己的页面。
    await pane.getByTestId("thread-open-page").click();
    await expect(page).toHaveURL(new RegExp(`/tasks/${first.number}(\\?|$)`));
    await expect(page.locator(".task-header__title")).toHaveText(
      `第一件事 ${stamp}`,
    );
  });

  test("侧栏项目名下的那一行里打开项目总览，别的频道里的任务也在上面", async ({
    page,
  }) => {
    const projectId = await projectIdOf(page);
    const stamp = Date.now();
    const roomId = await freshRoom(page, `跨房间 ${stamp}`);
    await dispatch(page, roomId, `跨房间的活 ${stamp}`);

    // 从一个频道里出发，走侧栏找到它，而不是直接敲地址：这一条要钉的一半正是
    // 「找得到」。总览就摆在项目名下面那一行的第一格（2026-10-13 用户定，见
    // .claude/rules/project-sidebar.md），点它进去 —— 它不在项目名菜单里了。
    await page.goto(`/projects/${projectId}/topics/${roomId}`);
    await page
      .locator('[aria-label="项目页面"] .pinned-row')
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
    // 按路径认，不管查询串：页面读的是还在进行的那些（`?status=open`）。
    await page.route(
      (url) => url.pathname === `/api/projects/${projectId}/tasks`,
      async (route) => {
        await gate;
        await route.continue();
      },
    );

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
  const stamp = Date.now();
  const room = await freshRoom(page, `文件来源 ${stamp}`);
  const firstTitle = `调整登录样式 ${stamp}`;
  const secondTitle = `修复登录校验 ${stamp}`;
  const first = await dispatch(page, room, firstTitle);
  const second = await dispatch(page, room, secondTitle);
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
    const switchTo = async (task: { number: number }, title: string) => {
      await page.locator(".rail-task", { hasText: title }).click();
      // Resolving the task's short address is asynchronous; the old page's tabs
      // remain clickable until that navigation lands.
      await expect(page).toHaveURL(new RegExp(`/tasks/${task.number}(\\?|$)`));
      await expect(page.locator(".task-header__title")).toHaveText(title);
      await page.getByRole("tab", { name: /改动/ }).click();
    };
    // 「改动」只看这一件任务：全部改动里是它改过的文件，点那一段的「打开」单独看它。
    const openLogin = () =>
      panel
        .locator('.diff-file[data-path="src/login.txt"]')
        .getByRole("button", { name: "打开", exact: true })
        .click();
    // 全部改动里每一段的差异默认收着，点段头才铺开：那一段的字在差异正文里，
    // 断言之前先铺开它，不然面板上只有段头那一行的路径和增删数。
    const expandLogin = () =>
      panel
        .locator('.diff-file[data-path="src/login.txt"]')
        .locator(".diff-file__toggle")
        .click();
    await openLogin();
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
    await switchTo(second, secondTitle);
    // 换过来的这一面同样是那一件任务改过的文件，每一段收着。这里要看的是它的
    // 正文，所以先铺开这一段再断言。
    await expandLogin();
    await expect(panel).toContainText("second task");
    await switchTo(first, firstTitle);
    // 回来时又是全部改动那一面；打开那份文件，没保存的草稿还在，直接是编辑那一面。
    await openLogin();
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
