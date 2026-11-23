/**
 * 桌面上工作面板的每一格是布局区里的一块：此刻在看哪一格，不管是谁定的（地址、关掉正看着的
 * 任务、规则的链接），布局区都要把那一格翻到前面。这里的布局区是一个最小的替身：它只记着
 * 「此刻翻到前面的是哪一格」，面板照它决定哪一格显示。断言的是读者看到的那一格内容。
 */
import type { Topic } from '../../cx_types'
import type { WorkLayout } from '../work/workLayout'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listFiles: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getTopicWorkSummary: vi.fn().mockResolvedValue(null),
    getAgentControl: vi.fn().mockResolvedValue({ id: null, connected: false, tasks: {} }),
    listRoomOutputs: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    readPreviewFile: vi.fn().mockRejectedValue(new Error('file not found')),
  }
})

vi.mock('../../api/routines', async () => ({
  ...(await vi.importActual<typeof import('../../api/routines')>('../../api/routines')),
  listProjectRoutines: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  listRoutineTemplates: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  getRoutine: vi.fn().mockRejectedValue(new Error('not found')),
}))

import WorkPanel from '../WorkPanel.vue'

const room = { id: 'topic-A', project_id: 'p1', title: '综合', status: 'active' } as Topic

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

/** A channel's work panel on the layout: each cell shows what the layout last brought forward. */
function mountChannel() {
  const front = ref<Set<string>>(new Set(['overview']))
  const tab = ref<string | undefined>(undefined)
  const deliveries = ref<{ id: string; title: string }[]>([])
  const routineId = ref<string | null>(null)
  const Host = defineComponent(() => () => {
    const layout: WorkLayout = {
      outlet: () => undefined,
      visible: front.value,
      show: (key) => (front.value = new Set([key])),
      single: false,
      head: () => undefined,
    }
    return h(
      WorkPanel,
      {
        topic: room,
        activityTick: 0,
        tab: tab.value,
        deliveries: deliveries.value,
        routineId: routineId.value,
        layout,
      },
      {
        overview: () => h('p', '概览的内容'),
        threads: () => h('p', '支线的内容'),
        delivery: ({ id }: { id: string }) => h('p', `任务 ${id} 的内容`),
      }
    )
  })
  render(Host, { global: { plugins: [createVuetify({ components, directives }), i18n] } })
  return { tab, deliveries, routineId, front }
}

const showing = (text: string) => screen.queryByText(text)?.closest('.work-pane') as HTMLElement | null
const visible = (text: string) => {
  const pane = showing(text)
  return !!pane && pane.style.display !== 'none'
}

describe('asking for a cell of the work panel brings it forward', () => {
  it('the tab the address names becomes the one shown', async () => {
    const { tab } = mountChannel()
    await waitFor(() => expect(visible('概览的内容')).toBe(true))
    tab.value = 'threads'
    await waitFor(() => expect(visible('支线的内容')).toBe(true))
    expect(visible('概览的内容')).toBe(false)
  })

  it('a task opened by its address is shown, not left behind the tab that was in front', async () => {
    const { tab, deliveries } = mountChannel()
    await waitFor(() => expect(visible('概览的内容')).toBe(true))
    deliveries.value = [{ id: 't1', title: '开放日流程页' }]
    tab.value = 'delivery:t1'
    await waitFor(() => expect(visible('任务 t1 的内容')).toBe(true))
    expect(visible('概览的内容')).toBe(false)
  })

  it('closing the task being looked at brings back the tab it was opened from', async () => {
    const { tab, deliveries } = mountChannel()
    tab.value = 'threads'
    await waitFor(() => expect(visible('支线的内容')).toBe(true))
    deliveries.value = [{ id: 't1', title: '开放日流程页' }]
    tab.value = 'delivery:t1'
    await waitFor(() => expect(visible('任务 t1 的内容')).toBe(true))
    deliveries.value = []
    await waitFor(() => expect(visible('支线的内容')).toBe(true))
  })

  it('a routine named by the address brings the routines cell forward', async () => {
    const { routineId, front } = mountChannel()
    await waitFor(() => expect(visible('概览的内容')).toBe(true))
    // 规则的链接走的是 `routineId`；那一格的内容自己去读规则，这里看布局区把它翻到了前面。
    routineId.value = 'r1'
    await waitFor(() => expect([...front.value]).toEqual(['routines']))
    expect(visible('概览的内容')).toBe(false)
  })
})

