import type { Page } from '@playwright/test';

// Seeded by backend/alembic/versions/219831eb75a3_seed_demo_data.py — every demo
// user (alice, bobby, ...) shares this bcrypt-hashed password. alice additionally
// owns the demo project seeded by backend/scripts/seed_fusion_demo.py, so she's
// the one account guaranteed to land on a project with at least one topic.
export const DEMO_USERNAME = 'alice';
export const DEMO_PASSWORD = 'demo12345';

// Drives the real sign-in form (not a localStorage shortcut) so this doubles as
// the login flow's own e2e coverage. Leaves the page on the authenticated app
// shell (rail visible) before returning.
export async function login(page: Page, username = DEMO_USERNAME, password = DEMO_PASSWORD) {
  await page.goto('/account/signin');
  await page.getByLabel('用户名').fill(username);
  // exact: true — Vuetify's show/hide-password toggle button gets an
  // auto-generated aria-label of "密码 appended action" (see InputIcon.js),
  // which is a substring match for the bare label and trips Playwright's
  // strict mode (two elements match `getByLabel('密码')`).
  await page.getByLabel('密码', { exact: true }).fill(password);
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: '立即登录' }).click();
  await page.locator('.app-rail-item:not(.app-rail-item--add)').first().waitFor();
}

// Opens the first project from the rail and waits for its topic sidebar to
// finish loading, returning the count of visible (non-archived) topic rows.
export async function openFirstProject(page: Page) {
  // Project tiles carry `--tile` (they render an avatar image); the bare
  // `.app-rail-item:not(--add)` also matches the 首页/cheese home icon, which
  // sits first in the rail — clicking it lands on /spaces, not a project.
  await page.locator('.app-rail-item--tile').first().click();
  await page.locator('[title="新建话题"]').waitFor();
  const rows = page.locator('.topic-row');
  // The + button renders before the topics do, so returning here would let a
  // caller count zero rows and then watch the seeded ones arrive — a
  // `toHaveCount(before + 1)` that passes without anything being created.
  // alice's project always has at least one topic (see the seed note above).
  await rows.first().waitFor();
  return rows;
}
