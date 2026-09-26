import { test, expect } from "@playwright/test";

import { acceptPendingConsents, apiLogin, apiToken } from "./helpers";

// 空间新界面那一棵路由（`/spaces/:id/board`）在**真栈**上跑一遍。
//
// 这一条测的不是「组件长得对不对」，而是单元测试结构上碰不到的那一段：路由真的
// 能被地址栏打开、真接口真的答话、`space.admins` 真的能把人判成所有者。
// 数据全部通过 API 现造 —— 库是本次运行专用的临时库，不碰任何部署。
//
// 造数要走 API 而不是点界面，是因为「建空间 → 平台审核 → 建题 → 过审」在界面上是
// 四个页面、七八次点击；断言则**全部留在屏幕上**，那才是人真正看到的东西。
//
// 第五批把**详情、发题、整板看板**收进这棵路由之后，这一份也跟着长出三条：
// 在新外壳里点题进详情、材料与视频还在、发出去的题落到审核队列、整板看板只对管理员
// 开门。它们量的正是「老页面被包进新外壳」这件事本身 —— 老页面自己那些功能各自
// 有自己的测试，这里要看的是**在另一棵树里还成不成立**。

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
  // 空间名一并交出去：新外壳的页头把「回题目板」那颗按钮写成空间名，
  // 按名字点它才有意义（写「返回」两个字的话，别处也有一颗，分不清）。
  return { spaceId, name };
}

/** 发一道题。`options` 那三样是第五批验收要用的：一道带材料、带讲解视频、带提交
 *  表单的题，和靠默认值建出来的题不是同一样东西。 */
async function createTask(
  page: import("@playwright/test").Page,
  auth: Record<string, string>,
  spaceId: number,
  name: string,
  options: {
    videoUrl?: string;
    attachmentIds?: number[];
    submissionSchema?: { prompt: string; type: string }[];
  } = {},
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
      ...(options.videoUrl ? { videoUrl: options.videoUrl } : {}),
      ...(options.attachmentIds ? { attachmentIds: options.attachmentIds } : {}),
    },
  });
  if (!res.ok()) throw new Error(`POST /tasks → ${res.status()} ${await res.text()}`);
  const taskId = ((await res.json()).data.task as { id: number }).id;

  // 提交表单（要交什么）不走建题那条请求，它有自己的覆盖更新接口。
  if (options.submissionSchema) {
    const patched = await page.request.patch(`/api/tasks/${taskId}`, {
      headers: auth,
      data: { submissionSchema: options.submissionSchema },
    });
    if (!patched.ok())
      throw new Error(`PATCH /tasks/${taskId} submissionSchema → ${patched.status()} ${await patched.text()}`);
  }
  return taskId;
}

/** 上板。新题一律是待审的，没过审的题领不了也提交不了。 */
async function approveTask(page: import("@playwright/test").Page, auth: Record<string, string>, taskId: number) {
  const res = await page.request.patch(`/api/tasks/${taskId}`, {
    headers: auth,
    data: { approved: "APPROVED" },
  });
  if (!res.ok()) throw new Error(`PATCH /tasks/${taskId} → ${res.status()} ${await res.text()}`);
}

/** 题目材料：文件先经 `POST /attachments` 传上来拿到 id，建题时一次挂上。 */
async function uploadAttachment(
  page: import("@playwright/test").Page,
  auth: Record<string, string>,
  filename: string,
) {
  const res = await page.request.post("/api/attachments", {
    headers: auth,
    multipart: {
      type: "file",
      file: { name: filename, mimeType: "text/plain", buffer: Buffer.from("题目材料（E2E）。") },
    },
  });
  if (!res.ok()) throw new Error(`POST /attachments → ${res.status()} ${await res.text()}`);
  return ((await res.json()).data as { id: number }).id;
}

/**
 * 换成另一个人的身份，返回他的 token。
 *
 * **两个键都要换**。只换 `accessToken` 不够：角色是拿 `user` 里的 handle 去比
 * `space.admins` 的，留着上一个人的话页面会以那个人的身份打开，而应用自己那次
 * `/users/me` 回来之前就判完了 —— 用例因此时绿时红。先落到应用 origin，一次把两样
 * 写齐，再去目标地址。
 *
 * 欠着的协议也在这里同意掉：换成的人一进应用就会被「协议已更新」那张模态盖住，
 * 底下点不着（详见 helpers.ts 里 `acceptPendingConsents`）。
 */
