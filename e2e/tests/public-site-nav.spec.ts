import { test, expect, type Page } from '@playwright/test';

// The public pages' top bar moves between them inside the app, without reloading
// it: a reload repaints the whole page white while the app boots. Each page
// still opens at its top, as a fresh page would, and Back returns to where the
// reader was. The docs are a separate site, so that link does leave the app.

async function markDocument(page: Page) {
  await page.evaluate(() => {
    (window as unknown as { sameDocument?: boolean }).sameDocument = true;
  });
}

const sameDocument = (page: Page) =>
  page.evaluate(() => (window as unknown as { sameDocument?: boolean }).sameDocument === true);

const scrollY = (page: Page) => page.evaluate(() => window.scrollY);

test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
});

test('the top bar moves between the public pages without reloading, each opening at its top', async ({ page }) => {
  await page.goto('/about');
  const nav = page.getByRole('navigation', { name: '主导航' });
  await expect(nav).toBeVisible();
  await markDocument(page);

  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await expect.poll(() => scrollY(page)).toBeGreaterThan(1000);
  const leftAt = await scrollY(page);

  await nav.getByRole('link', { name: '方案' }).click();
  await expect(page).toHaveURL(/\/solutions$/);
  await expect(page.getByRole('tab', { name: '企业' })).toBeVisible();
  expect(await sameDocument(page)).toBe(true);
  expect(await scrollY(page)).toBe(0);

  await page.evaluate(() => window.scrollTo(0, 1500));
  await nav.getByRole('link', { name: '下载' }).click();
  await expect(page).toHaveURL(/\/download$/);
  await expect(page.getByRole('heading', { name: '下载知是' })).toBeVisible();
  expect(await sameDocument(page)).toBe(true);
  expect(await scrollY(page)).toBe(0);

  await page.goBack();
  await page.goBack();
  await expect(page).toHaveURL(/\/about$/);
  expect(await sameDocument(page)).toBe(true);
  await expect.poll(() => scrollY(page)).toBe(leftAt);
});

test('产品 on the homepage goes to the story on the same page', async ({ page }) => {
  await page.goto('/about');
  await markDocument(page);
  await page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name: '产品' }).click();
  await expect(page).toHaveURL(/\/about#story$/);
  await expect(page.locator('#story')).toBeInViewport();
  expect(await sameDocument(page)).toBe(true);
});

test('文档 leaves the app for the docs site', async ({ page }) => {
  await page.goto('/solutions');
  await markDocument(page);
  const docs = page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name: '文档' });
  await expect(docs).toHaveAttribute('href', '/docs/');
  await docs.click();
  await expect(page).toHaveURL(/\/docs\/$/);
  // A new document: the docs are their own site, not a route of this app.
  await expect.poll(() => sameDocument(page)).toBe(false);
});

// On a phone the bar cannot hold the links beside the lockup, but /download and
// /docs/ still have to be reachable from the homepage. They live behind a
// disclosure button instead.
test('a phone reaches the nav links through the menu button, which Esc closes', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/about');
  const menu = page.locator('#site-menu');
  const toggle = page.getByRole('button', { name: '菜单' });
  await expect(toggle).toBeVisible();
  await expect(menu).toBeHidden();

  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(menu).toBeVisible();
  await expect(menu.getByRole('link', { name: '下载' })).toBeVisible();

  await page.keyboard.press('Escape');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(menu).toBeHidden();
  // The focus goes back to the button that opened the panel.
  await expect(toggle).toBeFocused();
});
