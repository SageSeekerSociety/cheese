import { test, expect, type Locator, type Page } from '@playwright/test';
import { api, login, openFirstProject } from './helpers';

// 「交给芝士」这颗按钮的立场是：**正文是唯一的真相，按钮只是它的镜子。** 组件测试
// 已经钉住了镜子本身（点一下写进去、手打 @ 它自己亮）。这里钉的是组件测试够不到的
// 那一段接缝——按钮在真的输入框旁边点得到，写进去的那个 @ 真的随消息发了出去，并且
// 在时间线上真的解析成了 @ 它。这一段断掉的样子恰好是「点了没反应」，而两侧各自的
// 测试都是绿的。

async function openFirstTopic(page: Page) {
  const rows = await openFirstProject(page);
  // 房间名册是另一条 HTTP 请求，比输入框可用要晚。它到之前，@ 补全名单里坐着的是
  // **项目**那位共用的芝士（房间自己那位还不在名单上），此刻写进正文的 @ 指的是另
  // 一个 handle。这里等它到齐再动手，等的是「这条测试要测的东西已经就位」，不是
  // 拿一个 sleep 去掩盖时序——那个窗口本身另说（见话题文档）。
  const rosterLoaded = page.waitForResponse(
    (r) => /\/topics\/[^/]+\/members(\?|$)/.test(r.url()) && r.ok()
  );
  // 进项目落在看板，所以要显式打开一个话题。
  await rows.first().click();
  // 先等聊天区挂出来再等输入框：话题这条路由把 tiptap 那一堆拖进来，冷启动的
  // vite 要现编，第一次进来可以慢到几十秒（见 playwright.config.ts 的 timeout
  // 注释）。少了这一步，冷启动时报的是「输入框找不到」，看着像选择器写错了。
  await page.getByTestId('chat-scroll').waitFor({ timeout: 30_000 });
  const composer = page.locator('.composer-input textarea').first();
  // 输入框要等这个话题的 WS 连上才可用。
  await expect(composer).toBeEnabled({ timeout: 20_000 });
  await rosterLoaded;
  return composer;
}

