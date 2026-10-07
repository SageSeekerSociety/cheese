import { test, expect } from '@playwright/test';

// 冷启动骨架外壳（index.html 里内联的那一屏）。
//
// 慢网下 JS 包要几十秒才到，头一帧只可能是服务端返回的 HTML 画出来的东西——实测
// 400kbps 下原本是一整屏纯白。这份 HTML 现在自带应用外壳的骨架（左栏、侧栏、正文），
// 真正的界面一挂载它就消失。两件事都只跟 index.html + main.ts 有关，所以下面直接把
// 入口那一支请求按住，不依赖后端。

test('慢网首帧画的是骨架外壳，不是白屏', async ({ page }) => {
  // 按住所有脚本请求：入口跑不起来，屏幕上剩下的就只能是 HTML 自己画的那一屏。
  // 按资源类型而不是按路径拦，开发服务器的 /src/main.ts 和生产构建带哈希的入口都拦得住。
  await page.route('**/*', (route) =>
    route.request().resourceType() === 'script' ? route.abort() : route.continue(),
  );
  await page.goto('/', { waitUntil: 'domcontentloaded' });

  const shell = page.locator('#sx-boot-skeleton');
  await expect(shell).toBeVisible();

  // 业主认可的比例：56px 左栏、210px 侧栏，第一格是品牌琥珀色的圆角块。
  // 量 boundingBox 而不是 computed width——盒子是 border-box，computed width 不含边框。
  expect((await shell.locator('.sx-rail').boundingBox())?.width).toBe(56);
  expect((await shell.locator('.sx-side').boundingBox())?.width).toBe(210);
  await expect(shell.locator('.sx-icon--brand')).toHaveCSS('background-color', 'rgb(245, 127, 23)');
  // 骨架本身有货：侧栏的行 + 正文的标题/卡片骨条。
  expect(await shell.locator('.sx-bone').count()).toBe(13);
});

test('应用一挂载，骨架节点立刻消失', async ({ page }) => {
  await page.goto('/');
  // 不该有残留：mount 那一帧 main.ts 就把它摘掉了。
  await expect(page.locator('#sx-boot-skeleton')).toHaveCount(0);
});