/** The channel page beside the conversation: one thing at a time, each with its own title bar. */
function mountPlace() {
  const front = ref<Set<string>>(new Set(['overview']))
  const tab = ref<string | undefined>(undefined)
  const deliveries = ref<{ id: string; title: string }[]>([])
  const heads = new Map<string, HTMLElement>()
  const head = (key: string) => {
    let el = heads.get(key)
    if (!el) {
      el = document.createElement('div')
      el.setAttribute('aria-label', `bar ${key}`)
      el.setAttribute('role', 'toolbar')
      document.body.appendChild(el)
      heads.set(key, el)
    }
    return el
  }
  const panel = ref<{ openSkill(skill: { id: string; title: string }): Promise<void> } | null>(null)
  const Host = defineComponent(() => () => {
    const layout: WorkLayout = {
      outlet: () => undefined,
      visible: front.value,
      show: (key) => (front.value = new Set([key])),
      single: true,
      head,
    }
    return h(
      WorkPanel,
      { ref: panel, topic: room, activityTick: 0, tab: tab.value, deliveries: deliveries.value, layout },
      {
        overview: () => h('p', '概览的内容'),
        threads: () => h('p', '支线的内容'),
        delivery: ({ id }: { id: string }) => h('p', `任务 ${id} 的内容`),
        skill: ({ id }: { id: string }) => h('p', `技能 ${id} 的内容`),
      }
    )
  })
  render(Host, { global: { plugins: [createVuetify({ components, directives }), i18n] } })
  return { tab, deliveries, panel, bar: (key: string) => screen.getByRole('toolbar', { name: `bar ${key}` }) }
}

describe('beside the conversation, one thing at a time', () => {
  afterEach(() => document.querySelectorAll('[role="toolbar"]').forEach((bar) => bar.remove()))

  it('back on something opened from a pane returns to that pane', async () => {
    const { tab, panel, bar } = mountPlace()
    tab.value = 'threads'
    await waitFor(() => expect(visible('支线的内容')).toBe(true))
    await panel.value!.openSkill({ id: 's1', title: '写周报' })
    await waitFor(() => expect(visible('技能 s1 的内容')).toBe(true))
    const back = within(bar('file:skill:s1')).getByRole('button', { name: i18n.global.t('global.back') })
    await fireEvent.click(back)
    await waitFor(() => expect(visible('支线的内容')).toBe(true))
    expect(screen.queryByText('技能 s1 的内容')).toBeNull()
  })

  it('back on something opened from a task in the channel returns to that task', async () => {
    const { tab, deliveries, panel, bar } = mountPlace()
    deliveries.value = [{ id: 't1', title: '开放日流程页' }]
    tab.value = 'delivery:t1'
    await waitFor(() => expect(visible('任务 t1 的内容')).toBe(true))
    await panel.value!.openSkill({ id: 's1', title: '写周报' })
    await waitFor(() => expect(visible('技能 s1 的内容')).toBe(true))
    await fireEvent.click(within(bar('file:skill:s1')).getByRole('button', { name: i18n.global.t('global.back') }))
    await waitFor(() => expect(screen.queryByText('技能 s1 的内容')).toBeNull())
    expect(visible('任务 t1 的内容')).toBe(true)
    expect(visible('概览的内容')).toBe(false)
  })

  it('opening a second thing replaces the first instead of keeping it out of sight', async () => {
    const { panel } = mountPlace()
    await panel.value!.openSkill({ id: 's1', title: '写周报' })
    await waitFor(() => expect(visible('技能 s1 的内容')).toBe(true))
    await panel.value!.openSkill({ id: 's2', title: '整理笔记' })
    await waitFor(() => expect(visible('技能 s2 的内容')).toBe(true))
    expect(screen.queryByText('技能 s1 的内容')).toBeNull()
  })

  it('a fixed pane carries its name and no back', async () => {
    const { bar } = mountPlace()
    await waitFor(() =>
      expect(within(bar('overview')).getByText(i18n.global.t('work.room.tabs.overview'))).toBeTruthy()
    )
    expect(within(bar('overview')).queryByRole('button', { name: i18n.global.t('global.back') })).toBeNull()
  })
})
