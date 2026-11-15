import { test, expect, type Page } from '@playwright/test';
import { apiLogin, apiToken } from './helpers';

// 管理后台「成员」页的加人那一套，真的在浏览器里点一遍。
//
// 这几条只能在这里测：候选画在 `v-autocomplete` 的浮层菜单里，happy-dom 里那个菜单
// 不展开（`AdminMembersPage.spec.ts` 顶上记着），所以「候选那一行长什么样」「搜不到时
// 下拉里说什么」「被拒的原话人看不看得见」在单元测试里一条都断不了。
//
// alice 是根管理员（playwright.config.ts 里后端 webServer 的 `PLATFORM_ADMIN_HANDLES`）；
// bobby、carol 是种子迁移里的普通账号（昵称 Bob、Carol）。

const dialog = (page: Page) => page.getByRole('dialog').filter({ hasText: '添加管理员' });
const field = (page: Page) => dialog(page).getByRole('combobox', { name: '搜索账号' });

async function openAddDialog(page: Page) {
  await apiLogin(page);
  await page.goto('/admin/members');
  await page.getByRole('button', { name: '添加管理员' }).click();
  await expect(dialog(page)).toBeVisible();
}

/** 这条用例自己加进名单的人，结束时移出去：名单是整个库共用的，留着会让下一条
 *  「已经是管理员」的判断换一个样子。 */
async function removeAdmin(page: Page, handle: string) {
  const token = await apiToken(page);
  const res = await page.request.delete(`/api/admin/admins/${handle}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(res.ok(), `DELETE /admin/admins/${handle} → ${res.status()}`).toBeTruthy();
}

test('一行候选：昵称在上、handle 在下，各画一次', async ({ page }) => {
  await openAddDialog(page);
  await field(page).fill('bobby');

  const option = page.getByRole('option').filter({ hasText: 'Bob' });
  await expect(option).toHaveCount(1);
  // 以前是 handle、昵称、handle 三行：`v-list-item` 先画了透传来的 `title`（= handle）。
  await expect(option.getByText('bobby', { exact: true })).toHaveCount(1);
  await expect(option.getByText('Bob', { exact: true })).toHaveCount(1);
});

test('搜不到人时下拉里说出来，而不是什么都不画', async ({ page }) => {
  await openAddDialog(page);
  await field(page).fill('没有这个人-e2e');
  await expect(page.getByText('没找到这个人')).toBeVisible();
});

test('打了字又马上删光，「正在搜」不会一直转', async ({ page }) => {
  await openAddDialog(page);
  // 两步之间短于 250ms 的防抖：计时器被掐掉，以前就再没有谁去把「正在搜」关上。
  await field(page).pressSequentially('b');
  await field(page).press('Backspace');
  await page.waitForTimeout(600);
  await expect(dialog(page).locator('.v-progress-linear--active')).toHaveCount(0);
});

test('有人被拒时，原话画在框里，加成了的人不再留在选择里', async ({ page }) => {
  await openAddDialog(page);
  // 服务端的拒绝（账号刚注销、是 agent……）在种子数据里造不出来，这里让 carol 那一次
  // 回一条拒绝，bobby 那一次照常落到真后端。
  await page.route('**/api/admin/admins', async (route) => {
    const body = route.request().postDataJSON() as { handle?: string } | null;
    if (route.request().method() === 'POST' && body?.handle === 'carol') {
      await route.fulfill({
        status: 400,
        contentType: 'application/json',
        body: JSON.stringify({ code: 400, message: 'e2e 造的拒绝', data: null }),
      });
      return;
    }
    await route.fallback();
  });

  for (const [query, nickname] of [
    ['bobby', 'Bob'],
    ['carol', 'Carol'],
  ]) {
    await field(page).fill(query);
    await page.getByRole('option').filter({ hasText: nickname }).click();
  }
  await dialog(page).getByRole('button', { name: '添加', exact: true }).click();

  try {
    // 框没关（有人被拒），被拒的原话就在框里，人看得见。
    await expect(dialog(page).getByText('carol：e2e 造的拒绝')).toBeVisible();
    // 选择里只剩被拒的那一个：再点一次「添加」只会重试 carol。
    const chips = dialog(page).locator('.v-chip');
    await expect(chips).toHaveCount(1);
    await expect(chips).toContainText('carol');
  } finally {
    await removeAdmin(page, 'bobby');
  }
});

test('上一次移出失败的红条，在这次加人成功之后让位给「已添加」', async ({ page }) => {
  await apiLogin(page);
  const token = await apiToken(page);
  const added = await page.request.post('/api/admin/admins', {
    headers: { Authorization: `Bearer ${token}` },
    data: { handle: 'bobby' },
  });
  expect(added.ok()).toBeTruthy();
  // 移出失败在种子数据里造不出来，这里让那一次 DELETE 回 500。
  await page.route('**/api/admin/admins/bobby', async (route) => {
    if (route.request().method() === 'DELETE') {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ code: 500, message: 'e2e 造的移出失败', data: null }),
      });
      return;
    }
    await route.fallback();
  });

  try {
    await page.goto('/admin/members');
    await page.getByRole('button', { name: '移出', exact: true }).first().click();
    await page.getByRole('dialog').getByRole('button', { name: '移出', exact: true }).click();
    await expect(page.getByText(/e2e 造的移出失败/)).toBeVisible();

    await page.getByRole('button', { name: '添加管理员' }).click();
    await field(page).fill('carol');
    await page.getByRole('option').filter({ hasText: 'Carol' }).click();
    await dialog(page).getByRole('button', { name: '添加', exact: true }).click();

    // 全部加成，框关上；页面上那条变成这次的结果。
    await expect(dialog(page)).toBeHidden();
    await expect(page.getByText('已添加：carol')).toBeVisible();
    await expect(page.getByText(/e2e 造的移出失败/)).toHaveCount(0);
  } finally {
    await page.unroute('**/api/admin/admins/bobby');
    await removeAdmin(page, 'bobby');
    await removeAdmin(page, 'carol');
  }
});
