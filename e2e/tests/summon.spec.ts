import { test, expect, type Locator, type Page } from '@playwright/test';
import { api, apiLogin, openFirstProject } from './helpers';

// 发送键旁边那颗队友头像的立场是：**正文是唯一的真相，头像只是它的镜子。** 组件测试
// 已经钉住了镜子本身（点一下写进去、手打 @ 它自己亮）。这里钉的是组件测试够不到的
// 那一段接缝——按钮在真的输入框旁边点得到，写进去的那个 @ 真的随消息发了出去，并且
// 在时间线上真的解析成了 @ 它。这一段断掉的样子恰好是「点了没反应」，而两侧各自的
// 测试都是绿的。

async function openFirstTopic(page: Page) {
  const rows = await openFirstProject(page);
  // 进项目落在项目总览，所以要显式打开一个话题。
  await rows.first().click();
  // 先等聊天区挂出来再等输入框：话题这条路由把 tiptap 那一堆拖进来，冷启动的
  // vite 要现编，第一次进来可以慢到几十秒（见 playwright.config.ts 的 timeout
  // 注释）。少了这一步，冷启动时报的是「输入框找不到」，看着像选择器写错了。
  await page.getByTestId('chat-scroll').waitFor({ timeout: 30_000 });
  const composer = page.locator('.composer-input .ProseMirror').first();
  // 输入框要等这个话题的 WS 连上才可用。
  await expect(composer).toBeEnabled({ timeout: 20_000 });
  // 房间名册到了，队友头像才开（名册没到时写进正文的 @ 会指错人，见最后一条）。
  // 名册随订阅的房间快照一起来，等的是「这条测试要测的东西已经就位」。
  await expect(page.locator('.summon__btn')).toBeEnabled({ timeout: 15_000 });
  return composer;
}

