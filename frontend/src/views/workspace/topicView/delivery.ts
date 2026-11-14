// 频道右栏里那一格交付页签，以及右栏此刻开着哪一格。
//
// 桌面上，在频道里审阅一件交付是右栏固定那几格右边多出的一格能关的页签，写着任务名；同一
// 时间只有一格，打开另一件就换成那一件。地址（`reviewTaskId`）选中它，页签本身不跟着地址
// 走：它可以开着而没被选中（交付在你看着别的页签时到了，页签上带一个点），离开它去别的
// 页签它也还在。支线也一样留在「支线」那一格里。手机上没有右栏，这些都是整页。
import type { Ref } from 'vue'
import type { RoomTask } from '@/cx_types'
import type { TopicPlace } from './shared'

import { computed, ref, watch } from 'vue'

export const DELIVERY_TAB = 'delivery'

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
  /** 去这件交付的地址（选中交付那一格）。 */
  openDelivery: (taskId: string) => void
  /** 停在频道的地址上换一格。 */
  setTab: (key: string) => void
  /** 从交付或支线的地址回到频道的地址，开着 `key` 那一格。 */
  toChannelTab: (key: string) => void
  /** 正开着的那一件交付又交了一版：它那一格重读。 */
  refreshDelivery: (taskId: string) => void
}) {
  const tab = ref<{ taskId: string; dot: boolean } | null>(null)
  watch(
    [() => opts.place().reviewTaskId, opts.desktop],
    ([taskId, desktop]) => {
      if (taskId && desktop) tab.value = { taskId, dot: false }
    },
    { immediate: true }
  )

  /** 右栏开着哪一格：在审阅一件交付是交付那一格，在一条支线里是「支线」，其余照地址。 */
  const shownTab = computed(() => {
    if (opts.desktop.value && opts.place().reviewTaskId) return DELIVERY_TAB
    if (opts.desktop.value && opts.place().threadId) return 'threads'
    return opts.urlTab.value
  })

  /** 右栏里换一格。交付那一格和支线各有自己的地址。 */
  function select(key: string) {
    if (!opts.desktop.value) return opts.setTab(key)
    const { reviewTaskId, threadId } = opts.place()
    if (key === DELIVERY_TAB) {
      if (tab.value && reviewTaskId !== tab.value.taskId) opts.openDelivery(tab.value.taskId)
      return
    }
    if (threadId && key === 'threads') return
    if (reviewTaskId || threadId) return opts.toChannelTab(key)
    opts.setTab(key)
  }

  function close() {
    tab.value = null
  }

  /**
   * 页面开着时频道主线上来了一行新交付。等的是我，右栏开着、停在「概览」时，直接打开它；
   * 右栏开着但在别的页签时，把交付那一格加进那一排、不选中，带一个点；右栏收着时不动它。
   * 刷新时已经在主线上的旧交付不算（对话栏只报实时到的那一行）。
   */
  async function arrived(taskId: string) {
    if (!opts.desktop.value || opts.place().taskId) return
    if (tab.value?.taskId === taskId) opts.refreshDelivery(taskId)
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
    if (onOverview) return opts.openDelivery(taskId)
    // 正看着的那一件交付不换走。
    if (reviewTaskId && tab.value?.taskId === reviewTaskId) return
    if (tab.value?.taskId === taskId) return
    tab.value = { taskId, dot: true }
  }

  return { tab, shownTab, select, close, arrived }
}
