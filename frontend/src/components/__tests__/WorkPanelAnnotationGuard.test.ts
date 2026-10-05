/* eslint-disable vue/one-component-per-file -- 测试里要同时摆下宿主与几个桩组件 */
/** 工作面板的空闲入口（`defineExpose` 上那几个：掀开总览、开一份文件）不是用户主动
 * 离开正在标注的那张图，绕过它去问「放弃标注?」是错的；而被拦下来之后，也不能把没
 * 落地的后续动作当成做过了。
 *
 * 两件事钉在这里：
 *   1. `pulse` / `highlightTurn` / `reviewDoc` 是「把总览里某样东西掀到眼前」，图那
 *      一格用 `v-show` 留着、笔画不会丢——所以它们不该走守卫，也不该留下一个空转的
 *      后续动作。
 *   2. `openFile` 是用户点了一份文件，该问；但问不到「可以走」时，它得当场放弃，不能
 *      转手去动一份没露面的「改动」。
 */
import type { Component } from 'vue'
import type { Topic } from '../../cx_types'

import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

const confirmDiscard = vi.fn()

vi.mock('../panels/preview/annotationDiscard', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../panels/preview/annotationDiscard')>()
  return { ...actual, confirmAnnotationDiscard: (...a: unknown[]) => confirmDiscard(...a) }
})

vi.mock('../../api', () => ({
  getPreview: vi.fn(async () => null),
  getTopicWorkSummary: vi.fn(async () => ({ changed_files: ['a.py'], has_run: true })),
  listRoomTasks: vi.fn(async () => ({ data: [], total: 0 })),
  // 不是房间文件：`openFile` 于是落到「改动」那一格。
  readPreviewFile: vi.fn(async () => {
    throw new Error('not a room file')
  }),
}))

vi.mock('../../api/routines', () => ({
  listProjectRoutines: vi.fn(async () => ({ data: [], total: 0 })),
  getRoutine: vi.fn(),
  createRoutine: vi.fn(),
  updateRoutine: vi.fn(),
  routineAction: vi.fn(),
  deleteRoutine: vi.fn(),
}))

import WorkPanel from '../WorkPanel.vue'

const topic = {
  id: 't1',
  project_id: 'p1',
  parent_id: null,
  title: 't',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-08-18T00:00:00Z',
  updated_at: '2026-08-18T00:00:00Z',
} as Topic

let vuetify: ReturnType<typeof createVuetify>
beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

const overview = { pulse: vi.fn(), highlightTurn: vi.fn(), reviewEdits: vi.fn() }
const changes = { openFile: vi.fn() }

const PanelOverviewStub = defineComponent({
  name: 'PanelOverviewHost',
  setup(_, { expose }) {
    expose({
      pulse: () => overview.pulse(),
      highlightTurn: (turnId: string) => overview.highlightTurn(turnId),
      reviewEdits: (request: unknown) => overview.reviewEdits(request),
    })
    return () => h('div', { class: 'stub-overview' })
  },
})
const PanelChangesStub = defineComponent({
  name: 'PanelChanges',
  setup(_, { expose }) {
    expose({ openFile: (path: string, taskId?: string) => changes.openFile(path, taskId) })
    return () => h('div', { class: 'stub-changes' })
  },
})

interface PanelApi {
  pulse: () => void
  highlightTurn: (turnId: string) => void
  reviewDoc: (request: unknown) => void
  openFile: (path: string, taskId?: string | null) => void
}