// 这个房间的 AI 队友叫什么，从头像的读屏名字上读，不写死「芝士」：队友是建话题时
// 选的，名字跟着它走。没交给时它读作「（发送并）交给<名字>（⌘/Ctrl+Enter）……」。
// 名字要拼进正则里，先转义（队友名字是人取的，不保证不含正则元字符）。
function escapeRe(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

async function agentNameFrom(button: Locator) {
  const label = (await button.getAttribute('aria-label')) ?? '';
  const name = label.match(/交给(.+?)[（，]/)?.[1]?.trim() ?? '';
  expect(name, '头像上读不到 AI 队友的名字').not.toBe('');
  return name;
}

test.describe('把消息交给芝士', () => {
  test.beforeEach(async ({ page }) => {
    await apiLogin(page);
  });

  test('按钮把 @ 写进正文，再点一下拿回来；手打 @ 它自己亮', async ({ page }) => {
    const composer = await openFirstTopic(page);
    const button = page.locator('.summon__btn');
    // 名册没到时按钮是关着的（那时候写进去的 @ 只是一行字）。
    await expect(button).toBeEnabled({ timeout: 15_000 });
    const agent = await agentNameFrom(button);

    await composer.fill('这条先不交给它');
    await expect(button).toHaveAttribute('aria-pressed', 'false');

    await button.click();
    // 输入框里那枚标签是队友的小头像加名字（「@」只在发出去之后的时间线上）。
    await expect(composer.locator('.mention')).toHaveText(new RegExp(`^${escapeRe(agent)}$`));
    await expect(button).toHaveAttribute('aria-pressed', 'true');

    // 再点一下要把正文恢复原样——按钮不存自己的状态，取消只能是把那几个字删掉。
    await button.click();
    await expect(composer).toHaveText('这条先不交给它');
    await expect(button).toHaveAttribute('aria-pressed', 'false');

    // 反过来：一个字都没点，光是正文里出现了那个 @，按钮就该亮。
    await composer.fill(`@${agent} 手打的`);
    await expect(button).toHaveAttribute('aria-pressed', 'true');
  });

  test('点按钮发出去的消息，在时间线上真的 @ 到了它', async ({ page }) => {
    const composer = await openFirstTopic(page);
    const button = page.locator('.summon__btn');
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
    const button = page.locator('.summon__btn');
    await expect(button).toBeEnabled({ timeout: 15_000 });
    const agent = await agentNameFrom(button);

    const text = `e2e summon by shortcut ${Date.now()}`;
    await composer.fill(text);
    await composer.press('Control+Enter');

    const sent = page.getByTestId('chat-scroll').locator('.im-text', { hasText: text }).last();
    await expect(sent).toBeVisible({ timeout: 15_000 });
    await expect(sent.locator('.mention', { hasText: `@${agent}` })).toBeVisible();
  });

  // 刚切进一个房间的头几百毫秒：房间名册还没回来，而按钮已经画出来了。这个窗口里
  // 点下去，写进正文的曾经是**项目**那位共用的芝士（`<@cheese>`）——消息照发、房间
  // 照样醒，但时间线上那条消息里的 @ 指的是另一个身份，而它自己写着「叫了它」。
  //
  // 名册平时随订阅的房间快照一起到，肉眼撞不上这个窗口。这条把快照从订阅的回答里
  // 拿掉（快照没读成时就是这样：前端退回去单独问名册），再把那一次问扣住不答，把窗口
  // 拉到能测的长度——测的是「名册没到时按钮关着」这句话真的成立，以及名册一到，@ 的
  // 就是这个房间那位。
  test('房间名册没到时按钮是关着的；到了以后 @ 的是这个房间那位', async ({ page }) => {
    const projects = (await api(page, 'get', '/projects')).data as { id: string }[];
    const project = projects[0];
    expect(project, 'alice 名下没有项目').toBeTruthy();
    const topics = (await api(page, 'get', `/topics?project_id=${project.id}`)).data as { id: string }[];
    const topic = topics[0];
    expect(topic, '这个项目里一个话题都没有').toBeTruthy();

    // 这个房间自己的座位是哪个 handle——后面要拿它比对时间线上的那枚 @ 名片。
    const roster = (await api(page, 'get', `/topics/${topic.id}/members`)).data as {
      member_handle: string;
      agent?: boolean;
    }[];
    const seat = roster.find((m) => m.agent);
    expect(seat, '这个房间名册上没有 AI 座位，这条用例无从谈起').toBeTruthy();

    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    await page.route(`**/topics/${topic.id}/members*`, async (route) => {
      await held;
      await route.continue();
    });
    await page.routeWebSocket(/\/rooms\/live/, (ws) => {
      const server = ws.connectToServer();
      ws.onMessage((message) => server.send(message));
      server.onMessage((message) => {
        const frame = JSON.parse(String(message));
        if (frame.type === 'subscribed') frame.room = null;
        ws.send(JSON.stringify(frame));
      });
    });

    await page.goto(`/projects/${project.id}/topics/${topic.id}`);
    const composer = page.locator('.composer-input .ProseMirror').first();
    await expect(composer).toBeEnabled({ timeout: 30_000 });

    const button = page.locator('.summon__btn');
    await expect(button).toBeVisible();
    await expect(button).toBeDisabled();
    // 键盘那一头同样关着：名册没到时它只是一次普通发送，正文里不会多出任何 @。
    // （这条消息不召唤，所以房间里不会有芝士跑起来。）
    const early = `e2e before roster ${Date.now()}`;
    await composer.fill(early);
    await composer.press('Control+Enter');
    const earlyRow = page.getByTestId('chat-scroll').locator('.im-text', { hasText: early }).last();
    await expect(earlyRow).toBeVisible({ timeout: 15_000 });
    await expect(earlyRow.locator('.mention')).toHaveCount(0);

    release();
    await expect(button).toBeEnabled({ timeout: 15_000 });

    const text = `e2e roster seat ${Date.now()}`;
    await composer.fill(text);
    await button.click();
    await composer.press('Enter');

    const sent = page.getByTestId('chat-scroll').locator('.im-text', { hasText: text }).last();
    await expect(sent).toBeVisible({ timeout: 15_000 });
    // 断在 handle 上，不是断在名字上：两行都叫「芝士」，只有这个 handle 说的是
    // 这个房间里会动的那一位。
    await expect(sent.locator(`.mention[data-handle="${seat!.member_handle}"]`)).toBeVisible();
  });

  // 手机宽度上那一行站得住：头像不被挤出屏幕、不和发送叠在一起，点它 @ 照样进正文。
  //
  // 手机宽度必须是**打开就这么宽**，不能把桌面窗口拖窄了测：工作台的分栏宽度是挂载
  // 时算出来的，拖窄之后整个输入框会塌成 0 宽。所以先把视口调好再进这个话题；走 URL
  // 是因为手机上左边那条项目栏是收起来的，点不进去。
  test('手机宽度上头像和发送在同一行，点它照样把 @ 写进正文', async ({ page }) => {
    const projects = (await api(page, 'get', '/projects')).data as { id: string }[];
    const project = projects[0];
    expect(project, 'alice 名下没有项目').toBeTruthy();
    const topics = (await api(page, 'get', `/topics?project_id=${project.id}`)).data as { id: string }[];
    expect(topics[0], '这个项目里一个话题都没有').toBeTruthy();

    await page.setViewportSize({ width: 375, height: 780 });
    await page.goto(`/projects/${project.id}/topics/${topics[0].id}`);
    const composer = page.locator('.composer-input .ProseMirror').first();
    await expect(composer).toBeEnabled({ timeout: 30_000 });

    const button = page.locator('.summon__btn');
    await expect(button).toBeEnabled({ timeout: 15_000 });
    const agent = await agentNameFrom(button);

    // 同一行：头像和发送键的垂直中心对得上，说明没有谁被挤到下一行去。
    const send = page.locator('.composer-send');
    const [b, sd] = [await button.boundingBox(), await send.boundingBox()];
    expect(b, '头像没有布局盒子').not.toBeNull();
    expect(sd, '发送键没有布局盒子').not.toBeNull();
    const center = (box: { y: number; height: number }) => box.y + box.height / 2;
    expect(Math.abs(center(b!) - center(sd!))).toBeLessThan(4);
    // 而且头像没被挤出屏幕，也没压到发送键上。
    expect(b!.x + b!.width).toBeLessThanOrEqual(375);
    expect(b!.x + b!.width).toBeLessThanOrEqual(sd!.x + 1);

    await composer.fill('这条是说给人听的');
    await button.click();
    // 看到的是队友名字标签加这句话（藏起来的那串 <@handle> 不算看到的字）。
    await expect(composer).toHaveText(new RegExp(`^${escapeRe(agent)} 这条是说给人听的$`), { useInnerText: true });
  });
});
