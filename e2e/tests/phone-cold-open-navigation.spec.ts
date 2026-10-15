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

// 待办里的通知常常指到一条支线。手机上支线那一层以前也挂着频道的头部（TopicHeader），
// 它要填进顶栏那一格，而那一格只在路由声明过的页面上才有：支线没声明，头部没处挂，
// 往回走时卸它卸到一半出错——之后地址照变，屏幕停在那条支线上。
test('手机上从待办打开一条支线再返回，之后照样换得了页', async ({ page }) => {
  await apiLogin(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.locator('.topic-row').first().click();
  await expect(page).toHaveURL(/\/channels\/[^/]+$/);
  const thread = `${new URL(page.url()).pathname}/threads/1`;

  await page.goto('/inbox');
  await expect(page.locator('.v-bottom-navigation')).toBeVisible();
  // 点一条通知走的就是应用自己的跳转：这里直接让应用的路由去那条支线。
  await page.evaluate((to) => {
    const app = (document.querySelector('#app') as unknown as { __vue_app__: { config: { globalProperties: { $router: { push: (to: string) => Promise<unknown> } } } } }).__vue_app__;
    return app.config.globalProperties.$router.push(to);
  }, thread);
  await expect(page).toHaveURL(new RegExp(`${thread}$`));

  await expect(page.locator('.thread-pane')).toBeVisible();

  // Back on 待办, the thread is gone from the screen, not just from the address bar.
  await page.goBack();
  await expect(page).toHaveURL(/\/inbox$/);
  await expect(page.locator('.thread-pane')).toHaveCount(0);
  await page.locator('.v-bottom-navigation').getByText('工作区', { exact: true }).click();
  await expect(page.locator('.topic-row').first()).toBeVisible();
  await expect(page.locator('.thread-pane')).toHaveCount(0);
});
