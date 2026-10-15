import { test, expect, type Page } from "@playwright/test";

import { api, apiLogin, projectIdOf } from "./helpers";

// 手机上的页面栈：每一屏一个头部；底栏三格的落点是根，根上没有
// ←；栈里的一层有 ←，退回去落在来处。头部是这一屏声明给顶栏的（useScreenHeader），
// 所以进、退、换屏幕宽度之后，顶栏写的永远是屏幕上这一屏。

const bar = (page: Page) => page.locator(".v-app-bar");
const backArrow = (page: Page) => bar(page).locator('[aria-label^="返回"]');
const tab = (page: Page, name: string) =>
  page.locator(".v-bottom-navigation").getByText(name, { exact: true }).click();

test("手机上走一遍页面栈：根上没有 ←，每一层的顶栏写的都是这一层", async ({
  page,
}) => {
  await apiLogin(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");

  // 工作区那一格的根：频道列表，顶栏是项目那一行。
  await expect(page.locator(".topic-row").first()).toBeVisible();
  await expect(backArrow(page)).toHaveCount(0);

  // 现开一个频道和它下面的一条支线。
  const stamp = Date.now();
  const channel = `页面栈 ${stamp}`;
  const topic = `组会前整理实验二的数据 ${stamp}`;
  const project_id = await projectIdOf(page);
  const room = (await api(page, "post", "/topics", {
    project_id,
    title: channel,
  })) as { id: string };
  const thread = (await api(page, "post", `/topics/${room.id}/threads`, {
    title: topic,
  })) as { id: string };
  await page.goto("/");

  // 进这个频道：顶栏换成这个频道，有 ←。
  await page.locator(".topic-row", { hasText: channel }).click();
  await expect(page).toHaveURL(/\/channels\/[^/]+$/);
  await expect(bar(page)).toContainText(channel);
  await expect(backArrow(page)).toHaveCount(1);

  // 再进它的那条支线（通知点进来也是这条路）：顶栏写这条支线说的事，「转为任务」也在
  // 顶栏上；页里不再有第二行标题。
  const threadPath = `${new URL(page.url()).pathname}/threads/${thread.id}`;
  await page.evaluate((to) => {
    const app = (
      document.querySelector("#app") as unknown as {
        __vue_app__: {
          config: {
            globalProperties: {
              $router: { push: (to: string) => Promise<unknown> };
            };
          };
        };
      }
    ).__vue_app__;
    return app.config.globalProperties.$router.push(to);
  }, threadPath);
  await expect(bar(page).getByRole("heading", { name: topic })).toBeVisible({
    timeout: 15_000,
  });
  await expect(bar(page)).not.toContainText(channel);
  await expect(bar(page).getByTestId("thread-to-task")).toBeVisible();
  await expect(
    page.locator("#main-content").getByRole("heading", { name: topic }),
  ).toHaveCount(0);
  await expect(backArrow(page)).toHaveCount(1);

  // ← 一层一层退回来：先是频道，再是频道列表（根，没有 ←）。
  await backArrow(page).click();
  await expect(page).toHaveURL(/\/channels\/[^/]+$/);
  await expect(bar(page)).toContainText(channel);
  await expect(bar(page)).not.toContainText(topic);
  await backArrow(page).click();
  await expect(page.locator(".topic-row").first()).toBeVisible();
  await expect(backArrow(page)).toHaveCount(0);

  // 底栏另外两格也是根：从别处点过来，根上也不给 ←。
  await tab(page, "待办");
  await expect(page).toHaveURL(/\/inbox$/);
  await expect(backArrow(page)).toHaveCount(0);
  await tab(page, "首页");
  await expect(page).toHaveURL(/\/home$/);
  await expect(backArrow(page)).toHaveCount(0);
});

test("频道页换屏幕宽度：桌面上头部在页面里，回到手机又在顶栏里", async ({
  page,
}) => {
  await apiLogin(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const channel = (await page.locator(".topic-row").first().innerText())
    .trim()
    .split("\n")[0];
  await page.locator(".topic-row").first().click();
  await expect(bar(page)).toContainText(channel);

  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(page.locator("#main-content .topic-header")).toContainText(
    channel,
  );

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(bar(page)).toContainText(channel);
  await expect(page.locator("#main-content .topic-header")).toHaveCount(0);
});
