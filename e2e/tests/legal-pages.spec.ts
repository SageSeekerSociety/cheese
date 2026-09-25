import { test, expect } from '@playwright/test';

// The legal documents are long, public, and opened outside the app shell —
// which locks document scrolling for the workspace. A reader has to be able to
// reach the end of what they are agreeing to, by wheel and by keyboard.
for (const path of ['/legal/terms', '/legal/privacy']) {
  test(`${path} can be read to the end`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto(path);
    const last = page.locator('.legal-body > *').last();
    await expect(last).toBeAttached();
    await expect(last).not.toBeInViewport();

    await page.mouse.move(640, 360);
    for (let i = 0; i < 40; i++) await page.mouse.wheel(0, 800);
    await expect(last).toBeInViewport();

    await page.keyboard.press('Home');
    await expect(last).not.toBeInViewport();
    await page.keyboard.press('End');
    await expect(last).toBeInViewport();
  });
}
