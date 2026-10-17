import { expect, test, type Page } from "@playwright/test";

import { DEMO_PASSWORD, acceptPendingConsents, api, apiLogin } from "./helpers";

// 开着待办页的时候来了新动态：不用刷新，顶上就说「有 1 条新动态」，点了才换成最新
// 的。走的是真实的那条连接（`WS /notifications/inbox`），经前端代理到后端——单测
// 拨的是假开关，只有这里证明浏览器真的连上了、服务端真的说了。
//
// 新的动态由另一个人造出来：alice 邀请 henry（别的 spec 都不用他）进她建的团队，henry
// 一接受，alice 的动态里就多一条「接受了你的邀请」，正文带着团队名。打开页面之前先接受
// 一个，列表就不是空的——空列表来了新的是直接摆上来，不提示。

async function henrySignsIn(page: Page): Promise<{ token: string; id: number }> {
  const signedIn = await page.request.post("/api/users/auth/login", {
    data: { username: "henry", password: DEMO_PASSWORD },
  });
  expect(signedIn.ok()).toBe(true);
  const data = (await signedIn.json()).data;
  const token = data.accessToken as string;
  await acceptPendingConsents(page, token);
  return { token, id: data.user.id as number };
}

/** alice 建一个团队、邀请 henry 进来；返回这条邀请的号。 */
async function teamInvitingHenry(page: Page, name: string): Promise<number> {
  const created = await api(page, "post", "/teams", {
    name,
    description: "A team whose owner watches the feed while someone joins it.",
  });
  const teamId = (created.team as { id: number }).id;
  const henry = await henrySignsIn(page);
  const sent = await api(page, "post", `/teams/${teamId}/invitations`, {
    userId: henry.id,
    role: "MEMBER",
  });
  return (sent.invitation as { id: number }).id;
}

async function henryAccepts(page: Page, invitationId: number): Promise<void> {
  const { token } = await henrySignsIn(page);
  const accepted = await page.request.post(
    `/api/users/me/team-invitations/${invitationId}/accept`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  expect(accepted.ok()).toBe(true);
}

test("a new notification shows up while the feed is open", async ({ page }) => {
  await apiLogin(page);
  const run = `${Date.now()}-${test.info().repeatEachIndex}`;
  const earlyName = `早到的 ${run}`;
  const lateName = `晚到的 ${run}`;
  await henryAccepts(page, await teamInvitingHenry(page, earlyName));
  const later = await teamInvitingHenry(page, lateName);

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

  await henryAccepts(page, later);

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
  await henryAccepts(page, await teamInvitingHenry(page, earlyName));
  const later = await teamInvitingHenry(page, lateName);

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

  await henryAccepts(page, later);

  for (const tab of [page, second])
    await expect(
      tab.locator(".notification-feed").getByRole("button", { name: /条新动态/ }),
    ).toBeVisible({ timeout: 10_000 });
  expect(sockets).toHaveLength(1);
});
