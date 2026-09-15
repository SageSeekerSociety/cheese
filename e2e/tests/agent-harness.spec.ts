import { test, expect } from '@playwright/test';
import { login, openFirstProject } from './helpers';

// 「用什么跑」是人自己挑的，模型跟着它筛 —— 约束的方向是 harness → model。
// 这条 spec 盯的就是这个方向：以前是反的，人选模型、harness 被倒推出来，于是
// 选 Codex 支持的模型就"变成"了 Codex 队友，没人挑过，界面上也没显示过。
test('the harness is chosen, and the model list follows it', async ({ page }, testInfo) => {
  await login(page);
  await openFirstProject(page);
  const project = page.url().match(/\/projects\/([0-9a-f-]{36})/)?.[1];
  expect(project).toBeTruthy();
  await page.goto(`/projects/${project}/agents`);
  const name = `Harness CI ${Date.now()}`;
  const role = 'Keep this role when changing the harness.';
  await page.getByRole('button', { name: '新建队友', exact: true }).first().click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('button', { name: '保存', exact: true })).toBeEnabled();
  await dialog.getByLabel('名字', { exact: true }).fill(name);
  await dialog.getByLabel('角色设定（可留空）').fill(role);

  const field = (label: string) => dialog.locator('.v-select').filter({ hasText: label });

  async function openOptions(label: string): Promise<string[]> {
    // Options are read page-wide, because Vuetify renders a select's menu into
    // the overlay container rather than inside the field. So the menu that was
    // just closed has to be GONE before the next one opens: while it fades out
    // its options are still in the DOM, and this returns both lists — 运行方式's
    // three harnesses arriving in what should be the model list.
    await expect(page.getByRole('option')).toHaveCount(0);
    await field(label).getByRole('combobox').click();
    await expect(page.getByRole('option').first()).toBeVisible();
    return (await page.getByRole('option').allInnerTexts()).map((text) => text.trim());
  }

  async function choose(label: string, option: string) {
    await openOptions(label);
    await page.getByRole('option', { name: option, exact: true }).click();
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
    // Reopening shows what was saved, both halves of it.
    await expect(field('运行方式')).toContainText(
      { 'claude-code': 'Claude Code', codex: 'Codex', pi: 'pi' }[agent.configuration.harness as string]!
    );
    await expect(dialog.getByLabel('角色设定（可留空）')).toHaveValue(role);
    return agent;
  }

  // Codex brings its own model list (AGENT_HARNESS_MODELS), so choosing it
  // narrows what can be picked — the constraint is visible before saving
  // rather than arriving as a refusal afterwards.
  await choose('运行方式', 'Codex');
  expect(await openOptions('模型')).toEqual(['codex-ci-fixture']);
  await page.keyboard.press('Escape');
  await choose('模型', 'codex-ci-fixture');
  const codex = await save();
  expect(codex.configuration).toMatchObject({ harness: 'codex', model: 'codex-ci-fixture', body: role });
  await dialog.screenshot({ path: testInfo.outputPath('codex-editor.png'), animations: 'disabled' });

  // pi speaks the platform gateway, so every model the project has is one it
  // can drive — and switching to it keeps the model already chosen rather than
  // resetting a decision the person made on purpose.
  await choose('运行方式', 'pi');
  expect(await openOptions('模型')).toContain('DeepSeek V4.1 Flash');
  await page.keyboard.press('Escape');
  const pi = await save();
  expect(pi.id).toBe(codex.id);
  expect(pi.configuration).toMatchObject({ harness: 'pi', model: 'codex-ci-fixture', body: role });
  await dialog.screenshot({ path: testInfo.outputPath('pi-editor.png'), animations: 'disabled' });

  await choose('运行方式', 'Claude Code');
  await choose('模型', 'DeepSeek V4.1 Flash');
  const claude = await save();
  expect(claude.id).toBe(codex.id);
  expect(claude.configuration).toMatchObject({ harness: 'claude-code', model: 'deepseek-flash', body: role });
  await dialog.screenshot({ path: testInfo.outputPath('claude-editor.png'), animations: 'disabled' });
});
