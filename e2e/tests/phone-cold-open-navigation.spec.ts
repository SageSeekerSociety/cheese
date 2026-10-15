import { test, expect } from '@playwright/test';

import { apiLogin } from './helpers';

// 手机上冷打开之后，点频道进得去、点底栏换得了页。
//
// 应用等第一页定下来才挂载（main.ts），顶栏和第一页在同一次渲染里出来。手机上项目的
// 频道列表把项目名那一行填进顶栏（TopicRailHeader 的 Teleport）；那时顶栏还不在文档里，
// 那一行就没挂上，之后每次换页都卡在挪动它的那一步：频道点了停在列表上，底栏点了一直转圈。

test('手机上冷打开项目，点频道进得了频道，点底栏换得了页', async ({ page }) => {
  await apiLogin(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');

  await page.locator('.topic-row').first().click();
  await expect(page).toHaveURL(/\/channels\//);
  await expect(page.locator('.composer').first()).toBeVisible();

  await page.goBack();
  await page.getByRole('link', { name: '待办' }).first().click();
  await expect(page).toHaveURL(/\/inbox$/);
  await expect(page.locator('.topic-row').first()).toBeHidden();
});
