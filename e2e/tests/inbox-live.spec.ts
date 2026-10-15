import { expect, test, type Page } from "@playwright/test";

import { DEMO_PASSWORD, acceptPendingConsents, api, apiLogin } from "./helpers";

// 开着待办页的时候来了新动态：不用刷新，顶上就说「有 1 条新动态」，点了才换成最新
// 的。走的是真实的那条连接（`WS /notifications/inbox`），经前端代理到后端——单测
// 拨的是假开关，只有这里证明浏览器真的连上了、服务端真的说了。
//
// 新的动态由另一个人造出来：henry（别的 spec 都不用他）申请加入 alice 建的、要审批
// 的团队，alice 的「需我处理」里就多一条加入申请。打开页面之前先申请一个，列表就
// 不是空的——空列表来了新的是直接摆上来，不提示。

async function teamAskingApproval(page: Page, name: string): Promise<string> {
  const created = await api(page, "post", "/teams", {
    name,
    description: "A team someone asks to join while its owner watches the feed.",
  });
  const teamId = (created.team as { id: number }).id;
  const link = await api(page, "patch", `/teams/${teamId}/join-link`, {
    approval: true,
  });
  return link.token as string;
}

async function henryAsksToJoin(page: Page, joinToken: string): Promise<void> {
  const signedIn = await page.request.post("/api/users/auth/login", {
    data: { username: "henry", password: DEMO_PASSWORD },
  });
  expect(signedIn.ok()).toBe(true);
  const token = (await signedIn.json()).data.accessToken as string;
  await acceptPendingConsents(page, token);
  const asked = await page.request.post(`/api/team-invites/${joinToken}/join`, {
    headers: { Authorization: `Bearer ${token}` },
    data: {},
  });
  expect(asked.ok()).toBe(true);
}

test("a new notification shows up while the feed is open", async ({ page }) => {
  await apiLogin(page);
  const run = `${Date.now()}-${test.info().repeatEachIndex}`;
  const earlyName = `早到的 ${run}`;
  const lateName = `晚到的 ${run}`;
  await henryAsksToJoin(page, await teamAskingApproval(page, earlyName));
  const later = await teamAskingApproval(page, lateName);

  // 先等这一页的那条连接连上、听到第一帧「现在到几号了」：在那之前到的一行它不会说。
  const connected = page
    .waitForEvent("websocket", (ws) => ws.url().endsWith("/notifications/inbox"))
    .then((ws) =>
      ws.waitForEvent("framereceived", (frame) =>
        String(frame.payload).includes('"inbox"'),
      ),
    );
  await page.goto("/inbox");
  const feed = page.locator(".notification-feed");
  await expect(feed.getByText(earlyName)).toBeVisible();
  await connected;

  await henryAsksToJoin(page, later);

  // 几条由同时跑的别的用例也可能凑进来，所以不钉数字。
  const fresh = feed.getByRole("button", { name: /条新动态/ });
  await expect(fresh).toBeVisible({ timeout: 10_000 });
  // 正在读的那一行没被挤走，新的也还没塞进来。
  await expect(feed.getByText(lateName)).toHaveCount(0);

  await fresh.click();
  await expect(fresh).toBeHidden();
  await expect(feed.getByText(lateName)).toBeVisible();
});

// 一个浏览器只开一条：同一个人开着两页待办，服务端那边只多一条连接，两页都听得到。
test("two tabs share one connection and both hear the news", async ({
  page,
  context,
}) => {
  await apiLogin(page);
  const run = `${Date.now()}-${test.info().repeatEachIndex}`;
  const earlyName = `两页之前 ${run}`;
  const lateName = `两页之后 ${run}`;
  await henryAsksToJoin(page, await teamAskingApproval(page, earlyName));
  const later = await teamAskingApproval(page, lateName);

  // 两页各自开出来的 inbox 连接，连同它们第一帧到没到。
  const sockets: Promise<unknown>[] = [];
  const watch = (tab: Page) =>
    tab.on("websocket", (ws) => {
      if (ws.url().endsWith("/notifications/inbox"))
        sockets.push(
          ws.waitForEvent("framereceived", (frame) =>
            String(frame.payload).includes('"inbox"'),
          ),
        );
    });
  watch(page);
  await page.goto("/inbox");
  const second = await context.newPage();
  watch(second);
  await second.goto("/inbox");
  for (const tab of [page, second])
    await expect(tab.locator(".notification-feed").getByText(earlyName)).toBeVisible();
  await expect.poll(() => sockets.length).toBe(1);
  // 连上以后才算数：等持着连接的那一页读到第一帧，再让新的一条来。
  await sockets[0];

  await henryAsksToJoin(page, later);

  for (const tab of [page, second])
    await expect(
      tab.locator(".notification-feed").getByRole("button", { name: /条新动态/ }),
    ).toBeVisible({ timeout: 10_000 });
  expect(sockets).toHaveLength(1);
});
