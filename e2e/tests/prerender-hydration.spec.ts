import { test, expect } from '@playwright/test';

// 预渲染的公开页，浏览器接管时要和构建时渲染出来的那份对得上。
//
// 构建时渲染的是 App.vue（scripts/prerender.mjs），浏览器在这份 HTML 上直接接管
// （hydrate）。两边算出来的东西不一样时，Vue 把对不上的那几个节点删掉、重新画一份：
// 页面会闪一下，接管也白做了。正式版的构建去掉了所有 console 调用，Vue 的那句提示
// 不会出现，所以这里看的是结果——构建时那份 HTML 里的节点，接管之后还在不在。
// 只对得上结构：两边同一个节点上属性不一样时 Vue 不改它，这里也看不出来。
// 下面照线上的样子发预渲染页（nginx.conf 把这几个地址指到 prerender/ 下）。

const PAGES = [
  { path: '/', file: 'index' },
  { path: '/about', file: 'about' },
  { path: '/solutions', file: 'solutions' },
];

for (const { path, file } of PAGES) {
  test(`the prerendered ${path} is taken over without a mismatch`, async ({ page, baseURL }) => {
    const prerendered = await (await page.request.get(`${baseURL}/prerender/${file}.html`)).text();
    test.skip(!prerendered.includes('cx-prerendered'), 'this build has no prerendered pages');
    await page.route(
      (url) => url.pathname === path,
      (route) =>
        route.request().resourceType() === 'document'
          ? route.fulfill({ body: prerendered, contentType: 'text/html' })
          : route.continue(),
    );
    // Watch #app from the first byte. Vue removes and redraws a node that does not
    // match while it takes the page over, and main.ts drops <html data-boot> the
    // moment that is done; what is removed after it is the page's own later changes
    // (a section that loads when scrolled near), not a mismatch.
    await page.addInitScript(() => {
      const state = window as unknown as { __takenOver?: boolean; __replaced: string[] };
      state.__replaced = [];
      const describe = (node: Node) =>
        node instanceof Element ? `<${node.tagName.toLowerCase()} class="${node.className}">` : `#${node.nodeName}`;
      new MutationObserver((records) => {
        for (const record of records) {
          if (state.__takenOver) return;
          if (record.type === 'attributes' && record.oldValue !== null) {
            state.__takenOver = true;
            return;
          }
          if (record.type === 'childList' && document.getElementById('app')?.contains(record.target)) {
            record.removedNodes.forEach((node) => state.__replaced.push(describe(node)));
          }
        }
      }).observe(document, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: ['data-boot'],
        attributeOldValue: true,
      });
    });

    await page.goto(path, { waitUntil: 'load' });
    // The boot attribute goes once the app has taken the page over (main.ts).
    await expect(page.locator('html[data-boot]')).toHaveCount(0, { timeout: 20_000 });
    const replaced = await page.evaluate(() => (window as unknown as { __replaced: string[] }).__replaced);
    expect(replaced).toEqual([]);
  });
}
