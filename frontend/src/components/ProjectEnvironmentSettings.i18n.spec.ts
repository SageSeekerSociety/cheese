// 环境设置区块的中英对照。
// ProjectEnvironmentSettings.spec.ts 管行为（保存、日志、只读），这里管用户屏幕上看到的字：
// 整块英文下不许出现汉字，状态文案和时间格式要跟着界面语言走。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '../i18n'

const api = vi.hoisted(() => ({
  getProjectEnvironment: vi.fn(),
  getRoomEnvironment: vi.fn(),
  saveProjectEnvironment: vi.fn(),
  applyRoomEnvironment: vi.fn(),
}))
vi.mock('../api', () => api)

import ProjectEnvironmentSettings from './ProjectEnvironmentSettings.vue'

const CJK = /[㐀-䶿一-鿿豈-﫿]/
const config = { setup_script: 'echo setup', startup_script: 'echo startup', variables: {}, revision: 'revision-first' }

beforeEach(() => {
  vi.resetAllMocks()
  setLocale('zh-CN')
  api.getProjectEnvironment.mockResolvedValue({
    config,
    can_edit: true,
    rooms: [{ id: 'r', title: 'Room', revision: config.revision }],
  })
  api.getRoomEnvironment.mockResolvedValue({
    state: 'failed',
    stage: 'startup',
    // 退出码只跟在脚本的报错后面显示，没有它就看不到这段。
    error: 'command failed',
    pinned_revision: config.revision,
    started_at: '2024-01-02T03:04:05Z',
    finished_at: '2024-01-02T03:05:05Z',
    log: 'log line',
    exit_code: 1,
  })
  api.saveProjectEnvironment.mockResolvedValue({ ...config, revision: 'revision-second' })
})
afterEach(cleanup)

function mount() {
  return render(ProjectEnvironmentSettings, {
    props: { projectId: 'p' },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

/** 模板里的换行缩进留在 textContent 里，比对前压成单空格。 */
const text = (element: Element) => (element.textContent ?? '').replace(/\s+/g, ' ')

it('讲中文：状态、阶段、时间都是中文', async () => {
  const view = mount()
  // 房间状态比整页晚一步到位，等它出现再整块看。
  await view.findByText(/准备失败/)
  const page = text(view.container)
  expect(page).toContain('准备失败 · 准备项目')
  expect(page).toContain('运行环境')
  expect(page).toContain('环境变量')
  expect(page).toContain('command failed')
  expect(page).toContain('（退出码 1）')
  // 时间跟界面语言走：中文是年在前。
  expect(page).toMatch(/开始：\d{4}\/\d{2}\/\d{2} \d{2}:\d{2}/)
})

it('英文下整块没有汉字，状态和阶段是英文', async () => {
  setLocale('en')
  const view = mount()
  await view.findByText(/Setup failed/)
  const page = text(view.container)
  expect(page).toContain('Setup failed · Preparing the project')
  expect(page).toContain('Runtime environment')
  expect(page).toContain('Environment variables')
  expect(page).toContain('Apply at the next start')
  expect(page).toContain('command failed')
  expect(page).toContain('(exit code 1)')
  // 时间跟界面语言走：英文是年在后。
  expect(page).toMatch(/Started: \d{2}\/\d{2}\/\d{4}/)
  expect(CJK.test(page)).toBe(false)
})

it('切语言后状态和阶段立刻跟着变 —— 它们不能是模块级常量表', async () => {
  const view = mount()
  await view.findByText(/准备失败/)
  setLocale('en')
  await view.findByText(/Setup failed/)
  expect(text(view.container)).toContain('Setup failed · Preparing the project')
  expect(CJK.test(text(view.container))).toBe(false)
})

it('变量名重复时报的是本地化文案', async () => {
  const view = mount()
  // 空名的不止一个：默认没有变量，点两次「添加变量」就重复了。
  const add = await view.findByRole('button', { name: '添加变量' })
  await fireEvent.click(add)
  await fireEvent.click(add)
  await fireEvent.click(await view.findByRole('button', { name: '保存配置' }))
  expect(await view.findByText('环境变量名称不能为空或重复')).toBeTruthy()
  expect(api.saveProjectEnvironment).not.toHaveBeenCalled()
})

it('变量名重复时，英文界面报英文', async () => {
  setLocale('en')
  const view = mount()
  const add = await view.findByRole('button', { name: 'Add variable' })
  await fireEvent.click(add)
  await fireEvent.click(add)
  await fireEvent.click(await view.findByRole('button', { name: 'Save configuration' }))
  expect(await view.findByText("Variable names can't be empty or repeated")).toBeTruthy()
  expect(api.saveProjectEnvironment).not.toHaveBeenCalled()
})
