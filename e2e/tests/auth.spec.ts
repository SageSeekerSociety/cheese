import { randomBytes } from 'node:crypto';
import { test, expect } from '@playwright/test';
import { DEMO_USERNAME, DEMO_PASSWORD, login } from './helpers';

test.describe('Login', () => {
  test('valid credentials sign the user in and land on the authenticated app shell', async ({ page }) => {
    await login(page);
    await expect(page.getByText('登录成功')).toBeVisible();
    await expect(page.locator('.app-rail-item:not(.app-rail-item--add)').first()).toBeVisible();
    const accessToken = await page.evaluate(() => localStorage.getItem('accessToken'));
    expect(accessToken).toBeTruthy();
  });

  test('wrong credentials are rejected and the user stays on the sign-in page', async ({ page }) => {
    // CI runners retain Redis login lockouts between jobs. Use a unique fake
    // username per attempt, within the form's 30-character limit.
    await page.goto('/account/signin');
    await page.getByLabel('用户名').fill(`e2e-${randomBytes(12).toString('hex')}`);
    // exact: true — see helpers.ts::login for why (Vuetify's password-visibility
    // toggle button's auto aria-label contains "密码" as a substring).
    await page.getByLabel('密码', { exact: true }).fill('wrong-password');
    await page.getByRole('checkbox').check();
    await page.getByRole('button', { name: '立即登录' }).click();

    await expect(page.getByText(/invalid username or password/i)).toBeVisible();
    await expect(page).toHaveURL(/\/account\/signin/);
    const accessToken = await page.evaluate(() => localStorage.getItem('accessToken'));
    expect(accessToken).toBeFalsy();
  });
});

test.describe('English login', () => {
  test.use({ locale: 'en-US' });

  test('keeps the selected language after reload and signs in through the English form', async ({ page }) => {
    await page.goto('/account/signin');
    await expect(page.getByRole('heading', { name: 'Sign in', exact: true })).toBeVisible();
    await page.getByRole('button', { name: '切换到中文' }).click();
    await page.getByRole('button', { name: 'Switch to English' }).click();
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('lang', 'en');
    await page.getByLabel('Username').fill(DEMO_USERNAME);
    await page.getByLabel('Password', { exact: true }).fill(DEMO_PASSWORD);
    await page.getByRole('checkbox').check();
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();

    await expect(page.getByText('Signed in', { exact: true })).toBeVisible();
    await expect(page.locator('.app-rail-item:not(.app-rail-item--add)').first()).toBeVisible();
    expect(await page.evaluate(() => localStorage.getItem('accessToken'))).toBeTruthy();
  });
});