async function switchTo(page: import("@playwright/test").Page, username: string): Promise<string> {
  const res = await page.request.post("/api/users/auth/login", {
    data: { username, password: "demo12345" },
  });
  if (!res.ok()) throw new Error(`${username} 登录失败 → ${res.status()}`);
  const session = (await res.json()).data as { accessToken: string; user: unknown };
  await acceptPendingConsents(page, session.accessToken);

  const asset = await page.goto("/favicon.ico");
  if (!asset?.ok()) throw new Error("拿不到应用的 origin");
  await page.evaluate(
    (s) => {
      localStorage.setItem("accessToken", s.accessToken);
      localStorage.setItem("user", JSON.stringify(s.user));
    },
    session,
  );
  return session.accessToken;
}

// 串行：这一份要**现建空间**再当场审过，两个用例并发跑同一套栈时，后一个的
// `POST /admin/spaces/{id}/review` 会拿到 404 —— 单独跑各自都绿。这不是本页的问题
// （同一个空间在同一台栈上来回建，本来就该排队），所以这里显式串起来，
// 不去和一个属于建空间那条路的现象缠斗。
//
// 时限放宽到 3 分钟：第一次进题目详情要让 vite **现编**那一大片依赖树
// （tiptap / prism / 聊天），冷启动时可以慢到几十秒（见 playwright.config.ts
// 里 timeout 那段注释），默认那 60 秒不够「冷编译一次 + 后面几步断言」。
test.describe.configure({ mode: "serial", timeout: 180_000 });

