import { test, expect } from '@playwright/test';

test.describe('Smoke Tests', () => {
  test('backend health check responds', async ({ request }) => {
    const response = await request.get('http://localhost:8081/healthz');
    expect(response.ok()).toBeTruthy();
  });

  test('frontend loads', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/.+/);
  });

  test('frontend shows login or home page', async ({ page }) => {
    await page.goto('/');
    const body = page.locator('body');
    await expect(body).toBeVisible();
  });
});
