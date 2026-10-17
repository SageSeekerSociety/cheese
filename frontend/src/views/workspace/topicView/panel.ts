// 右侧工作面板的收 / 开（三档见 useTopicPanel）。
//
// 支线开着时右边那一半是它，关掉支线就回到频道；其余情况收起面板、焦点回到打开它的那
// 颗开关（键盘和读屏用户必须回得去）。窄档里二级侧栏浮层也可能同时开着，两层共用一个
// Esc 栈：一下 Esc 只关最上面那层（后打开的那层）。见 useEscapeStack。
import type { Ref } from 'vue'

import { computed, nextTick } from 'vue'

import { useEscapeLayer } from '@/composables/useEscapeStack'
import { useTopicPanel } from '@/composables/useTopicPanel'

import { t } from '@/i18n'

export function useTopicPanelState(opts: {
  /** 主区（对话加面板）那一块。 */
  panes: Ref<HTMLElement | null>
  /** 地址里要看的那一格，以及写或清它的那一下。 */
  tab: Ref<string | undefined>
  setTab: (tab: string | undefined) => void
  /** 手机和平板竖放没有并排这回事，面板是页签里的一格。 */
  desktop: Ref<boolean>
  /** 支线开着时它占着右边这一块。 */
  thread: Ref<boolean>
  /** 面板此刻在画哪一格：收起再打开回到它。 */
  showing: () => string | undefined
  /** 支线页上收起面板等于离开支线，回频道。 */
  backToRoom: () => void
}) {
  const panel = useTopicPanel({
    panes: opts.panes,
    tab: opts.tab,
    desktop: opts.desktop,
    thread: opts.thread,
    setTab: opts.setTab,
    showing: opts.showing,
  })
  const open = panel.open
  const float = panel.float

  function close() {
    if (opts.thread.value) return opts.backToRoom()
    panel.hide()
    // Esc 关掉浮层，焦点回到打开它那颗开关。
    void nextTick(() => document.querySelector<HTMLElement>('[data-panel-toggle]')?.focus())
  }
  function toggle() {
    if (open.value) close()
    else panel.show()
  }
  // 浮层按 Esc 收起，焦点回到页头那颗开关。
  useEscapeLayer(
    computed(() => float.value && open.value),
    close
  )

  const closeLabel = computed(() => t('work.room.panel.close'))

  return { open, float, close, toggle, closeLabel, show: panel.show }
}