test.describe("空间新界面（真路由）", () => {
  test("所有者打开 /spaces/:id/board，看到真数据", async ({ page }) => {
    await apiLogin(page);
    const token = await apiToken(page);
    const auth = { Authorization: `Bearer ${token}` };

    const { spaceId } = await createReviewedSpace(page, auth);
    const approvedName = "已过审的题（E2E）";
    const pendingName = "还在等审的题（E2E）";
    const approvedId = await createTask(page, auth, spaceId, approvedName);
    await createTask(page, auth, spaceId, pendingName);

    // 过审 → 上板。这一步走的就是新界面「审核」页那颗「通过上板」按钮背后的接口。
    await approveTask(page, auth, approvedId);

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
    const { spaceId } = await createReviewedSpace(page, auth);

    // bobby 只是一个路过的人：不在管理员名单里，也不在这个空间里。
    const otherToken = await switchTo(page, "bobby");
    const join = await page.request.post("/api/spaces/join", {
      headers: { Authorization: `Bearer ${otherToken}` },
      data: { code: (await inviteCodeOf(page, auth, spaceId)) },
    });
    if (!join.ok()) throw new Error(`加入空间失败 → ${join.status()} ${await join.text()}`);

    // 手打地址进「审核」：被送回首页，而不是看到一张空表。
    await page.goto(`/spaces/${spaceId}/board/review`);
    await expect(page).toHaveURL(new RegExp(`/spaces/${spaceId}/board$`));
    // 普通成员连入口都没有。
    await expect(page.getByRole("link", { name: "审核", exact: true })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "数据看板", exact: true })).toHaveCount(0);

    // 但「出题目」是有的：这块板任何人都能出题（接口那道门已从管理员放开成成员），
    // 成员发出来的题进待审队列，由所有者或管理员审。少一颗按钮就等于这条要求没落地。
    // 落点是**新外壳**那一页，不是老树里的 `/tasks/publish`。
    await expect(page.getByRole("link", { name: "出题目" })).toBeVisible();
    await page.getByRole("link", { name: "出题目" }).click();
    await expect(page).toHaveURL(new RegExp(`/spaces/${spaceId}/board/publish$`));

    // 整板看板是同一道门槛 —— 手打地址同样被弹回首页，看不到里面的数。
    await page.goto(`/spaces/${spaceId}/board/analytics`);
    await expect(page).toHaveURL(new RegExp(`/spaces/${spaceId}/board$`));
    await expect(page.getByRole("heading", { name: "总览" })).toHaveCount(0);
  });

  test("点一道题在新外壳里打开详情，导航还在；老地址也还开着", async ({ page }) => {
    await apiLogin(page);
    const auth = { Authorization: `Bearer ${await apiToken(page)}` };
    const { spaceId, name: spaceName } = await createReviewedSpace(page, auth);
    const taskName = "收进新外壳的一道题（E2E）";
    const taskId = await createTask(page, auth, spaceId, taskName);
    await approveTask(page, auth, taskId);

    // 先把详情页走一遍，让 dev server 付掉**冷启动那一次编译**。
    //
    // 题目详情是这一整套 e2e 里唯一会拖出 tiptap / prism / 聊天那一大片依赖树的
    // 页面，而它只在这里被打开 —— 所以第一次编译它的总是这一条用例。冷启动时这一趟
    // 要几十秒，vite 还会在这次编译里顺手优化依赖并**整页刷新一次**（刷新那一刻
    // 懒加载路由还没提交，正在等它的那一下点击就丢了）。那是 dev server 优化器的
    // 行为，构建产物里没有优化器、真用户不会遇到；所以这里先走一遍把它付掉，再断言
    // 「点卡片进详情」这件事本身。
    await page.goto(`/spaces/${spaceId}/board/tasks/${taskId}`);
    await expect(page.locator(".task-header-title", { hasText: taskName })).toBeVisible({ timeout: 60_000 });

    await page.goto(`/spaces/${spaceId}/board`);
    await page.locator(".tcard__title", { hasText: taskName }).click();

    // 地址栏落在**新外壳那棵路由**上（第四批之前这里会跳到老地址）。
    await expect(page).toHaveURL(new RegExp(`/spaces/${spaceId}/board/tasks/${taskId}$`));
    // 外壳还在：导航与角色标都没被详情页顶掉。
    await expect(page.getByRole("link", { name: "空间", exact: true })).toBeVisible();
    await expect(page.getByText(/·\s*所有者/)).toBeVisible();
    // 页头那颗回题板的按钮，点它回得到题目板首页。
    await page.getByRole("link", { name: spaceName }).click();
    await expect(page).toHaveURL(new RegExp(`/spaces/${spaceId}/board$`));

    // 题目本身真的画出来了（老组件复用，不是一层空壳）。
    //
    // 下面这两次 `goto` 各自等 60s：题目详情在**两棵树里是两个路由组件**，vite 各
    // 编译各的，上面暖过的那一次只暖了新外壳那一棵，老树那一棵第一次进来照样要编译。
    // 编译完没画出来才算这条用例失败。
    await page.goto(`/spaces/${spaceId}/board/tasks/${taskId}`);
    await expect(page.locator(".task-header-title", { hasText: taskName })).toBeVisible({ timeout: 60_000 });

    // 老地址这一批一个字没动，照常在原处服务同一道题。
    await page.goto(`/spaces/${spaceId}/tasks/${taskId}`);
    await expect(page.locator(".task-header-title", { hasText: taskName })).toBeVisible({ timeout: 60_000 });
  });

  test("材料、视频、从领取到提交这条链，在新外壳里都还在", async ({ page }) => {
    await apiLogin(page);
    const auth = { Authorization: `Bearer ${await apiToken(page)}` };
    const { spaceId } = await createReviewedSpace(page, auth);

    const attachmentId = await uploadAttachment(page, auth, "题目材料.txt");
    const taskName = "带材料与视频的题（E2E）";
    const taskId = await createTask(page, auth, spaceId, taskName, {
      // B 站链接：详情页会把它嵌成播放器；别的域名存得下、放不出来。
      videoUrl: "https://www.bilibili.com/video/BV1GJ411x7h7",
      attachmentIds: [attachmentId],
      submissionSchema: [{ prompt: "作业说明", type: "TEXT" }],
    });
    await approveTask(page, auth, taskId);

    // bobby：不在管理员名单里，先加进这个空间才看得见这道题。
    const otherToken = await switchTo(page, "bobby");
    const join = await page.request.post("/api/spaces/join", {
      headers: { Authorization: `Bearer ${otherToken}` },
      data: { code: (await inviteCodeOf(page, auth, spaceId)) },
    });
    if (!join.ok()) throw new Error(`加入空间失败 → ${join.status()} ${await join.text()}`);

    await page.goto(`/spaces/${spaceId}/board/tasks/${taskId}`);

    // 清单谁都看得见（看不见清单就无从判断要不要领），下载按钮不给 —— 两件事。
    await expect(page.getByText("题目附件")).toBeVisible();
    await expect(page.getByTestId("task-attachment").filter({ hasText: "题目材料.txt" })).toBeVisible();
    await expect(page.getByText("领取这道题之后才能下载")).toBeVisible();
    // 讲解视频嵌成了播放器。
    await expect(page.locator('iframe[src*="player.bilibili.com"]')).toBeVisible();

    // 领取就在屏幕上点：填一个联系方式即可，够得着那颗按钮就说明这条链在新树里是通的。
    //
    // 等的是**这次请求自己回来**，不是对话框关上。`TaskDialogs.vue` 的
    // `handleSubmitVerify` 是发出 `submit-verify` 就收弹窗（乐观关闭），请求还在
    // 路上 —— 自助领取要顺手建一个项目（含代码仓库），本地实测 1.4s。只看弹窗关
    // 了就往下走，会在记录还没落库时去读领取名单，读到一个空名单。
    const joinResponse = page.waitForResponse(
      (response) =>
        response.url().includes(`/tasks/${taskId}/participations/user`) &&
        response.request().method() === "POST",
    );
    await page.locator(".join-btn").click();
    await page.getByLabel("邮箱").fill("bobby@example.com");
    await page.getByRole("button", { name: "确认参与" }).click();
    expect((await joinResponse).status()).toBe(200);

    // 自助领取落库时是「待批」，出题人过一下（这一步是管理动作，走接口）。
    await approveParticipant(page, auth, taskId);
    await page.reload();

    // 领了之后那一行给下载按钮，不再是那句提示。
    await expect(page.getByText("领取这道题之后才能下载")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "下载" })).toBeVisible();

    // 「提交」那一格领取之后才有；点它进的也是新外壳那一棵。
    await page.getByRole("tab", { name: "提交", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/spaces/${spaceId}/board/tasks/${taskId}/submit$`));
    await page.getByLabel("作业说明").fill("我的作业正文（E2E）");
    await page.getByRole("button", { name: "提交", exact: true }).click();

    // 交完之后那一跳也走新外壳：老页面在自己那棵树里跳「提交记录」，
    // 名字是同一个、树是另一棵 —— 这里正好量到接缝有没有接错。
    await expect(page).toHaveURL(new RegExp(`/spaces/${spaceId}/board/tasks/${taskId}/submissions$`));
    await expect(page.getByText("暂无提交记录")).toHaveCount(0);
  });

  test("在新外壳里发题，发出去的题落到审核队列", async ({ page }) => {
    await apiLogin(page);
    const auth = { Authorization: `Bearer ${await apiToken(page)}` };
    const { spaceId } = await createReviewedSpace(page, auth);

    await page.goto(`/spaces/${spaceId}/board`);
    await page.getByRole("link", { name: "出题目" }).click();
    await expect(page).toHaveURL(new RegExp(`/spaces/${spaceId}/board/publish$`));

    // 发题页三块都在：PDF 那条路、材料那张卡、以及给发布参数用的表单
    // （PDF 解析出的草稿也要落到下面这张表单里填参数，所以两条路共用它）。
    await expect(page.getByText("PDF 快速发布")).toBeVisible();
    await expect(page.getByText("附件（可选）")).toBeVisible();
    await expect(page.getByLabel("题目名称")).toBeVisible();

    const taskName = `从新外壳发出的题 ${Date.now().toString(36)}（E2E）`;
    await page.getByLabel("题目名称").fill(taskName);
    // 表单有三栏必填而初始为空：参与者类型、题目难度、所属分类。不选就点提交，
    // `TaskForm.vue` 的 `taskFormSchema` 在本地把这次提交挡下来 —— 一个请求都不
    // 发出去，屏幕上只多三个「必填」，所以这份测试得自己把它们选上。
    await page.getByRole("radio", { name: "个人" }).check();
    await page.getByRole("radio", { name: "初级" }).check();
    // 所属分类这枚下拉的 `label` 落成了占位文字，不是可访问名 —— 按文字找不到它
    // （这一步曾在 180s 里一直等 `getByLabel('所属分类')`，直到超时）。只能按角色找：
    // 这一页一共两枚 combobox，都在「分类标签」那张卡里，前一枚是分类、后一枚是话题。
    // 选项也不必挑：新建空间自带一个默认分类（`General`，即 `default_category_id`），
    // 菜单里就它一条。
    await page.getByRole("combobox").first().click();
    await page.getByRole("option").first().click();
    await page.locator(".tiptap-editor").click();
    await page.keyboard.type("正文（E2E）。");
    await page.getByRole("button", { name: "提交", exact: true }).click();

    // 发完不跳走，落到新外壳自己的「我的」（老树那一步是「我发布的」页）。
    await expect(page).toHaveURL(new RegExp(`/spaces/${spaceId}/board/mine$`));

    // 新题是待审的：管理员在审核队列里看得到它。
    await page.goto(`/spaces/${spaceId}/board/review`);
    await expect(page.locator(".queue__title", { hasText: taskName })).toBeVisible();
  });

  test("管理员从新外壳进整板看板，看到真数", async ({ page }) => {
    await apiLogin(page);
    const auth = { Authorization: `Bearer ${await apiToken(page)}` };
    const { spaceId } = await createReviewedSpace(page, auth);
    const taskId = await createTask(page, auth, spaceId, "看板要有的一道题（E2E）");
    await approveTask(page, auth, taskId);

    await page.goto(`/spaces/${spaceId}/board`);
    await page.getByRole("link", { name: "数据看板", exact: true }).click();

    // 第一格「总览」自己就挂在 /analytics 这个地址上（它的子路径是空串），
    // 所以地址栏不变，变的是屏幕上真有这一格的内容。
    await expect(page).toHaveURL(new RegExp(`/spaces/${spaceId}/board/analytics$`));
    await expect(page.getByRole("heading", { name: "总览" })).toBeVisible();
    // 外壳还在：看板是挂在新题目板里的，不是把老侧栏那套又搬回来。
    await expect(page.getByRole("link", { name: "数据看板", exact: true })).toBeVisible();
    // 真数据：这一板上确实有一道题。
    await expect(page.getByText("题目总数")).toBeVisible();
  });

  test("从空间列表点进一块板，落的是题目板；课的几屏还在", async ({ page }) => {
    await apiLogin(page);
    const auth = { Authorization: `Bearer ${await apiToken(page)}` };
    const { spaceId, name } = await createReviewedSpace(page, auth);

    await page.goto("/spaces");
    // 探索空间那张卡整张可点（`<v-card :to="spaceEntryRoute(space)">`）。同一块板在
    // 上面「我的申请」里也有一行写着同一个名字，所以按类名限定到卡片本身，
    // 不然 `getByText` 会同时命中两处、strict 模式直接判失败。
    await page.locator(".space-card", { hasText: name }).click();

    // 落点是题目板 —— 不是 `/spaces/{id}` 那条 redirect 到的老树。
    await expect(page).toHaveURL(new RegExp(`/spaces/${spaceId}/board$`));
    for (const label of ["空间", "我的", "公告"]) {
      await expect(page.getByRole("link", { name: label, exact: true })).toBeVisible();
    }
    // 老侧栏那几格一个都不在（「全部分类」是老树独有的一格）。
    await expect(page.getByText("全部分类")).toHaveCount(0);

    // 新建的板**都是课**（#1448），所以「课程」这格在。它是课那几屏唯一的入口 ——
    // 进板的落点改成题目板之后，少了这一格，课里的人就回不到教学单元/作业/小测/小组。
    await page.getByRole("link", { name: "课程", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/spaces/${spaceId}/course$`));
    // 建这块板的人就是所有者 = 这门课的管理员，所以他看到的是课程总览（成员看到
    // 「我的课程」，同一条路由两种人两种第一屏）。
    await expect(page.getByRole("heading", { name: "课程总览" })).toBeVisible();
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

/** 批准那个刚自助领取的人。走的是「参与者」那一页背后同一条接口。 */
async function approveParticipant(
  page: import("@playwright/test").Page,
  auth: Record<string, string>,
  taskId: number,
) {
  const list = await page.request.get(`/api/tasks/${taskId}/participants`, { headers: auth });
  if (!list.ok()) throw new Error(`GET participants → ${list.status()} ${await list.text()}`);
  const participants = ((await list.json()).data.participants ?? []) as {
    id: number;
    approved: string;
  }[];
  // 自助领取落库是 `approved=2`（待批），序列化出来是 "NONE"。
  const pending = participants.find((p) => p.approved === "NONE");
  if (!pending) throw new Error("没有待批的领取记录");
  const res = await page.request.patch(`/api/tasks/${taskId}/participants/${pending.id}`, {
    headers: auth,
    data: { approved: "APPROVED" },
  });
  if (!res.ok()) throw new Error(`PATCH participant → ${res.status()} ${await res.text()}`);
}
