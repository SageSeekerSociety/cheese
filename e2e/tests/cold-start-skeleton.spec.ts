import { test, expect, type Page } from '@playwright/test';

// 冷启动骨架（index.html 里内联的那一屏）。
//
// 慢网下 JS 包要几十秒才到，头一帧只可能是服务端返回的 HTML 画出来的东西——实测
// 400kbps 下原本是一整屏纯白。现在这份 HTML 按这次打开会落到哪一页画它的轮廓：没登录的
// 人在推广页上看到推广页的轮廓，不是应用外壳；登录的人看到应用的外框，侧栏是他自己拖出来
// 的宽度，换成真页面时版面不跳。真正的界面一挂载它就消失。这些只跟 index.html + main.ts
// 有关，所以下面直接把脚本请求按住，不依赖后端。

// 按住所有脚本请求：入口跑不起来，屏幕上剩下的就只能是 HTML 自己画的那一屏。
// 按资源类型而不是按路径拦，开发服务器的 /src/main.ts 和生产构建带哈希的入口都拦得住。
async function holdScripts(page: Page) {
  await page.route('**/*', (route) =>
    route.request().resourceType() === 'script' ? route.abort() : route.continue(),
  );
}

test('慢网首帧，没登录的人在首页上看到的是首页的轮廓，不是应用外壳', async ({ page }) => {
  await holdScripts(page);
  await page.goto('/', { waitUntil: 'domcontentloaded' });

  const skeleton = page.locator('#sx-boot-skeleton');
  await expect(skeleton).toBeVisible();
  await expect(skeleton.locator('.sx-landing')).toBeVisible();
  await expect(skeleton.locator('.sx-rail')).toBeHidden();
});

test('慢网首帧，登录的人看到应用外框，侧栏是他留下的宽度', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('accessToken', 'stored-token');
    localStorage.setItem('user', JSON.stringify({ id: 1, onboarded_at: '2026-01-01T00:00:00Z' }));
    localStorage.setItem('cheesex.sidebarWidth', '320');
  });
  await holdScripts(page);
  await page.goto('/inbox', { waitUntil: 'domcontentloaded' });

  const skeleton = page.locator('#sx-boot-skeleton');
  await expect(skeleton).toBeVisible();
  await expect(skeleton.locator('.sx-landing')).toBeHidden();
  // 正文从左栏加上那 320px 之后开始，和真页面一样（侧栏和它的分隔线一起占这么宽）。
  const rail = await skeleton.locator('.sx-rail').boundingBox();
  const main = await skeleton.locator('.sx-main').boundingBox();
  expect(rail?.width).toBeGreaterThan(0);
  expect(main?.x).toBe((rail?.x ?? 0) + (rail?.width ?? 0) + 320);
});

test('应用一挂载，骨架节点立刻消失', async ({ page }) => {
  await page.goto('/');
  // 不该有残留：mount 那一帧 main.ts 就把它摘掉了。
  await expect(page.locator('#sx-boot-skeleton')).toHaveCount(0);
});
