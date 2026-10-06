// 话题页右侧面板开着还是收着，以及它是并排还是浮层（三档见
// `useWorkspaceLayout.ts` 的 `panelMode`）。
//
// 地址里的 `?tab=` 是「有人要看这一格」：对话里点「查看改动」、别人发来的链接都走
// 它，它总能把面板打开。并排时，「概览」那颗开关记下这个人自己的选择；浮层不记，
// 开关只是写或清 `?tab=`。
import type { Ref } from 'vue'

import { computed } from 'vue'
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
