/**
 * 频道页对话旁边一块一块的：进来是概览；支线、文件两块先是列表，选中一样在同一块里显示它，
 * 「返回」回到列表；对话里点开的一份文件、文档、技能开在文件那一块里，和支线那一块并排；在频道里
 * 打开的一件任务开在支线那一处，换掉支线，再要支线时任务关掉；例行事务开在文件那一处；全关掉时
 * 对话占满。
 *
 * 用的是真的摆法（`useRoomLayout`）和真的工作面板；外壳是一个最小的替身：没藏起来的那几块就是在
 * 屏幕上的，每一块有一个标题栏落点（概览没有标题栏）。断言的是读者看到的那几块。
 */
import type { PagePanels, PagePanelsHandle } from '@/layouts/pagePanels'
import type { RoomWorkTab } from '@/views/workspace/roomLayout'
import type { Topic } from '../../cx_types'
import type { WorkTabMeta } from '../work/workLayout'

import { computed, defineComponent, h, ref, shallowRef } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '@/i18n'

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getTopicWorkSummary: vi.fn().mockResolvedValue(null),
    listRoomAttachments: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listRoomFiles: vi.fn().mockResolvedValue({ data: [], total: 0, truncated: false }),
    listRoomOutputs: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listRoomDocuments: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  }
})
vi.mock('../../api/routines', async () => ({
  ...(await vi.importActual<typeof import('../../api/routines')>('../../api/routines')),
  listProjectRoutines: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  listRoutineTemplates: vi.fn().mockResolvedValue({ data: [], total: 0 }),
}))

import { workPaneId } from '../work/workLayout'
import WorkPanel from '../WorkPanel.vue'

import { useRoomLayout } from '@/views/workspace/roomLayout'

const room = { id: 'c1', project_id: 'p1', title: '验证', status: 'active' } as Topic

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})
beforeEach(() => {
  setLocale('zh-CN')
  document.querySelectorAll('[data-head]').forEach((el) => el.remove())
})

interface PanelApi {
  select: (key: string) => void
  openSkill: (skill: { id: string; title: string }) => Promise<void>
  closeTab: (key: string) => void
  fixedMeta: WorkTabMeta[]
  fileMeta: WorkTabMeta[]
}

/** The channel page beside the conversation: the real arrangement and work panel, on a stand-in shell. */
function mountChannel() {
  const panel = ref<PanelApi | null>(null)
  const deliveries = ref<{ id: string; title: string }[]>([])
  const workHidden = ref(false)
  const closedTasks: string[] = []
  const heads = new Map<string, HTMLElement>()
  const spec = shallowRef<() => PagePanels | null>(() => null)
  const declared = computed(() => spec.value()?.panels ?? [])
  const inSight = computed(() => new Set(declared.value.filter((one) => !one.hidden).map((one) => one.id)))
  /** A block's title bar, while it is on screen; 概览 has none. */
  function head(key: string): HTMLElement | undefined {
    const block = declared.value.find((one) => one.id === key)
    if (!block || block.hidden || block.bare) return undefined
    let el = heads.get(key)
    if (!el) {
      el = document.createElement('div')
      el.dataset.head = key
      document.body.appendChild(el)
      heads.set(key, el)
    }
    return el
  }
  const handle: PagePanelsHandle = {
    active: computed(() => true),
    outlet: () => undefined,
    headOutlet: head,
    maximized: computed(() => null),
    maximize: () => {},
    restore: () => {},
    visible: inSight,
    show: () => {},
    rearranged: computed(() => false),
    reset: () => {},
  }
  const Host = defineComponent(() => {
    function tabs(): RoomWorkTab[] {
      const api = panel.value
      return [
        ...(api?.fixedMeta ?? []),
        ...deliveries.value.map((d) => ({
          key: `delivery:${d.id}`,
          title: d.title,
          icon: 'mdi-file-eye-outline',
          transient: true,
        })),
        ...(api?.fileMeta ?? []).map((tab) => ({ ...tab, transient: true })),
      ]
    }
    const layout = useRoomLayout(
      (s) => {
        spec.value = s
        return handle
      },
      {
        kind: () => 'room',
        tabs,
        workHidden: () => workHidden.value,
        floating: () => false,
        hide: { label: () => '收起', run: () => (workHidden.value = true) },
        onActivated: () => {},
        onClose: (key) => panel.value?.closeTab(key),
        onKeep: () => {},
        onSpawn: () => {},
        scope: () => 'channel',
      }
    )
    return () =>
      h(
        WorkPanel,
        {
          ref: panel,
          topic: room,
          activityTick: 0,
          deliveries: deliveries.value,
          layout: layout.workLayout.value,
          onCloseDelivery: (id: string) => {
            closedTasks.push(id)
            deliveries.value = deliveries.value.filter((d) => d.id !== id)
          },
        },
        {
          overview: () => h('p', '概览的内容'),
          threads: () => h('p', '支线的列表'),
          delivery: ({ id }: { id: string }) => h('p', `任务 ${id} 的内容`),
          skill: ({ id }: { id: string }) => h('p', `技能 ${id} 的内容`),
        }
      )
  })
  render(Host, { global: { plugins: [createVuetify({ components, directives }), i18n] } })
  /** The ✕ on a block's title bar. */
  const close = (key: string) => spec.value()?.onClose?.(key)
  return { panel, deliveries, workHidden, closedTasks, close, bar: (key: string) => heads.get(key)! }
}

