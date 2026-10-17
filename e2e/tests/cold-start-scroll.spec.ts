import { test, expect } from '@playwright/test';

// 头一次打开、样式表还没缓存时，首页加载完要能滚动。
//
// 首屏不等外部样式表：它们先当预加载去下，下完才换成样式表（scripts/prerender.mjs）。
// 之后才加载的代码块也要这些样式；要是在那张还没换好的时候又挂一份到页头最后，Vuetify
// 那份锁住整页滚动的规则就排到了首页解锁它的规则后面，首页滚不动——刷新之后样式表走缓存、
// 先换好了，又能滚。下面照线上的样子模拟没有缓存的第一次：nginx 在 / 上发的是预渲染好的
// 首页（nginx.conf），Vuetify 那张大样式表最晚到。

test('样式表晚到的第一次打开，首页加载完照样能滚动', async ({ page, baseURL }) => {
  const prerendered = await (await page.request.get(`${baseURL}/prerender/index.html`)).text();
  test.skip(!prerendered.includes('cx-prerendered'), 'this build has no prerendered home page');
  await page.route(/\/$/, (route) =>
    route.request().resourceType() === 'document'
      ? route.fulfill({ body: prerendered, contentType: 'text/html' })
      : route.continue(),
  );
  await page.route(/\/assets\/vuetify-[^/]*\.css$/, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 2500));
    await route.continue();
  });

  await page.goto('/', { waitUntil: 'load' });
  await page.waitForTimeout(4000);

  await expect
    .poll(async () => {
      await page.mouse.move(200, 400);
      await page.mouse.wheel(0, 600);
      return page.evaluate(() => document.scrollingElement?.scrollTop ?? 0);
    })
    .toBeGreaterThan(0);
});
