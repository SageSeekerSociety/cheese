import type { Page } from "@playwright/test";

// Seeded by backend/alembic/versions/219831eb75a3_seed_demo_data.py — every demo
// user (alice, bobby, ...) shares this bcrypt-hashed password. alice additionally
// owns the demo project seeded by backend/scripts/seed_fusion_demo.py, so she's
// the one account guaranteed to land on a project with at least one topic.
export const DEMO_USERNAME = "alice";
export const DEMO_PASSWORD = "demo12345";

// Drives the real sign-in form (not a localStorage shortcut) so this doubles as
// the login flow's own e2e coverage. Leaves the page on the authenticated app
// shell (rail visible) before returning.
export async function login(
  page: Page,
  username = DEMO_USERNAME,
  password = DEMO_PASSWORD,
) {
  await page.goto("/account/signin");
  await page.getByLabel("用户名").fill(username);
  // exact: true — Vuetify's show/hide-password toggle button gets an
  // auto-generated aria-label of "密码 appended action" (see InputIcon.js),
  // which is a substring match for the bare label and trips Playwright's
  // strict mode (two elements match `getByLabel('密码')`).
  await page.getByLabel("密码", { exact: true }).fill(password);
  // The seeded accounts never accepted the terms, so the first sign-in of a run
  // is stopped by the re-consent dialog (#1486) that covers the whole app. Read
  // the app's own answer to "is anything pending" instead of racing the dialog.
  const pending = page.waitForResponse(
    (r) =>
      r.url().includes("/users/me/consents") && r.request().method() === "GET",
  );
  await page.getByRole("button", { name: "立即登录" }).click();
  await page
    .locator(".app-rail-item:not(.app-rail-item--add)")
    .first()
    .waitFor();
  const { data } = await (await pending).json();
  if (data.pending.length > 0) {
    const accepted = page.waitForResponse(
      (r) =>
        r.url().includes("/users/me/consents") &&
        r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "同意并继续" }).click();
    if (!(await accepted).ok()) throw new Error("接受协议失败");
    await page
      .getByRole("button", { name: "同意并继续" })
      .waitFor({ state: "hidden" });
  }
}

// The signed-in JWT the app itself uses (see frontend/src/api.ts — one token,
// stored as `accessToken`). Setup that would take a person many clicks — five
// dispatched threads, a sealed batch — goes through the API with this; the
// ASSERTION always stays on what the screen says, which is the only part a
// person actually gets.
export async function apiToken(page: Page): Promise<string> {
  const raw = await page.evaluate(() => localStorage.getItem("accessToken"));
  if (!raw) throw new Error("没有登录态：apiToken 必须在 login() 之后调用");
  return raw.replace(/^"|"$/g, "");
}

// `/api/...` through the frontend's own proxy, so this needs no second base URL
// and cannot drift from the port the app is really talking to.
export async function api(
  page: Page,
  method: "get" | "post" | "patch" | "put",
  path: string,
  body?: unknown,
): Promise<Record<string, unknown>> {
  const token = await apiToken(page);
  const res = await page.request[method](`/api${path}`, {
    headers: { Authorization: `Bearer ${token}` },
    ...(body === undefined ? {} : { data: body }),
  });
  if (!res.ok())
    throw new Error(
      `${method.toUpperCase()} ${path} → ${res.status()} ${await res.text()}`,
    );
  const payload = (await res.json()) as { data?: Record<string, unknown> };
  return payload.data ?? {};
}

// Opens the first project from the rail and waits for its topic sidebar to
// finish loading, returning the count of visible (non-archived) topic rows.
export async function openFirstProject(page: Page) {
  // Project tiles carry `--tile` (they render an avatar image); the bare
  // `.app-rail-item:not(--add)` also matches the 首页/cheese home icon, which
  // sits first in the rail — clicking it lands on /spaces, not a project.
  await page.locator(".app-rail-item--tile").first().click();
  await page.locator('[title="新建话题"]').waitFor();
  const rows = page.locator(".topic-row");
  // The + button renders before the topics do, so returning here would let a
  // caller count zero rows and then watch the seeded ones arrive — a
  // `toHaveCount(before + 1)` that passes without anything being created.
  // alice's project always has at least one topic (see the seed note above).
  await rows.first().waitFor();
  return rows;
}

// ---- 控制台噪声：哪些不是被测页面的问题 ----------------------------------
//
// 一登录就替项目磁贴取这几张图——**在任何一页上都会出现，包括完全不碰这一页的页**。
// 种子里只给默认头像写了文件，所以任何新部署的项目磁贴都会缺图，这是一个真问题，
// 记在话题文档的待办里。放行范围写死到这个形状，不写成「忽略所有 404」：页面自己
// 发出的请求挂掉时仍然要红。
const AVATAR_404 =
  /^\[console\] Failed to load resource: the server responded with a status of 404 \(Not Found\) @ https?:\/\/[^\s]+\/api\/avatars\/\d+$/;

const RESOURCE_FAILED = /^\[console\] Failed to load resource: .*? @ (\S+)$/;

/**
 * 这条控制台报错是不是环境的，不是这一页的。
 *
 * 外部源上的资源（字体、CDN）取不到，CI 机器网络抖一下就是一条
 * `net::ERR_NETWORK_CHANGED`——而任何一条用例想问的都不是「第三方 CDN 现在可不可
 * 用」。**按源判，不按错误码、也不按域名判**：换一个 CDN、换一种失败码，规则照样
 * 成立；而页面自己发的请求都是同源的，一条都放不进去。
 *
 * 写死域名的那种写法已经红过一次：放行名单上写着 jsdelivr，而那天挂的是
 * fonts.gstatic.com，于是一份和管理端毫无关系的字体没取到，整条用例跟着红。
 */
export function isEnvironmentNoise(entry: string, appOrigin: string): boolean {
  if (AVATAR_404.test(entry)) return true;
  const failed = RESOURCE_FAILED.exec(entry);
  if (!failed) return false;
  try {
    return new URL(failed[1]).origin !== appOrigin;
  } catch {
    // 取不到位置（控制台那句可能不带 URL，我们写成 `@ ?`）时不当外部源，照旧报出来。
    return false;
  }
}

/** 配置里那个 baseURL 的源——按定义就是被测应用自己的源，和当前停在哪一页无关。 */
export function appOriginOf(baseURL: string | undefined): string {
  return new URL(baseURL!).origin;
}
