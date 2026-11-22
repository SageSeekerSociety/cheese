// 频道页、任务页和私聊页在桌面上怎么摆：对话是一块，工作面板的每一格各是一块，都交给外壳的
// 布局区（layouts/pagePanels.ts）。页签可以拖去和别的并排、拖进对话那一格，格子可以收起、
// 放大，摆法按页面的种类（频道 / 任务 / 私聊）记在这台设备上。
//
// 对话那一块和工作面板固定的那几格不能关（只能挪）；频道和私聊里对话独占一格时不画页签栏，
// 任务页上它和别的格子一样有页签栏（收起、放大）。工作面板那几格
// 同属 `work` 这一组：新打开的一格（一件任务、一份文件）落到这一组页签最多的那一格里，✕ 也只在
// 那一格上。「收起」（✕、页头的「概览」）收的是只装着这一组的那几格；挪进对话那一格的不跟着收。
//
// 放大（页签栏上的 ⤢）是布局层的；对话因此看不见的这段时间里有了新回复，那颗还原按钮上带一个
// 点（对话这一块的信号）。挪过摆法之后，「恢复默认摆法」在页头 ⋯ 和命令面板里。
import type { ComputedRef, Ref } from 'vue'
import type { LayoutNode } from '@/components/layout-area'
import type { WorkLayout, WorkTabMeta } from '@/components/work/workLayout'
import type { PagePanel, PagePanels, PagePanelsHandle } from '@/layouts/pagePanels'

import { computed, ref, watch } from 'vue'

import { useCommands } from '@/commands'
import { workPaneId } from '@/components/work/workLayout'
import { t } from '@/i18n'

/** 对话那一块。 */
export const CONVERSATION = 'conversation'
/** 工作面板那几格同属的一组。 */
export const WORK_GROUP = 'work'

/** 工作面板的一格，交给布局区时还要说的几样。 */
export interface RoomWorkTab extends WorkTabMeta {
  /** 随读者打开、关掉而来去的一格（一件打开的任务、一份文件）。 */
  transient?: boolean
  /** 里面有 iframe（预览）：挪到别的格子时不能重新加载。 */
  alwaysRender?: boolean
}

export interface RoomLayoutOptions {
  /** 这一页的摆法记在哪个键下（同一种页面共用）；null 是这一档不交给外壳（手机、平板）。 */
  kind: () => 'room' | 'task' | 'dm' | null
  /** 工作面板此刻有哪几格，按页签顺序。 */
  tabs: () => RoomWorkTab[]
  /** 工作面板那一侧收着（或是浮层）：只装着工作面板的格子藏起来。 */
  workHidden: () => boolean
  /** 主区太窄、工作面板是压在对话上的浮层：它自己排页签，不进布局区。 */
  floating: () => boolean
  /** 页签栏上 ✕ 的说法和动作。 */
  hide: { label: () => string; run: () => void }
  /** 读者在布局区里点了工作面板的一格；关掉了能关的一格；双击留下了一格。 */
  onActivated: (key: string) => void
  onClose: (key: string) => void
  onKeep: (key: string) => void
  /** 读者在「+」里选了能开好几格的那一种：页面新开一格。 */
  onSpawn: (key: string) => void
}

