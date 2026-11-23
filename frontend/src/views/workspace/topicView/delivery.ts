// 频道右栏里打开的那几件任务，以及右栏此刻开着哪一格。
//
// 桌面上，在频道里打开一件任务是右栏里多出的一格，写着任务名。频道页上右栏一次只显示一格
// （`single`）：打开另一件换掉这一件，「返回」回到打开它之前那一格。右栏是浮层（主区太窄）时
// 它自己画页签：打开另一件再多一格，放不下时那一排横着滚。地址（`reviewTaskId`）只记正选中的那
// 一件：开着哪几格是这一页的状态，不进地址，刷新之后只回来地址上那一件。浮层的页签里一格可以
// 开着而没被选中（交付在你看着别的页签时到了，页签上带一个点），离开它去别的页签它也还在。支线
// 留在「支线」那一格里。手机上没有右栏，这些都是整页。
import type { Ref } from 'vue'
import type { RoomTask } from '@/cx_types'
import type { TopicPlace } from './shared'

import { computed, ref, watch } from 'vue'

/** 一格任务页签在右栏里的键：`delivery:<任务 id>`。 */
export const DELIVERY_PREFIX = 'delivery:'
export function deliveryKey(taskId: string): string {
  return DELIVERY_PREFIX + taskId
}

export function useDeliveryTab(opts: {
  place: () => TopicPlace
  desktop: Ref<boolean>
  /** 地址里的 `?tab=`。 */
  urlTab: Ref<string | undefined>
  /** 右栏此刻开着没有。 */
  panelOpen: Ref<boolean>
  /** 右栏此刻在画哪一格。 */
  activeTab: () => string | undefined
  me: () => string
  /** 这个频道里还开着的任务，现读一次（卡上的审阅人以它为准）。 */
  readOpenTasks: () => Promise<RoomTask[]>
  /** 去这件任务的地址（选中它那一格）。 */
  openDelivery: (taskId: string) => void
  /** 停在频道的地址上换一格。 */
  setTab: (key: string) => void
  /** 从任务或支线的地址回到频道的地址，开着 `key` 那一格。 */
  toChannelTab: (key: string) => void
  /** 正开着的那一件又交了一版：它那一格重读。 */
  refreshDelivery: (taskId: string) => void
  /** 右栏一次只显示一格、没有页签（频道页的桌面并排）。 */
  single: () => boolean
}) {
  /** 开着的那几格，按打开的先后。 */
  const tabs = ref<{ taskId: string; dot: boolean }[]>([])
  function add(taskId: string, dot: boolean) {
    // 一次只显示一格：打开的这一件换掉开着的那一件，不在看不见的地方攒着。
    if (opts.single()) {
      if (tabs.value.length !== 1 || tabs.value[0]!.taskId !== taskId) tabs.value = [{ taskId, dot: false }]
      return
    }
    const at = tabs.value.findIndex((tab) => tab.taskId === taskId)
    if (at < 0) tabs.value = [...tabs.value, { taskId, dot }]
    else if (tabs.value[at]!.dot !== dot) tabs.value = tabs.value.map((tab, i) => (i === at ? { ...tab, dot } : tab))
  }
  watch(
    [() => opts.place().reviewTaskId, opts.desktop],
    ([taskId, desktop]) => {
      if (taskId && desktop) add(taskId, false)
    },
    { immediate: true }
  )

  /** 右栏开着哪一格：在一件任务的地址上是它那一格，在一条支线里是「支线」，其余照地址。 */
  const shownTab = computed(() => {
    const { reviewTaskId, threadId } = opts.place()
    if (opts.desktop.value && reviewTaskId) return deliveryKey(reviewTaskId)
    if (opts.desktop.value && threadId) return 'threads'
    return opts.urlTab.value
  })

  /** 右栏里换一格。任务页签和支线各有自己的地址。 */
  function select(key: string) {
    if (!opts.desktop.value) return opts.setTab(key)
    const { reviewTaskId, threadId } = opts.place()
    if (key.startsWith(DELIVERY_PREFIX)) {
      const taskId = key.slice(DELIVERY_PREFIX.length)
      if (reviewTaskId !== taskId && tabs.value.some((tab) => tab.taskId === taskId)) opts.openDelivery(taskId)
      return
    }
    if (threadId && key === 'threads') return
    if (reviewTaskId || threadId) return opts.toChannelTab(key)
    opts.setTab(key)
  }

  /** 关掉一格。正看着它时去哪一格由右栏定（旁边那一格，或打开它之前那一格）。 */
  function close(taskId: string) {
    tabs.value = tabs.value.filter((tab) => tab.taskId !== taskId)
  }

  /** 已经为它打开过、或加过一格的那几版（卡的 id）：一版只动一次。 */
  const handled = new Set<string>()

  /**
   * 页面开着时频道主线上来了一行关于这件任务的新内容：平台记的「已提交审阅」，或者开这件任务的
   * 队友带着它说的话。它有一版在等我审阅、右栏开着、停在「概览」时，直接打开它；右栏是带页签的
   * 浮层、停在别的页签时，把它那一格加进那一排、不选中，带一个点（一次只显示一格时没有那一排，
   * 不动它）；右栏收着时不动它。同一版只这样做一次，
   * 之后关于它的话不再打开。刷新时已经在主线上的旧内容不算（对话栏只报实时到的那一行）。
   */
  async function arrived(taskId: string) {
    if (!opts.desktop.value || opts.place().taskId) return
    if (tabs.value.some((tab) => tab.taskId === taskId)) opts.refreshDelivery(taskId)
    // 到达那一刻的样子：等下面读回任务行的这段时间里人可能已经换了页签。
    const open = opts.panelOpen.value
    const { reviewTaskId, threadId } = opts.place()
    const onOverview = !reviewTaskId && !threadId && opts.activeTab() === 'overview'
    if (!open) return
    let rows: RoomTask[]
    try {
      rows = await opts.readOpenTasks()
    } catch {
      return
    }
    const card = rows.find((row) => row.id === taskId)?.card
    const me = opts.me()
    if (!card || card.status !== 'pending' || !me || !(card.reviewer ?? '').split('、').includes(me)) return
    if (handled.has(card.id)) return
    handled.add(card.id)
    if (onOverview) return opts.openDelivery(taskId)
    // 正看着的那一件不带点；没有页签的右栏没有地方挂点。
    if (reviewTaskId === taskId || opts.single()) return
    add(taskId, true)
  }

  /** 选中了的那一格不再带点。 */
  watch(
    () => opts.place().reviewTaskId,
    (taskId) => {
      if (taskId && tabs.value.some((tab) => tab.taskId === taskId && tab.dot)) add(taskId, false)
    }
  )

  return { tabs, shownTab, select, close, arrived }
}
