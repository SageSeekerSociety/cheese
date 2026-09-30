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

vi.mock('../api', () => ({
  listProjectSkills: vi.fn(),
  listTopics: vi.fn(),
  getProjectSkill: vi.fn(),
  createProjectSkill: vi.fn(),
  updateProjectSkill: vi.fn(),
  confirmProjectSkill: vi.fn(),
  restoreProjectSkill: vi.fn(),
  deleteProjectSkill: vi.fn(),
}))

const { confirmProjectSkill, getProjectSkill, listProjectSkills, listTopics, restoreProjectSkill } = await import(
  '../api'
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
  created_at: '2026-09-25T00:00:00Z',
  updated_at: '2026-09-25T00:00:00Z',
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.clearAllMocks()
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

// These assertions read the Chinese copy; the English rendering is checked in its own case.
beforeEach(() => setLocale('zh-CN'))

describe('工作方法', () => {
  it('reads in English under the en locale', async () => {
    setLocale('en')
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('Waiting for you to confirm'))
    expect(buttonIn(row(container, 'new-1'), 'Confirm and save')).toBeTruthy()
    expect(row(container, 'new-1').textContent).toContain('written up by')
    expect(row(container, 'edit-1').textContent).toContain('version 2 is in use')
    expect(row(container, 'live-1').textContent).toContain('version 1')
  })

  it('一行说清是谁整理、谁确认的', async () => {
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('站会纪要'))
    expect(row(container, 'new-1').textContent).toMatch(/summary · 由\s*\S*cheese-x\S*\s*整理/)
    expect(row(container, 'live-1').textContent).toMatch(/standup · 第 1 版 · /)
  })

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

    expect(restoreProjectSkill).toHaveBeenCalledWith('edit-1', 2)
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
