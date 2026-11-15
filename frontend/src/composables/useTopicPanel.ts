// 话题页右侧面板开着还是收着，以及它是并排还是浮层（三档见
// `useWorkspaceLayout.ts` 的 `panelMode`）。
//
// 地址里的 `?tab=` 是「有人要看这一格」：对话里点「查看改动」、别人发来的链接都走
// 它，它总能把面板打开。并排时，「概览」那颗开关记下这个人自己的选择；浮层不记，
// 开关只是写或清 `?tab=`。
//
// `?tab=` 打开的浮层是压在对话上的一张纸，对话看不见。所以主区变窄、并排换成浮层时，
// 原来并排开着的那一格要让开：地址里那一格留着（换回宽档还开着它），但浮层不因此
// 自己拉开。不然打开个人芝士（它自己占掉右边一条）时，对话会被一块面板和一层遮罩盖住。
// 主动要看的（人自己点了那一格、点了「查看改动」）照旧拉开——那是刚发生的一次点击，
// 不是「上次看过的那一格」。
import type { Ref } from 'vue'

import { computed, watch } from 'vue'
import { useElementSize } from '@vueuse/core'

import { panelMode, panelOpenByDefault } from '@/composables/useWorkspaceLayout'

import { useWorkspaceStore } from '@/stores/workspace'

export function useTopicPanel(opts: {
  /** 主区（对话加面板）那一块。 */
  panes: Ref<HTMLElement | null>
  /** 地址里要看的那一格。 */
  tab: Ref<string | undefined>
  /** 手机和平板竖放没有并排这回事，面板是页签里的一格。 */
  desktop: Ref<boolean>
  /** 支线开着时它占着右边这一块。 */
  thread: Ref<boolean>
  /** 写或清地址里的那一格。 */
  setTab: (tab: string | undefined) => void
  /** 面板此刻在画哪一格：收起再打开回到它。 */
  showing: () => string | undefined
}) {
  const store = useWorkspaceStore()
  const { width } = useElementSize(opts.panes)
  const float = computed(() => opts.desktop.value && panelMode(width.value) === 'float')
  const open = computed(() => {
    if (!opts.desktop.value || opts.thread.value || opts.tab.value) return true
    if (float.value) return false
    return store.panelPref ?? panelOpenByDefault(width.value)
  })

  // 并排换成浮层的那一下（主区变窄：助手面板打开、窗口缩小）：原来并排开着的那一格
  // 让开，不变成盖在对话上的浮层。地址里那一格也清掉，理由和 hide() 一样——不清的话
  // 下次「查看改动」会因为「已经在那一格」什么都不做。
  //
  // 只在换档这一下让：已经在浮层里点开一格（人自己点的那一下）照常开。
  watch(float, (now, before) => {
    if (!now || before) return
    if (opts.tab.value) opts.setTab(undefined)
  })
  function show() {
    if (!float.value) store.setPanelPref(true)
    else opts.setTab(opts.showing() ?? 'overview')
  }
  function hide() {
    if (!float.value) store.setPanelPref(false)
    // 清掉地址里那一格：不然下一次「查看改动」会因为「已经在那一格」什么都不做。
    if (opts.tab.value) opts.setTab(undefined)
  }
  function toggle() {
    if (open.value) hide()
    else show()
  }

  return { float, open, show, hide, toggle }
}
