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
    // A made-up username, not the shared demo account: the backend's login
    // rate limiter locks out by username after 5 failed attempts (see
    // backend/app/api/routes/users.py user_login), and that Redis state
    // outlives a single test run against a persistent (non-CI) dev DB. Failing
    // against a throwaway name keeps this test from ever locking out `alice`,
    // who the other specs depend on being able to log in.
    await page.goto('/account/signin');
    await page.getByLabel('用户名').fill('no-such-user-e2e');
    await page.getByLabel('密码').fill('wrong-password');
    await page.getByRole('checkbox').check();
    await page.getByRole('button', { name: '立即登录' }).click();

    await expect(page.getByText(/invalid username or password/i)).toBeVisible();
    await expect(page).toHaveURL(/\/account\/signin/);
    const accessToken = await page.evaluate(() => localStorage.getItem('accessToken'));
    expect(accessToken).toBeFalsy();
  });
});
