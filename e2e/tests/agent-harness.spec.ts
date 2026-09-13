import { test, expect } from '@playwright/test';
import { login, openFirstProject } from './helpers';

test('a teammate keeps its identity and role across saved harness switches', async ({ page }) => {
  await login(page);
  await openFirstProject(page);
  const project = page.url().match(/\/projects\/([0-9a-f-]{36})/)?.[1];
  expect(project).toBeTruthy();
  await page.goto(`/projects/${project}/agents`);
  const name = `Harness CI ${Date.now()}`;
  const role = 'Keep this role when changing the native harness.';
  await page.getByRole('button', { name: '新建队友', exact: true }).first().click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('button', { name: '保存', exact: true })).toBeEnabled();
  await dialog.getByLabel('名字', { exact: true }).fill(name);
  await dialog.getByLabel('角色设定（可留空）').fill(role);

  async function selectHarness(label: string) {
    await dialog.locator('.v-select').filter({ hasText: '运行方式' }).getByRole('combobox').click();
    await page.getByRole('option', { name: label, exact: true }).click();
    await expect(dialog.getByLabel('角色设定（可留空）')).toHaveValue(role);
  }

  async function save() {
    const response = page.waitForResponse((result) =>
      result.url().includes(`/projects/${project}/agents`)
      && ['POST', 'PUT'].includes(result.request().method())
    );
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    const saved = await response;
    expect(saved.ok(), await saved.text()).toBeTruthy();
    const agent = (await saved.json()).data;
    await expect(dialog).not.toBeVisible();
    await page.reload();
    const card = page.locator('.agents-page .v-card').filter({ hasText: name });
    await expect(card).toContainText(agent.configuration.model);
    await card.getByRole('button', { name: '编辑', exact: true }).click();
    await expect(dialog.getByRole('button', { name: '保存', exact: true })).toBeEnabled();
    await expect(dialog.getByText(agent.configuration.harness === 'codex' ? 'Codex' : 'Claude Code', { exact: true })).toBeVisible();
    await expect(dialog.getByLabel('角色设定（可留空）')).toHaveValue(role);
    return agent;
  }

  await selectHarness('Codex');
  await dialog.locator('.v-select').filter({ hasText: '模型' }).getByRole('combobox').click();
  await expect(page.getByRole('option', { name: 'codex-ci-fixture', exact: true })).toBeVisible();
  await expect(page.getByRole('option', { name: 'DeepSeek V4.1 Flash', exact: true })).toHaveCount(0);
  await page.getByRole('option', { name: 'codex-ci-fixture', exact: true }).click();
  const original = await save();
  expect(original.configuration).toMatchObject({ harness: 'codex', model: 'codex-ci-fixture', body: role });

  await selectHarness('Claude Code');
  await dialog.locator('.v-select').filter({ hasText: '模型' }).getByRole('combobox').click();
  await expect(page.getByRole('option', { name: 'DeepSeek V4.1 Flash', exact: true })).toBeVisible();
  await expect(page.getByRole('option', { name: 'codex-ci-fixture', exact: true })).toHaveCount(0);
  await page.getByRole('option', { name: 'DeepSeek V4.1 Flash', exact: true }).click();
  const claude = await save();
  expect(claude.id).toBe(original.id);
  expect(claude.configuration).toMatchObject({ harness: 'claude-code', model: 'deepseek-flash', body: role });

  await selectHarness('Codex');
  const restored = await save();
  expect(restored.id).toBe(original.id);
  expect(restored.configuration).toMatchObject({ harness: 'codex', model: 'codex-ci-fixture', body: role });
});