function harness() {
  let panel: PanelApi | null = null
  const tabs: string[] = []
  const Host = defineComponent({
    render() {
      return h(WorkPanel, {
        ref: (instance: unknown) => {
          panel = instance as PanelApi | null
        },
        topic,
        activityTick: 0,
        // 面板把「这一步走去哪一格」报上来；包了一层组件，事件得自己接住。
        'onUpdate:tab': (key: string) => tabs.push(key),
      })
    },
  })
  const ui = render(Host as Component, {
    global: {
      plugins: [vuetify, i18n],
      stubs: {
        PanelOverviewHost: PanelOverviewStub,
        PanelChanges: PanelChangesStub,
        PanelSite: true,
        PanelPreview: true,
        RoutinePanelHost: true,
      },
    },
  })
  return { ui, panel: () => panel as PanelApi, currentTab: () => tabs.at(-1) }
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

beforeEach(() => {
  confirmDiscard.mockReset()
  overview.pulse.mockReset()
  overview.highlightTurn.mockReset()
  overview.reviewEdits.mockReset()
  changes.openFile.mockReset()
})

describe('后台掀开总览不弹框、也不空转', () => {
  it('pulse：有未发标注也照样掀开，不去问人', async () => {
    confirmDiscard.mockResolvedValue(true)
    const { ui, panel, currentTab } = harness()
    await flush()
    // 先离开总览，落到「现场」——这样「掀回总览」才是一次真的切换。
    await fireEvent.click(await ui.findByRole('tab', { name: /现场/ }))
    await waitFor(() => expect(currentTab()).toBe('site'))

    // 此刻有人在标注：守卫会拦。
    confirmDiscard.mockResolvedValue(false)
    confirmDiscard.mockClear()
    await panel().pulse()
    await flush()

    expect(confirmDiscard).not.toHaveBeenCalled()
    expect(currentTab()).toBe('overview')
    // 不是空操作：总览那东西真被点动了。
    expect(overview.pulse).toHaveBeenCalledTimes(1)
  })

  it('highlightTurn / reviewDoc 同理', async () => {
    confirmDiscard.mockResolvedValue(true)
    const { ui, panel, currentTab } = harness()
    await flush()
    await fireEvent.click(await ui.findByRole('tab', { name: /现场/ }))
    await waitFor(() => expect(currentTab()).toBe('site'))

    confirmDiscard.mockResolvedValue(false)
    confirmDiscard.mockClear()
    await panel().highlightTurn('turn-1')
    await flush()
    expect(confirmDiscard).not.toHaveBeenCalled()
    expect(overview.highlightTurn).toHaveBeenCalledWith('turn-1')

    await panel().reviewDoc({ requester: 'me', edits: [{ block: 'b1', summary: 's' }] })
    await flush()
    expect(overview.reviewEdits).toHaveBeenCalledTimes(1)
  })
})

describe('开文件被拦下时不装作开了', () => {
  it('被拦 → 留在原地，也不去动那份没露面的「改动」', async () => {
    confirmDiscard.mockResolvedValue(true)
    const { ui, panel, currentTab } = harness()
    await flush()
    // 先让「改动」挂上（去过一次，它就一直是挂着的）——正是这样，才可能被偷偷去动。
    await fireEvent.click(await ui.findByRole('tab', { name: /改动/ }))
    await waitFor(() => expect(currentTab()).toBe('changes'))
    await fireEvent.click(await ui.findByRole('tab', { name: /现场/ }))
    await waitFor(() => expect(currentTab()).toBe('site'))

    // 用户点了一份文件，而此刻有人在标注 → 守卫问一句后说「保留」。
    changes.openFile.mockClear()
    confirmDiscard.mockClear()
    confirmDiscard.mockResolvedValue(false)
    await panel().openFile('src/a.ts')
    await flush()

    expect(confirmDiscard).toHaveBeenCalledTimes(1)
    expect(currentTab()).toBe('site')
    expect(changes.openFile).not.toHaveBeenCalled()
  })

  it('没被拦 → 切到改动并把文件交给它', async () => {
    confirmDiscard.mockResolvedValue(true)
    const { ui, panel, currentTab } = harness()
    await flush()
    // 让「改动」先挂上，再看它这一步有没有真的把文件交过去。
    await fireEvent.click(await ui.findByRole('tab', { name: /改动/ }))
    await waitFor(() => expect(currentTab()).toBe('changes'))
    await fireEvent.click(await ui.findByRole('tab', { name: /现场/ }))
    await waitFor(() => expect(currentTab()).toBe('site'))
    changes.openFile.mockClear()

    await panel().openFile('src/a.ts')
    await flush()

    expect(currentTab()).toBe('changes')
    expect(changes.openFile).toHaveBeenCalledWith('src/a.ts', undefined)
  })
})
