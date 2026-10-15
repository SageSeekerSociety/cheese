// 房间里提到一条规则的那一行 → 右栏那一格：这条线接得上吗。
//
// 两个断点值得各钉一条：
//
// 1. 「例行事务」那一格是**第一次被选中时才挂上**的（`mounted`）。人从聊天里那一行点
//    进来时它可能从来没打开过，那一刻模板 ref 还是空的 —— 先指、后开，这一次点击就落
//    进空气里，而且没有报错。所以 `showRoutine` 必须先等 `setTab` 走完。
// 2. 换完那一格要真的把编号递下去。`setTab` 自己只管换页签，「指到哪一条」在那一格
//    里面（`RoutinePanelHost.showRoutine`）。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { defineComponent, h, nextTick, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

vi.mock('../api', () => ({
  getPreview: vi.fn(async () => null),
  getTopicWorkSummary: vi.fn(async () => ({ has_run: false, changed_files: [] })),
  listRoomTasks: vi.fn(async () => ({ data: [], total: 0 })),
}))

// 那一格本身在这一份里不是被测的对象（它有自己的 spec）：这里只记「有没有人指过来、
// 指的是哪一条」。**记得到就是证明**：那一格没挂上时模板 ref 是空的，`?.` 会把这一次
// 调用整个吞掉，什么也记不下来。
const pointed = vi.hoisted(() => ({ ids: [] as string[], records: [] as string[] }))

vi.mock('./routine/RoutinePanelHost.vue', () => ({
  __esModule: true,
  default: defineComponent({
    name: 'RoutinePanelHostStub',
    setup(_props, { expose }) {
      expose({
        showRoutine: (id: string) => pointed.ids.push(id),
        showRoutineRecords: (id: string) => pointed.records.push(id),
      })
      return () => h('div', { 'data-testid': 'routine-host' })
    },
  }),
}))

import WorkPanel from './WorkPanel.vue'

const Panel = WorkPanel as unknown as Component

const topic = {
  id: 't1',
  project_id: 'p1',
  parent_id: null,
  title: '产品',
  kind: 'root',
  status: 'active',
  created_by: 'u',
  created_at: '2026-08-18T00:00:00Z',
  updated_at: '2026-08-18T00:00:00Z',
} as Topic

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

beforeEach(() => {
  pointed.ids.length = 0
  pointed.records.length = 0
})

interface PanelApi {
  showRoutine: (routineId: string) => Promise<void>
  showRoutineRecords: (routineId: string) => Promise<void>
  activeTab: () => string
}

/** 拿着面板 `defineExpose` 出来的那几样从外面调 —— 就像 `TopicView` 那样。 */
function mountPanel(tab?: ReturnType<typeof ref<string | undefined>>) {
  let panel: PanelApi | null = null
  const Harness = defineComponent({
    setup: () => () =>
      h(Panel, {
        topic,
        activityTick: 0,
        tab: tab?.value,
        'onUpdate:tab': (key: string) => {
          if (tab) tab.value = key
        },
        ref: (el: unknown) => {
          panel = el as PanelApi | null
        },
      }),
  })
  render(Harness, {
    global: {
      plugins: [vuetify, i18n],
      stubs: { PanelSiteHost: true, PanelChangesHost: true, PanelPreviewHost: true },
    },
  })
  return {
    get panel(): PanelApi {
      return panel as PanelApi
    },
  }
}

describe('房间里那一行指到一条规则', () => {
  it('那一格还没打开过：先把它打开，再指过去', async () => {
    const { panel } = mountPanel()
    await nextTick()
    // 一次都没打开过：这一格还没挂上，此刻指过去只会落进空气里。
    expect(pointed.ids).toEqual([])
    expect(panel.activeTab()).not.toBe('routines')

    await panel.showRoutine('r-9')
    await waitFor(() => expect(pointed.ids).toEqual(['r-9']))
    expect(panel.activeTab()).toBe('routines')
    // 先开、后指：指过去的时候那一格已经在场上了（没在场就一条也记不下来）。
    expect(document.querySelector('[data-testid="routine-host"]')).toBeTruthy()
  })

  it('地址里已经写着这一格：不用再挂一次，照样指得到', async () => {
    const tab = ref<string | undefined>('routines')
    const { panel } = mountPanel(tab)
    await waitFor(() => expect(document.querySelector('[data-testid="routine-host"]')).toBeTruthy())

    await panel.showRoutine('r-1')
    await waitFor(() => expect(pointed.ids).toEqual(['r-1']))
  })

  it('第二颗按钮走的是另一条：开记录整页，不是指到行上', async () => {
    const { panel } = mountPanel()
    await nextTick()

    await panel.showRoutineRecords('r-9')
    await waitFor(() => expect(pointed.records).toEqual(['r-9']))
    // 两条路不互相串：看记录这件事不该顺带把行选中、展开。
    expect(pointed.ids).toEqual([])
    expect(panel.activeTab()).toBe('routines')
  })
})
