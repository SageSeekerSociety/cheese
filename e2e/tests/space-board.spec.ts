import { test, expect } from "@playwright/test";

import { apiLogin, apiToken } from "./helpers";

// 空间新界面那一棵路由（`/spaces/:id/board`）在**真栈**上跑一遍。
//
// 这一条测的不是「组件长得对不对」，而是单元测试结构上碰不到的那一段：路由真的
// 能被地址栏打开、真接口真的答话、`space.admins` 真的能把人判成所有者。
// 数据全部通过 API 现造 —— 库是本次运行专用的临时库，不碰任何部署。
//
// 造数要走 API 而不是点界面，是因为「建空间 → 平台审核 → 建题 → 过审」在界面上是
// 四个页面、七八次点击；断言则**全部留在屏幕上**，那才是人真正看到的东西。

// 空间名在库里是唯一的，而两个用例可能在同一毫秒里各建一个 —— 带上随机尾巴。
const unique = () => `E2E 空间 ${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

async function createReviewedSpace(page: import("@playwright/test").Page, auth: Record<string, string>) {
  const name = unique();
  const created = await page.request.post("/api/spaces", {
    headers: auth,
    data: { name, intro: "用来跑空间新界面的临时空间。" },
  });
  if (!created.ok()) throw new Error(`POST /spaces → ${created.status()} ${await created.text()}`);
  const spaceId = ((await created.json()).data.space as { id: number }).id;

  // 新建的空间是 PENDING：子资源（题目、成员）在过审之前一律读不到。
  // alice 在 e2e 环境里是平台管理员（PLATFORM_ADMIN_HANDLES），所以这条审得过。
  const reviewed = await page.request.post(`/api/admin/spaces/${spaceId}/review`, {
    headers: auth,
    data: { approved: true, reason: "" },
  });
  if (!reviewed.ok()) throw new Error(`POST review → ${reviewed.status()} ${await reviewed.text()}`);
  return spaceId;
}

async function createTask(
  page: import("@playwright/test").Page,
  auth: Record<string, string>,
  spaceId: number,
  name: string,
) {
  const res = await page.request.post("/api/tasks", {
    headers: auth,
    data: {
      name,
      // 简介里**不要**再写一遍标题：卡片上标题和简介是两个元素，同一个串出现两次
      // 会让 `getByText` 撞上 strict 模式。
      intro: "一句话简介。",
      description: "正文。",
      submitterType: "USER",
      resubmittable: true,
      editable: true,
      space: spaceId,
      participantLimit: 0,
      defaultDeadline: 30,
      deadline: null,
    },
  });
  if (!res.ok()) throw new Error(`POST /tasks → ${res.status()} ${await res.text()}`);
  return ((await res.json()).data.task as { id: number }).id;
}

// 串行：这一份要**现建空间**再当场审过，两个用例并发跑同一套栈时，后一个的
// `POST /admin/spaces/{id}/review` 会拿到 404 —— 单独跑各自都绿。这不是本页的问题
// （同一个空间在同一台栈上来回建，本来就该排队），所以这里显式串起来，
// 不去和一个属于建空间那条路的现象缠斗。
test.describe.configure({ mode: "serial" });

test.describe("空间新界面（真路由）", () => {
  test("所有者打开 /spaces/:id/board，看到真数据", async ({ page }) => {
    await apiLogin(page);
    const token = await apiToken(page);
    const auth = { Authorization: `Bearer ${token}` };

    const spaceId = await createReviewedSpace(page, auth);
    const approvedName = "已过审的题（E2E）";
    const pendingName = "还在等审的题（E2E）";
    const approvedId = await createTask(page, auth, spaceId, approvedName);
    await createTask(page, auth, spaceId, pendingName);

    // 过审 → 上板。这一步走的就是新界面「审核」页那颗「通过上板」按钮背后的接口。
    const patched = await page.request.patch(`/api/tasks/${approvedId}`, {
      headers: auth,
      data: { approved: "APPROVED" },
    });
    if (!patched.ok()) throw new Error(`PATCH /tasks → ${patched.status()} ${await patched.text()}`);

    await page.goto(`/spaces/${spaceId}/board`);

    // 1. 外壳认得这个空间，也认得出「我是所有者」——导航里于是有管理员那三格。
    await expect(page.getByRole("heading", { name: /^E2E 空间/ })).toBeVisible();
    // `exact` 是必要的：首屏那条「有 N 道题在等你审」的提醒里也有一个「去审核」，
    // 不写 exact 会同时命中它，strict 模式直接判失败。
    for (const label of ["空间", "我的", "公告", "审核", "成员", "数据看板"]) {
      await expect(page.getByRole("link", { name: label, exact: true })).toBeVisible();
    }
    // 右上角那块角色标：这是首页唯一说明「你是谁」的地方。
    await expect(page.getByText(/·\s*所有者/)).toBeVisible();

    // 2. 首屏列的是**已上板**的题；待审的题不在这一份列表里。
    const cardTitles = page.locator(".tcard__title");
    await expect(cardTitles.filter({ hasText: approvedName })).toHaveCount(1);
    await expect(cardTitles.filter({ hasText: pendingName })).toHaveCount(0);

    // 3. 审核页里才看得到它，而且带「你自己出的」标记（自己出的题自己也能审）。
    await page.locator(".board__tab", { hasText: "审核" }).click();
    await expect(page.locator(".queue__title", { hasText: pendingName })).toBeVisible();
    await expect(page.getByText("你自己出的 · 可直接通过")).toBeVisible();
    // 4. 「我的」是出题人看自己题的地方 —— 两道题都该在（一道已上板、一道待审）。
    await page.locator(".board__tab", { hasText: "我的" }).click();
    await expect(page.getByRole("tab", { name: "我发布的（2）" })).toBeVisible();
    await expect(page.locator(".pub-row__title", { hasText: approvedName })).toBeVisible();
    await expect(page.locator(".pub-row__title", { hasText: pendingName })).toBeVisible();
  });

  test("打不开的空间给一句话，不是一张空表", async ({ page }) => {
    await apiLogin(page);
    // 不存在的空间：真接口答 404（不存在与没权限在它那儿是同一个回答）。
    await page.goto("/spaces/99999999/board");
    await expect(page.getByText("打不开这个空间")).toBeVisible();
  });

  test("普通成员打不到管理员那三页，直接输地址也不行", async ({ page }) => {
    await apiLogin(page);
    const token = await apiToken(page);
    const auth = { Authorization: `Bearer ${token}` };
    const spaceId = await createReviewedSpace(page, auth);

    // bobby 只是一个路过的人：不在管理员名单里，也不在这个空间里。
    const other = await page.request.post("/api/users/auth/login", {
      data: { username: "bobby", password: "demo12345" },
    });
    if (!other.ok()) throw new Error(`bobby 登录失败 → ${other.status()}`);
    const otherSession = (await other.json()).data as { accessToken: string; user: unknown };

    const join = await page.request.post("/api/spaces/join", {
      headers: { Authorization: `Bearer ${otherSession.accessToken}` },
      data: { code: (await inviteCodeOf(page, auth, spaceId)) },
    });
    if (!join.ok()) throw new Error(`加入空间失败 → ${join.status()} ${await join.text()}`);

    // **两个键都要换成 bobby 的**。只换 accessToken 不够：角色是拿 `user` 里的 handle
    // 去比 `space.admins` 的，留着 alice 的话这一页会以所有者的身份打开，而应用自己
    // 那次 `/users/me` 回来之前就判完了 —— 这条用例因此时绿时红。先落到应用 origin，
    // 一次把两样写齐，再去目标地址。
    const asset = await page.goto("/favicon.ico");
    if (!asset?.ok()) throw new Error("拿不到应用的 origin");
    await page.evaluate(
      ({ session }) => {
        localStorage.setItem("accessToken", session.accessToken);
        localStorage.setItem("user", JSON.stringify(session.user));
      },
      { session: otherSession },
    );

    // 手打地址进「审核」：被送回首页，而不是看到一张空表。
    await page.goto(`/spaces/${spaceId}/board/review`);
    await expect(page).toHaveURL(new RegExp(`/spaces/${spaceId}/board$`));
    // 普通成员连入口都没有。
    await expect(page.getByRole("link", { name: "审核", exact: true })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "数据看板", exact: true })).toHaveCount(0);
  });
});

/** 空间建好时就带着一个邀请码（`createSpace` 的返回里那条），用它把别人放进来。 */
async function inviteCodeOf(
  page: import("@playwright/test").Page,
  auth: Record<string, string>,
  spaceId: number,
) {
  const res = await page.request.get(`/api/spaces/${spaceId}/invite-codes`, { headers: auth });
  if (!res.ok()) throw new Error(`GET invite-codes → ${res.status()} ${await res.text()}`);
  const codes = ((await res.json()).data.inviteCodes ?? []) as { code: string }[];
  if (!codes.length) throw new Error("这个空间没有邀请码");
  return codes[0].code;
}
