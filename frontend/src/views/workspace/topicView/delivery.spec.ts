// 交付到了、等的是我：右栏停在「概览」时直接打开它那一格；在别的页签时加进那一排、不选中、
// 带一个点；右栏收着、或者等的不是我时什么都不做。右栏里换页签时，交付和支线回到频道的地址。
import type { RoomTask } from '@/cx_types'
import type { TopicPlace } from './shared'

import { ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { useDeliveryTab } from './delivery'

function row(id: string, reviewer: string, status = 'pending'): RoomTask {
  return { id, card: { id: `c-${id}`, status, reviewer } } as unknown as RoomTask
}

function setup(over: { place?: TopicPlace; open?: boolean; active?: string; rows?: RoomTask[] } = {}) {
  const place = ref<TopicPlace>(over.place ?? { projectId: 'p1', topicId: 'c1' })
  const opened: string[] = []
  const tabs: string[] = []
  const channelTabs: string[] = []
  const refreshed: string[] = []
  const delivery = useDeliveryTab({
    place: () => place.value,
    desktop: ref(true),
    urlTab: ref<string | undefined>(undefined),
    panelOpen: ref(over.open ?? true),
    activeTab: () => over.active ?? 'overview',
    me: () => 'me',
    readOpenTasks: vi.fn(async () => over.rows ?? [row('k1', 'me')]),
    openDelivery: (id) => opened.push(id),
    setTab: (key) => tabs.push(key),
    toChannelTab: (key) => channelTabs.push(key),
    refreshDelivery: (id) => refreshed.push(id),
  })
  return { place, delivery, opened, tabs, channelTabs, refreshed }
}

describe('交付到了', () => {
  it('右栏停在「概览」：直接打开它那一格', async () => {
    const { delivery, opened } = setup()
    await delivery.arrived('k1')
    expect(opened).toEqual(['k1'])
  })

  it('右栏在别的页签：加进那一排，不选中，带一个点', async () => {
    const { delivery, opened } = setup({ active: 'files' })
    await delivery.arrived('k1')
    expect(opened).toEqual([])
    expect(delivery.tab.value).toEqual({ taskId: 'k1', dot: true })
  })

  it('右栏收着：不动它', async () => {
    const { delivery, opened } = setup({ open: false })
    await delivery.arrived('k1')
    expect(opened).toEqual([])
    expect(delivery.tab.value).toBeNull()
  })

  it('等的不是我：不动它', async () => {
    const { delivery, opened } = setup({ rows: [row('k1', 'bob')] })
    await delivery.arrived('k1')
    expect(opened).toEqual([])
    expect(delivery.tab.value).toBeNull()
  })

  it('请了几个人审、我是其中一个：照样打开', async () => {
    const { delivery, opened } = setup({ rows: [row('k1', 'bob、me')] })
    await delivery.arrived('k1')
    expect(opened).toEqual(['k1'])
  })

  it('正看着另一件交付：不换走它', async () => {
    const { delivery } = setup({
      place: { projectId: 'p1', topicId: 'c1', reviewTaskId: 'k0' },
      rows: [row('k1', 'me')],
    })
    await delivery.arrived('k1')
    expect(delivery.tab.value).toEqual({ taskId: 'k0', dot: false })
  })

  it('正开着的那一件又交了一版：它那一格重读', async () => {
    const { delivery, refreshed } = setup({ place: { projectId: 'p1', topicId: 'c1', reviewTaskId: 'k1' } })
    await delivery.arrived('k1')
    expect(refreshed).toEqual(['k1'])
  })
})

describe('右栏里换页签', () => {
  it('在交付的地址上点别的页签：回到频道的地址，开着那一格', () => {
    const { delivery, channelTabs, tabs } = setup({ place: { projectId: 'p1', topicId: 'c1', reviewTaskId: 'k1' } })
    expect(delivery.shownTab.value).toBe('delivery')
    delivery.select('files')
    expect(channelTabs).toEqual(['files'])
    expect(tabs).toEqual([])
  })

  it('在支线里点「支线」：留在这条支线上', () => {
    const { delivery, channelTabs, tabs } = setup({ place: { projectId: 'p1', topicId: 'c1', threadId: 'th1' } })
    expect(delivery.shownTab.value).toBe('threads')
    delivery.select('threads')
    expect(channelTabs).toEqual([])
    expect(tabs).toEqual([])
  })

  it('点没选中的交付页签：去它的地址', async () => {
    const { delivery, opened } = setup({ active: 'files' })
    await delivery.arrived('k1')
    delivery.select('delivery')
    expect(opened).toEqual(['k1'])
  })
})
