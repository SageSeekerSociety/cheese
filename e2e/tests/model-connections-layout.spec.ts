import { test, expect } from '@playwright/test';
import { fieldDefects } from './field-layout';

test.describe('表单字段不会互相压住，也不会被裁掉', () => {
  test('添加自己的模型连接：桌面与手机的选择字段完整可见', async ({ page }) => {
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      // The catalog state with the dialog open: on the team page its button is a header command.
      await page.goto('/demo/catalog/model-connections-view/frame/5');
      const dialog = page.getByRole('dialog');
      await expect(dialog.getByRole('combobox', { name: /模型连接|Model connection/, exact: true })).toBeVisible();
      expect(await fieldDefects(dialog)).toEqual([]);
      const bounds = await dialog.boundingBox();
      expect(bounds!.x).toBeGreaterThanOrEqual(0);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
    }
  });
});