export function useRoomLayout(
  usePanels: (spec: () => PagePanels | null) => PagePanelsHandle,
  opts: RoomLayoutOptions
): {
  panels: PagePanelsHandle
  /** 交给工作面板的那几样；不交给外壳时是 null。 */
  workLayout: ComputedRef<WorkLayout | null>
  /** 对话那一块此刻在屏幕上（没交给外壳时总是）。 */
  conversationInSight: ComputedRef<boolean>
  /** 对话看不见的这段时间里来了新回复。 */
  noteChatNews: () => void
  /** 页头「概览」开合的那几块的 id：交给外壳时是工作面板在屏幕上的那几格，否则是整块面板。 */
  panelControls: ComputedRef<string>
} {
  const chatNews: Ref<boolean> = ref(false)

  function spec(): PagePanels | null {
    const kind = opts.kind()
    if (!kind) return null
    const hideAction = { icon: 'mdi-close', label: opts.hide.label(), run: opts.hide.run }
    const work: PagePanel[] = opts.tabs().map((tab) => ({
      id: tab.key,
      title: tab.title,
      icon: tab.icon,
      faint: tab.faint,
      hint: tab.hint,
      signal: tab.signal,
      temporary: tab.temporary,
      transient: tab.transient,
      beside: tab.beside,
      alwaysRender: tab.alwaysRender,
      spawns: tab.spawns,
      group: WORK_GROUP,
      // 固定的几格不能关（挪去哪都行）；能关的是打开的任务和文件，以及任务的「文件」那一格。
      closable: !!tab.transient || !!tab.closable,
      minWidth: 360,
      hidden: opts.workHidden(),
      actions: [hideAction],
    }))
    // 默认摆法里只有不能关的那几格：「文件」由读者打开（「+」、点一个文件链接）。
    const fixed = work.filter((panel) => !panel.closable && !panel.spawns).map((panel) => panel.id)
    // 没拖过的人：窄桌面对半分，宽屏（≥ 1440px）把多出来的那块给工作面板。
    const wide = window.innerWidth >= 1440
    const arrangement: LayoutNode = {
      direction: 'row',
      children: [
        { panels: [CONVERSATION], size: wide ? 45 : 50 },
        { panels: fixed, size: wide ? 55 : 50 },
      ],
    }
    return {
      key: kind,
      panels: [
        {
          id: CONVERSATION,
          title: t('work.room.tabs.chat'),
          icon: 'mdi-message-outline',
          minWidth: 480,
          // 频道和私聊：对话独占一格时不画页签栏，有别的页签拖进来才出现。任务页上对话那一格
          // 和别的格子一样有页签栏，能收起、放大。
          stripWhenShared: kind !== 'task',
          signal: chatNews.value ? { kind: 'dot' } : undefined,
        },
        ...work,
      ],
      arrangement,
      onActivated: (id) => id !== CONVERSATION && opts.onActivated(id),
      onClose: opts.onClose,
      onKeep: opts.onKeep,
      onSpawn: opts.onSpawn,
      // 私聊页没有页头那一行：对话自己的头和工作面板的页签栏并排，同高。
      tabStripHeight: kind === 'dm' ? 'var(--app-page-header-height)' : undefined,
    }
  }

  const panels = usePanels(spec)

  const workLayout = computed<WorkLayout | null>(() =>
    panels.active.value && !opts.floating()
      ? { outlet: (key) => panels.outlet(key), visible: panels.visible.value, show: (key) => panels.show(key) }
      : null
  )
  // 收着时说的是上一次在屏幕上的那几格（再打开回到的就是它们）；从没打开过时是第一格。
  let lastInSight: string[] = []
  const panelControls = computed(() => {
    if (!workLayout.value) return 'topic-panel'
    const keys = opts.tabs().map((tab) => tab.key)
    const inSight = keys.filter((key) => panels.visible.value.has(key))
    if (inSight.length) lastInSight = inSight
    const named = inSight.length ? inSight : lastInSight.filter((key) => keys.includes(key))
    return (named.length ? named : keys.slice(0, 1)).map(workPaneId).join(' ')
  })
  const conversationInSight = computed(() => !panels.active.value || panels.visible.value.has(CONVERSATION))
  watch(conversationInSight, (inSight) => {
    if (inSight) chatNews.value = false
  })

  // 命令面板里放大 / 还原读者此刻在看的那一格工作面板。
  const maximizedNow = computed(() => panels.maximized.value)
  useCommands(() => {
    if (!workLayout.value) return []
    const reset = panels.rearranged.value
      ? [{ id: 'room.resetLayout', title: t('shell.layout.reset'), icon: 'mdi-restore', run: () => panels.reset() }]
      : []
    const front = opts.tabs().find((tab) => panels.visible.value.has(tab.key))
    if (!front && !maximizedNow.value) return reset
    const restore = !!maximizedNow.value
    return [
      ...reset,
      {
        id: 'room.maximize',
        title: restore ? t('shell.layout.restore') : t('shell.layout.maximize'),
        icon: restore ? 'mdi-arrow-collapse' : 'mdi-arrow-expand',
        run: () => (restore ? panels.restore() : front && panels.maximize(front.key)),
      },
    ]
  })

  return {
    panels,
    workLayout,
    conversationInSight,
    panelControls,
    noteChatNews: () => {
      if (!conversationInSight.value) chatNews.value = true
    },
  }
}