// 这个房间的 AI 队友叫什么，从按钮自己的标签上读，不写死「芝士」：队友是建话题时
// 选的，名字跟着它走。窄屏上这个 span 被 CSS 藏起来，所以读 textContent 而不是
// innerText。
// 名字要拼进正则里，先转义（队友名字是人取的，不保证不含正则元字符）。
function escapeRe(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

async function agentNameFrom(button: Locator) {
  const label = (await button.locator('.summon-btn-label').evaluate((el) => el.textContent)) ?? '';
  const name = label.replace(/^交给/, '').trim();
  expect(name, '按钮上读不到 AI 队友的名字').not.toBe('');
  return name;
}

test.describe('把消息交给芝士', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('按钮把 @ 写进正文，再点一下拿回来；手打 @ 它自己亮', async ({ page }) => {
    const composer = await openFirstTopic(page);
    const button = page.locator('.summon-btn');
    // 名册没到时按钮是关着的（那时候写进去的 @ 只是一行字）。
    await expect(button).toBeEnabled({ timeout: 15_000 });
    const agent = await agentNameFrom(button);

    await composer.fill('这条先不交给它');
    await expect(button).toHaveAttribute('aria-pressed', 'false');

    await button.click();
    await expect(composer).toHaveValue(new RegExp(`@${escapeRe(agent)}`));
    await expect(button).toHaveAttribute('aria-pressed', 'true');

    // 再点一下要把正文恢复原样——按钮不存自己的状态，取消只能是把那几个字删掉。
    await button.click();
    await expect(composer).toHaveValue('这条先不交给它');
    await expect(button).toHaveAttribute('aria-pressed', 'false');

    // 反过来：一个字都没点，光是正文里出现了那个 @，按钮就该亮。
    await composer.fill(`@${agent} 手打的`);
    await expect(button).toHaveAttribute('aria-pressed', 'true');
  });

  test('点按钮发出去的消息，在时间线上真的 @ 到了它', async ({ page }) => {
    const composer = await openFirstTopic(page);
    const button = page.locator('.summon-btn');
    await expect(button).toBeEnabled({ timeout: 15_000 });
    const agent = await agentNameFrom(button);

    const text = `e2e summon by button ${Date.now()}`;
    await composer.fill(text);
    await button.click();
    await composer.press('Enter');

    // 断言落在屏幕上：那条消息在，且它里面有一枚指向 AI 队友的 @ 名片。发出去的是
    // `<@handle>` 而不是一行字，这是「按钮真的接上了」和「按钮只是改了改草稿」的
    // 分界线。
    const sent = page.getByTestId('chat-scroll').locator('.im-text', { hasText: text }).last();
    await expect(sent).toBeVisible({ timeout: 15_000 });
    await expect(sent.locator('.mention', { hasText: `@${agent}` })).toBeVisible();
  });

  test('⌘/Ctrl+Enter 一个 @ 都不用打，发出去的消息自己说明它叫了谁', async ({ page }) => {
    const composer = await openFirstTopic(page);
    const button = page.locator('.summon-btn');
    await expect(button).toBeEnabled({ timeout: 15_000 });
    const agent = await agentNameFrom(button);

    const text = `e2e summon by shortcut ${Date.now()}`;
    await composer.fill(text);
    await composer.press('Control+Enter');

    const sent = page.getByTestId('chat-scroll').locator('.im-text', { hasText: text }).last();
    await expect(sent).toBeVisible({ timeout: 15_000 });
    await expect(sent.locator('.mention', { hasText: `@${agent}` })).toBeVisible();
  });

  // 手机宽度上按钮收成一个 @ 图标。这条不是审美，是排版会不会塌：那一行右边还站着
  // 发送，按钮带着三个字的时候它们挤不下就要换行。
  //
  // 手机宽度必须是**打开就这么宽**，不能把桌面窗口拖窄了测：工作台的分栏宽度是挂载
  // 时算出来的，拖窄之后整个输入框会塌成 0 宽（和这颗按钮无关——600px 上标签还好好
  // 显示着就已经塌了）。照那条路走，测到的是另一个毛病。所以先把视口调好再进这个
  // 话题；走 URL 是因为手机上左边那条项目栏是收起来的，点不进去。
  test('手机宽度上按钮收成一个图标，输入框那一行不换行', async ({ page }) => {
    const projects = (await api(page, 'get', '/projects')).data as { id: string }[];
    const project = projects[0];
    expect(project, 'alice 名下没有项目').toBeTruthy();
    const topics = (await api(page, 'get', `/topics?project_id=${project.id}`)).data as { id: string }[];
    expect(topics[0], '这个项目里一个话题都没有').toBeTruthy();

    await page.setViewportSize({ width: 375, height: 780 });
    await page.goto(`/projects/${project.id}/topics/${topics[0].id}`);
    const composer = page.locator('.composer-input textarea').first();
    await expect(composer).toBeEnabled({ timeout: 30_000 });

    const button = page.locator('.summon-btn');
    await expect(button).toBeVisible();
    // 三个字收掉，只剩那个 @。
    await expect(button.locator('.summon-btn-label')).toBeHidden();

    // 同一行：按钮和发送键的垂直中心对得上，说明没有谁被挤到下一行去。
    const send = page.locator('.composer-send');
    const [b, sd] = [await button.boundingBox(), await send.boundingBox()];
    expect(b, '按钮没有布局盒子').not.toBeNull();
    expect(sd, '发送键没有布局盒子').not.toBeNull();
    const center = (box: { y: number; height: number }) => box.y + box.height / 2;
    expect(Math.abs(center(b!) - center(sd!))).toBeLessThan(4);
    // 而且按钮没被挤出屏幕。
    expect(b!.x + b!.width).toBeLessThanOrEqual(375);

    // 收成图标之后照样是那颗按钮：点一下，@ 还是进正文。
    await composer.fill('这条是说给人听的');
    await button.click();
    await expect(composer).toHaveValue(/^@.+ 这条是说给人听的$/);
  });
});
