import { test, expect, type Page } from '@playwright/test';
import { api, login } from './helpers';

// 反馈的两条全流程，真的从界面走一遍：提交者提一条，管理员把它办完。
//
// 为什么要有这两条：反馈的单元测试和集成测试各自守着自己那一层，接不起来的地方
// 正好在缝里——前端把 `kind` 拼错、接口回的信封多一层、管理端的下拉送的是 label
// 而不是 value，这些在两边都绿的情况下依然能让用户点着点着撞墙。这条用例只做一件
// 事：像人一样点一遍，任何一步和预期不符就红。
//
// 管理端那条要求后端把 alice 放进管理员名单（`FEEDBACK_ADMIN_HANDLES`，见
// playwright.config.ts 里后端 webServer 的 env）。没有它，`/admin/feedback` 只会
// 回 403，用例红在「这一页是管理员后台」的闸门那一步，而不是红在要验的东西上。

// 每条用例的标题带时间戳，这样重跑时不会撞上上一轮留下的条目——列表按新到旧排，
// 同名两条会让「刚提的这条在不在」这种断言分不清是在说哪一条。
const uniqueTitle = (what: string) => `【e2e】${what} ${Date.now()}`;

// 60s 不够：vite 开发服务器按路由编译，这两条用例各自会踩到几条第一次进的路由
// （反馈中心、反馈详情、反馈管理），冷编译一条就能吃掉几十秒。配置里那个 60s 是给
// 「路由已经热了」的用例定的，这里翻成三倍，免得红在编译上而不是红在要验的东西上。
test.describe.configure({ timeout: 180_000 });

// 浏览器控制台里的话也算断言的一部分。
//
// 起因是一个真漏出来的 bug：给管理端抽屉加头像时只写了模板没写 import，Vue 只在
// 控制台打一句「Failed to resolve component: FeedbackAuthorAvatar」，页面上那个位置
// 就是空的——typecheck 不看模板、eslint 不看模板、单测没渲染过那个抽屉，三边全绿。
// 这类事只有真的把页面打开才看得见，所以就在这里看着。
const consoleNoise: string[] = [];

test.beforeEach(({ page }) => {
  consoleNoise.length = 0;
  page.on('console', (msg) => {
    if (msg.type() === 'error' || msg.text().includes('Failed to resolve component')) {
      // 带上资源地址：控制台那句「Failed to load resource」不带 URL，光看它认不出是
      // 哪一次请求挂了。
      consoleNoise.push(`[console] ${msg.text()} @ ${msg.location().url || '?'}`);
    }
  });
  page.on('pageerror', (err) => consoleNoise.push(`[pageerror] ${err.message}`));
});

// 唯一的例外，而且只放行这一个形状：`/api/avatars/{id}` 的 404。
//
// 这是环境的缺口，不是反馈这功能带来的：头像表里 2/3/4 号（猫咪/柴犬/熊猫，都是
// predefined）有行、`uploads/avatars/` 下却没有对应文件，于是取图就是 404。浏览器
// 一登录就替项目磁贴取这几张图——**在任何一页上都会出现，包括完全不碰反馈的页**，
// 这一条是实测过的（登录后停 3 秒，一个反馈页面都没进，三条 404 已经在控制台里）。
// 顺带说明：种子里只给默认头像写了文件，所以任何新部署的项目磁贴都会缺图，这是
// 反馈之外的一个真问题，记在话题文档的待办里，没有在这条用例里顺手改。
//
// 放行范围写死到这个形状，不写成「忽略所有 404」：反馈自己发出的请求挂掉时仍然要红。
const AVATAR_404 =
  /^\[console\] Failed to load resource: the server responded with a status of 404 \(Not Found\) @ https?:\/\/[^\s]+\/api\/avatars\/\d+$/;

// 第二条放行，管的是**外部源**上的资源。
//
// 字体（JetBrains Mono）是从 jsdelivr 取的（`src/styles/fonts.css`），CI 机器网络抖
// 一下就是一条 `net::ERR_NETWORK_CHANGED` 的控制台 error —— 而这条检查想问的从来
// 不是「第三方 CDN 现在可不可用」。它上次就是这么红的：一份和反馈毫无关系的字体没
// 取到，整条反馈用例跟着红。
//
// **按源判，不按错误码、也不按域名判**：换一个 CDN、换一种失败码，规则照样成立；
// 而页面自己发的请求都是同源的，一条都放不进去。
const RESOURCE_FAILED = /^\[console\] Failed to load resource: .*? @ (\S+)$/;

