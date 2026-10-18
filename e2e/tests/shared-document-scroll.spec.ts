import { test, expect } from '@playwright/test';

// 经链接分享的文档在应用外框之外打开，整页滚动。比一屏长的文档，读的人要能滚到末尾。
// 文档内容由这里给出，不依赖种子数据里有一条分享链接。
test('分享的长文档能滚到末尾', async ({ page }) => {
  const paragraphs = Array.from({ length: 60 }, (_, i) => `第 ${i + 1} 段。`);
  await page.route('**/api/shared/scroll-check', (route) =>
    route.fulfill({
      json: {
        code: 200,
        data: { title: '长文档', content: paragraphs.join('\n\n'), updated_at: '2026-10-18T00:00:00Z' },
      },
    }),
  );

  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/s/scroll-check');
  const last = page.getByText('第 60 段。', { exact: true });
  await expect(last).toBeAttached();
  await expect(last).not.toBeInViewport();

  await page.mouse.move(640, 360);
  for (let i = 0; i < 40; i++) await page.mouse.wheel(0, 800);
  await expect(last).toBeInViewport();
});