/** Whether a block's content is on screen. */
const shown = (key: string) => {
  const pane = document.getElementById(workPaneId(key))
  return !!pane && !/display:\s*none/.test(pane.getAttribute('style') ?? '')
}
/** The blocks on screen now, by name. */
function blocksInSight(): string[] {
  return ['overview', 'threads', 'files', 'routines', 'delivery:k1', 'file:skill:s1'].filter(shown)
}

describe('beside the channel conversation, one block per kind of thing', () => {
  it('entering the channel shows 概览 alone', async () => {
    mountChannel()
    await waitFor(() => expect(blocksInSight()).toEqual(['overview']))
    expect(screen.getByText('概览的内容')).toBeTruthy()
  })

  it('opening 支线 makes 概览 step aside and shows its list under a title bar of its own', async () => {
    const { panel, bar } = mountChannel()
    await waitFor(() => expect(panel.value).toBeTruthy())
    panel.value!.select('threads')
    await waitFor(() => expect(blocksInSight()).toEqual(['threads']))
    expect(within(bar('threads')).getByText('支线')).toBeTruthy()
  })

  it('a link to a skill opens it in the 文件 block beside 支线, and back returns that block to the list', async () => {
    const { panel, bar } = mountChannel()
    await waitFor(() => expect(panel.value).toBeTruthy())
    panel.value!.select('threads')
    await waitFor(() => expect(blocksInSight()).toEqual(['threads']))
    await panel.value!.openSkill({ id: 's1', title: '写周报' })
    await waitFor(() => expect(blocksInSight()).toEqual(['threads', 'file:skill:s1']))
    expect(screen.getByText('技能 s1 的内容')).toBeTruthy()

    await fireEvent.click(within(bar('file:skill:s1')).getByRole('button', { name: i18n.global.t('global.back') }))
    await waitFor(() => expect(blocksInSight()).toEqual(['threads', 'files']))
    expect(screen.queryByText('技能 s1 的内容')).toBeNull()
  })

  it('a task opened in the channel takes the place of 支线; asking for 支线 again closes the task', async () => {
    const { panel, deliveries, closedTasks, bar } = mountChannel()
    await waitFor(() => expect(panel.value).toBeTruthy())
    panel.value!.select('threads')
    await waitFor(() => expect(blocksInSight()).toEqual(['threads']))
    deliveries.value = [{ id: 'k1', title: '报名表改短' }]
    panel.value!.select('delivery:k1')
    await waitFor(() => expect(blocksInSight()).toEqual(['delivery:k1']))
    expect(screen.getByText('任务 k1 的内容')).toBeTruthy()
    // Its title bar is the task's own head, with no back: its ✕ closes it.
    expect(within(bar('delivery:k1')).queryByRole('button', { name: i18n.global.t('global.back') })).toBeNull()

    panel.value!.select('threads')
    await waitFor(() => expect(blocksInSight()).toEqual(['threads']))
    expect(closedTasks).toEqual(['k1'])
  })

  it('例行事务 opens where 文件 is, in its place', async () => {
    const { panel } = mountChannel()
    await waitFor(() => expect(panel.value).toBeTruthy())
    panel.value!.select('files')
    await waitFor(() => expect(blocksInSight()).toEqual(['files']))
    panel.value!.select('routines')
    await waitFor(() => expect(blocksInSight()).toEqual(['routines']))
  })

  it('closing every block leaves the conversation alone', async () => {
    const { panel, workHidden, close } = mountChannel()
    await waitFor(() => expect(panel.value).toBeTruthy())
    panel.value!.select('threads')
    await waitFor(() => expect(blocksInSight()).toEqual(['threads']))
    panel.value!.select('files')
    await waitFor(() => expect(blocksInSight()).toEqual(['threads', 'files']))
    close('threads')
    await waitFor(() => expect(blocksInSight()).toEqual(['files']))
    close('files')
    await waitFor(() => expect(blocksInSight()).toEqual([]))
    expect(workHidden.value).toBe(true)
  })
})
