import { test, expect } from '@playwright/test';
import { login, openFirstProject } from './helpers';

test.describe('Topics and chat', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('creating a topic adds it to the sidebar as the active topic', async ({ page }) => {
    const rows = await openFirstProject(page);
    const before = await rows.count();

    // The + button opens a teammate menu rather than creating on the spot: the
    // room's agent has to be chosen now, because changing it later throws away
    // the topic's session. The default teammate is deliberately first, so the
    // everyday path is "open, take the top one" — which is what this clicks.
    await page.locator('[title="新建话题"]').click();
    // Scoped by the menu's own subheader: the sidebar renders a <v-list> per
    // topic group and every one of them is a listbox, so an unfiltered
    // getByRole('listbox') matches many and trips strict mode.
    const teammates = page.getByRole('listbox').filter({ hasText: '交给哪个 AI 队友' });
    await teammates.locator('.v-list-item').first().click();

    await expect(rows).toHaveCount(before + 1);
    await expect(page.locator('.topic-row.is-active')).toHaveCount(1);
  });

  test('sending a chat message shows it in the conversation', async ({ page }) => {
    await openFirstProject(page);
    // Land on whatever topic is selected by default (root/本体 for a project
    // alice already owns) rather than creating one — creation is covered by
    // the previous test, and reusing an existing topic keeps this test
    // focused on the composer/WS send path alone.
    const composer = page.locator('.composer-input textarea').first();
    await expect(composer).toBeEnabled({ timeout: 15_000 }); // enabled only once the topic's WS connects

    const message = `e2e message ${Date.now()}`;
    await composer.fill(message);
    await composer.press('Enter');

    await expect(page.getByTestId('chat-scroll').getByText(message)).toBeVisible();
  });
});
