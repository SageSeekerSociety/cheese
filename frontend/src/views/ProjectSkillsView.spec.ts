/**
 * 工作方法：芝士整理或改过的要人确认；芝士的改动可以整个放弃，回到正在用的那一版；
 * 旧版本能恢复。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ProjectSkillsView from './ProjectSkillsView.vue'

import i18n, { setLocale } from '@/i18n'

const routeQuery: Record<string, string> = {}
vi.mock('vue-router', () => ({ useRoute: () => ({ query: routeQuery }) }))

// 确认框回什么由这一格决定：`wait` 解出真就是人点了确定。
const dialog = vi.hoisted(() => ({ confirm: vi.fn() }))
vi.mock('@/plugins/dialog', async () => ({
  ...(await vi.importActual<typeof import('@/plugins/dialog')>('@/plugins/dialog')),
  useDialog: () => ({ confirm: dialog.confirm }),
}))

vi.mock('../api', () => ({ listTopics: vi.fn() }))
vi.mock('../api/projectSkills', () => ({
  listProjectSkills: vi.fn(),
  getProjectSkill: vi.fn(),
  createProjectSkill: vi.fn(),
  updateProjectSkill: vi.fn(),
  confirmProjectSkill: vi.fn(),
  declineProjectSkill: vi.fn(),
  restoreProjectSkill: vi.fn(),
  deleteProjectSkill: vi.fn(),
}))

const { listTopics } = await import('../api')
const { confirmProjectSkill, getProjectSkill, listProjectSkills, restoreProjectSkill } = await import(
  '../api/projectSkills'
)

const vuetify = createVuetify({ components, directives })

afterEach(cleanup)

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
    })
  }
})

const content = {
  title: '项目周报',
  description: '把一周进展整理成一页周报',
  inputs: '时间范围',
  steps: '数字都标来源',
  outputs: '一页 markdown',
  files: {},
}
const base = {
  ...content,
  project_id: 'p1',
  name: 'weekly-report',
  proposed_by: 'cheese-x',
  confirmed_by: 'u1',
  confirmed_at: '2026-09-25T00:00:00Z',
  source_topic_id: 'room-1',
  proposal: null,
  created_at: '2026-09-25T00:00:00Z',
  updated_at: '2026-09-25T00:00:00Z',
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.clearAllMocks()
  // 默认「点了确定」：取消那一格在下面的用例里单独摆。
  dialog.confirm.mockImplementation(() => ({ wait: async () => true }))
  for (const k of Object.keys(routeQuery)) delete routeQuery[k]
  vi.mocked(listTopics).mockResolvedValue({
    data: [{ id: 'room-1', title: '周报房间', status: 'active' }],
    total: 1,
  } as never)
  vi.mocked(listProjectSkills).mockResolvedValue({
    data: [
      { ...base, id: 'new-1', name: 'summary', title: '芝士刚整理的摘要方法', state: 'draft', shipped_revision: 0 },
      { ...base, id: 'edit-1', title: '被芝士改过的周报', state: 'draft', shipped_revision: 2 },
      { ...base, id: 'live-1', name: 'standup', title: '站会纪要', state: 'active', shipped_revision: 1 },
    ],
    total: 3,
  })
})

function mount() {
  return render(ProjectSkillsView, { props: { projectId: 'p1' }, global: { plugins: [vuetify, i18n] } })
}

function buttonIn(scope: Element, label: string): HTMLElement | undefined {
  return Array.from(scope.querySelectorAll('button')).find((b) => b.textContent?.trim() === label)
}

const row = (c: Element, id: string) => c.querySelector(`[data-skill="${id}"]`)!

describe('工作方法', () => {
  it('从房间的「去确认」点进来，那一条被指出来', async () => {
    routeQuery.skill = 'new-1'
    const { container } = mount()
    await waitFor(() => expect(row(container, 'new-1').classList.contains('row--focus')).toBe(true))
    expect(row(container, 'live-1').classList.contains('row--focus')).toBe(false)
  })

  it('芝士整理的方法要人点确认才保存', async () => {
    vi.mocked(confirmProjectSkill).mockResolvedValue({
      ...base,
      id: 'new-1',
      title: '芝士刚整理的摘要方法',
      state: 'active',
      shipped_revision: 1,
    })
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('等你确认'))
    expect(buttonIn(row(container, 'live-1'), '确认保存')).toBeUndefined()

    await fireEvent.click(buttonIn(row(container, 'new-1'), '确认保存')!)

    expect(confirmProjectSkill).toHaveBeenCalledWith('new-1')
  })

  it('放弃芝士的改动，回到正在用的那一版', async () => {
    vi.mocked(restoreProjectSkill).mockResolvedValue({ ...base, id: 'edit-1', state: 'active', shipped_revision: 2 })
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('被芝士改过的周报'))

    await fireEvent.click(buttonIn(row(container, 'edit-1'), '放弃改动')!)

    // 改动丢了找不回来：行里的入口是灰的，这一下确认才是红的。
    expect(dialog.confirm).toHaveBeenCalledWith('改动作废，回到正在用的那一版；这一版改动找不回来。', {
      title: '放弃对「被芝士改过的周报」的改动？',
      confirmLabel: '放弃改动',
      danger: true,
    })
    expect(restoreProjectSkill).toHaveBeenCalledWith('edit-1', 2)
  })

  it('放弃要人点过确认才动：说取消，那一版改动还在', async () => {
    dialog.confirm.mockImplementation(() => ({ wait: async () => false }))
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('被芝士改过的周报'))

    await fireEvent.click(buttonIn(row(container, 'edit-1'), '放弃改动')!)

    expect(dialog.confirm).toHaveBeenCalledTimes(1)
    expect(restoreProjectSkill).not.toHaveBeenCalled()
    expect(container.textContent).toContain('被芝士改过的周报')
  })

  it('历史版本里能把旧的一版恢复回来', async () => {
    vi.mocked(getProjectSkill).mockResolvedValue({
      ...base,
      id: 'live-1',
      state: 'active',
      shipped_revision: 2,
      revisions: [
        {
          revision: 2,
          content: { ...content, steps: '新规则' },
          confirmed_by: 'u1',
          note: '修改',
          created_at: '2026-09-25T01:00:00Z',
        },
        { revision: 1, content, confirmed_by: 'u1', note: '创建', created_at: '2026-09-25T00:00:00Z' },
      ],
    })
    vi.mocked(restoreProjectSkill).mockResolvedValue({ ...base, id: 'live-1', state: 'active', shipped_revision: 3 })
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('站会纪要'))

    await fireEvent.click(buttonIn(row(container, 'live-1'), '历史版本')!)
    await waitFor(() => expect(document.body.querySelector('[data-revision="1"]')).toBeTruthy())
    await fireEvent.click(buttonIn(document.body.querySelector('[data-revision="1"]')!, '恢复到这一版')!)

    expect(restoreProjectSkill).toHaveBeenCalledWith('live-1', 1)
  })
})