function isKnownEnvNoise(entry: string, appOrigin: string): boolean {
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

test.afterEach(() => {
  // 用配置里那个 baseURL 而不是当前页面的地址：它按定义就是被测应用自己的源，
  // 用例结束时停在哪个页面都不影响这条判断。
  const appOrigin = new URL(test.info().project.use.baseURL!).origin;
  const ours = consoleNoise.filter((entry) => !isKnownEnvNoise(entry, appOrigin));
  expect(ours, '浏览器控制台不该有报错，也不该有没注册的组件').toEqual([]);
});

// 用户侧支持按钮的可见文字会在点下去之后从「支持这个反馈」变成「已支持」，所以
// 断言不能靠文案，得看计数和 variant 渲染出来的那颗实心图标。
const supportButton = (page: Page) => page.locator('.fb-page button', { hasText: /支持这个反馈|已支持/ });

test('用户提一条反馈，能看见、能支持、能评论', async ({ page }) => {
  await login(page);
  const title = uniqueTitle('提交者这一条');
  const comment = `补充一句：${title}`;

  await page.goto('/feedback');
  await page.locator('.fb-page').waitFor();

  // 提一条。页头上那颗「提交反馈」和抽屉里那颗同名，而且抽屉即使关着也留在 DOM 里
  // （Vuetify 的 navigation-drawer 只是藏起来），所以两边都得限定范围，否则
  // getByRole 会因为两个同名按钮直接抛 strict mode 的错。抽屉是 .fb-page__inner
  // 的兄弟节点，按内容区限定就只剩页头那一颗。
  await page.locator('.fb-page__inner').getByRole('button', { name: '提交反馈' }).click();
  const drawer = page.locator('.fb-drawer');
  await drawer.waitFor();
  await drawer.getByLabel('标题').fill(title);
  await drawer.getByRole('button', { name: '提交反馈' }).click();

  // 提交成功后前端会跳详情页（FeedbackCenterPage 的 onSubmitted），所以这里等的是
  // 详情页的标题，而不是列表里多了一张卡。
  await expect(page.locator('.fb-title')).toHaveText(title);
  await expect(page).toHaveURL(/\/feedback\/[0-9a-f-]{36}$/);
  // 刚提的条目落在梯子的第一级，标签是「已收录」——不是「开放」「待处理」之类。
  // 断在药丸上而不是整页文字上：详情页的进展条会把四级的名字都写出来，`toContainText`
  // 分不清「状态是已收录」和「梯子上有个叫已收录的台阶」。
  await expect(page.locator('.fb-chip').first()).toHaveText('已收录');

  // 支持一次：计数从 0 变 1，按钮变成已支持。
  await supportButton(page).click();
  await expect(page.locator('.fb-support-count')).toHaveText('1');
  await expect(page.locator('.fb-page')).toContainText('已支持');

  // 评论一条，评论列表里读得回来。
  //
  // 底部的框收起时只有一行（它 sticky 在视口底部，常驻三行就是永久少掉一屏），
  // 先点开才有多行框 —— 这一步不是绕路，正是「点一下就能打字」要验的东西。
  await page.locator('.fb-composer__open').click();
  await page.getByPlaceholder('补充你遇到的情况，或者说明为什么这个改动对你重要').fill(comment);
  await page.getByRole('button', { name: '发表评论' }).click();
  await expect(page.locator('.fb-page')).toContainText(comment);
  // 发完收回去，占地方的那一屏还回去。
  await expect(page.locator('.fb-composer__open')).toBeVisible();

  // 点赞那条评论。**三个非颜色信号一起变**才是这条要验的：文案从「赞」到「已赞」、
  // 计数出现、图标实心。断言只读文案和计数 —— 图标那个 class 页面上看不见，而颜色
  // 在这一层根本验不了（琥珀还是中性灰只有人眼分得出来，规则在单测和设计规范里）。
  const top = page.locator('.fb-ci', { hasText: comment });
  await top.getByText('赞', { exact: true }).click();
  await expect(top.getByText('已赞')).toBeVisible();
  await expect(top.locator('.fb-ci__count')).toHaveText('1');

  // 回一条，再回那条回复：「回复 X」只出现在第二条上。
  //
  // 折楼之后 `parent_id` 只指顶层，「回的是谁」在客户端猜不出来，所以这一句能不能
  // 出现完全取决于服务端有没有把 `reply_to_handle` 存下来 —— 正是那种「两边各自都
  // 绿、接起来没有」的缝。回楼主的那些不写它（本来就紧挨着楼主渲染），所以第一条
  // 回复上必须**没有**这一行。
  const reply = `回复一句：${title}`;
  await top.getByText('回复', { exact: true }).click();
  // 回复框和动作行里都有「回复」两个字，所以两处都按容器限定，不然撞 strict mode。
  await page.locator('.fb-ci__form').getByPlaceholder('回复 alice').fill(reply);
  await page.locator('.fb-ci__form').getByRole('button', { name: '回复' }).click();
  const firstReply = page.locator('.fb-ci', { hasText: reply });
  await expect(firstReply).toBeVisible();
  await expect(firstReply.locator('.fb-ci__re')).toHaveCount(0);

  const second = `再回一句：${title}`;
  await firstReply.getByText('回复', { exact: true }).click();
  await page.locator('.fb-ci__form').getByPlaceholder('回复 alice').fill(second);
  await page.locator('.fb-ci__form').getByRole('button', { name: '回复' }).click();
  await expect(page.locator('.fb-ci', { hasText: second }).locator('.fb-ci__re')).toContainText('alice');

  // 一栋楼超过两条回复就折起来：整栋楼一次铺开，会把下面所有评论推到屏幕外。前置
  // 数据走接口（这条用例验的是界面），断言留在屏幕上。
  const feedbackId = /\/feedback\/([0-9a-f-]{36})/.exec(page.url())?.[1];
  expect(feedbackId, '详情页地址里应该有这条反馈的 id').toBeTruthy();
  // 这条接口现在是**按楼分页**的：回的是一页 `{ items, next_cursor }`，不是整条线程
  // 的裸数组。这里只要「刚发的那条顶层评论的 id」，读这一页的 `items` 就够 —— 这条
  // 用例总共只有一栋楼。
  const firstPage = (await api(page, 'get', `/feedback/${feedbackId}/comments`)) as unknown as {
    items: { id: string; body: string; parent_id: string | null }[];
  };
  const topId = firstPage.items.find((row) => row.body === comment && row.parent_id === null)?.id;
  expect(topId, '刚发的那条顶层评论要在帖子里').toBeTruthy();
  for (const n of [3, 4]) {
    await api(page, 'post', `/feedback/${feedbackId}/comments`, {
      body: `第 ${n} 条回复：${title}`,
      parent_id: topId,
    });
  }
  await page.reload();
  const hidden = `第 4 条回复：${title}`;
  // 「展开更多」不是「加载更多」：服务端一次给全了，折的是已经拿到手的那几条。
  await expect(page.getByText(hidden)).toBeHidden();
  await page.getByText('展开更多 2 条回复').click();
  await expect(page.getByText(hidden)).toBeVisible();

  // 删掉自己那条顶层评论：它下面的四条回复跟着一起走（服务端同事务软删——只删顶层
  // 会让那些回复变成查不到父亲的孤儿）。确认那一步必须写清连带几条，只说「删掉这条
  // 评论」而实际删掉一整栋楼，是在骗按按钮的人。
  await top.getByText('删除', { exact: true }).click();
  await expect(page.getByText('删掉这条评论，连同它下面的 4 条回复一起？')).toBeVisible();
  await page.getByText('确认删除').click();
  await expect(page.getByText('暂无评论')).toBeVisible();
  await expect(page.locator('.fb-page')).not.toContainText(comment);

  // 回到列表：这条在「全部」里，支持数跟着走。
  await page.goto('/feedback');
  const card = page.locator('.fb-card', { hasText: title });
  await expect(card).toBeVisible();
  await expect(card.locator('.fb-card__count')).toHaveText('1');

  // 「我的反馈」：从中心页的入口点进去，刚提的这条要在里面，而且支持数跟着走
  // （同一张卡片，两页读的是同一份服务端数据）。
  //
  // 入口那颗按钮是 `to=` 的 `v-btn`，渲染出来是链接不是按钮，所以按 role 找；
  // 限定在 `.fb-page__inner` 里找，免得撞上外壳导航里同名的东西。
  await page.locator('.fb-page__inner').getByRole('link', { name: '我的反馈' }).click();
  const mineCard = page.locator('.fb-card', { hasText: title });
  await expect(mineCard).toBeVisible();
  await expect(mineCard.locator('.fb-card__count')).toHaveText('1');

  // 接口这一层也确认一次：这一页的口径就是服务端的口径，两边都得说这条在。
  const mine = (await api(page, 'get', '/feedback/mine?page_size=50')) as { data?: { title: string }[] };
  expect(mine.data?.some((row) => row.title === title), '我提的那条要在 /feedback/mine 里').toBe(true);
});

test('管理员把一条反馈走完四级，指派、优先级、内部备注都留得下', async ({ page }) => {
  await login(page);
  const title = uniqueTitle('管理员这一条');
  const note = `内部备注：${title}`;

  // 前置数据走接口：这条用例要验的是管理端的界面，不是提交抽屉（上面那条已经验过）。
  const created = (await api(page, 'post', '/feedback', {
    kind: 'bug',
    title,
    problem: '管理员全流程的走查条目。',
  })) as { id: string };
  const id = created.id;

  await page.goto('/admin/feedback');
  // 两件事叠在一起，所以这一步的等待要给足：管理端在 onMounted 里先 loadMeta 再
  // loadAdmin（落地就断言行数会读到空表），而 vite 开发服务器是**按路由**编译的，
  // 「反馈管理」这一条在这条用例里是第一次进，冷编译能吃掉几十秒——默认 5s 的
  // expect 超时不够，那时 main 里还是空的。
  await expect(page.locator('tr.fb-row').first()).toBeVisible({ timeout: 45_000 });

  await page.locator('tr.fb-row', { hasText: title }).click();
  const drawer = page.locator('.fb-admin-drawer');
  await drawer.waitFor();

  // 状态是下拉，不是按钮：三个动作块里第一个 select 是状态，第二个是优先级。
  const status = drawer.locator('.v-select').first();
  for (const label of ['处理中', '已修复', '已上线']) {
    await status.click();
    await page.getByRole('option', { name: label, exact: true }).click();
    await expect(drawer).toContainText(label);
  }

  const priority = drawer.locator('.v-select').nth(1);
  await priority.click();
  await page.getByRole('option', { name: '高', exact: true }).click();
  await expect(drawer).toContainText('高');

  // 指派是自由输入框，回车触发（失焦也触发）。
  await drawer.getByPlaceholder('填 handle，留空取消指派').fill('alice');
  await drawer.getByPlaceholder('填 handle，留空取消指派').press('Enter');
  await expect(drawer).toContainText('alice');

  await drawer.getByPlaceholder('写点什么给下一个接手的人').fill(note);
  await drawer.getByRole('button', { name: '加一条备注' }).click();
  await expect(drawer).toContainText(note);

  // 收工。关抽屉走头上那颗关闭按钮——Vuetify 的 navigation-drawer 不吃 Esc
  // （`temporary` 只保证遮罩点击关得掉），点 Esc 抽屉还在，别把它当成人手路径。
  //
  // 关掉之后不能断言 `toBeHidden()`：`temporary` 的抽屉关上是**滑出视口**，
  // 元素留在 DOM 里、display 还是 flex、还是有尺寸，Playwright 因此判它 visible。
  // 真正表示「关了」的是那颗 `--active` 类。
  await drawer.getByRole('button', { name: '关闭' }).click();
  await expect(drawer).not.toHaveClass(/v-navigation-drawer--active/);
  await expect(page.locator('tr.fb-row', { hasText: title })).toContainText('@alice');

  // 用户侧的两栏跟着动：走完梯子的进「已完成」，不在「处理中」里。
  await page.goto('/feedback');
  await page.getByRole('tab', { name: /处理中/ }).click();
  await expect(page.locator('.fb-card', { hasText: title })).toBeHidden();
  await page.getByRole('tab', { name: /已完成/ }).click();
  await expect(page.locator('.fb-card', { hasText: title })).toBeVisible();

  // 详情页上「已上线」是收尾的最后一级。修复和上线是两件事，所以药丸上写的必须是
  // 「已上线」——写「已解决」等于把上线这一级又抹掉了，那是这轮特意拆开的。
  await page.goto(`/feedback/${id}`);
  await expect(page.locator('.fb-chip').first()).toHaveText('已上线');
  // 办完的条目不再接受支持——服务端那一侧回 412，按钮这一侧也该是禁用的。
  await expect(supportButton(page)).toBeDisabled();
});
