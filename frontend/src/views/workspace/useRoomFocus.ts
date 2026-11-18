// 频道页和私聊页的「专注模式」：工作面板铺满整个布局区，对话和侧栏都让开；再按一下回到
// 原来的摆法和宽度。铺满是外壳布局区的「放大」（layouts/pagePanels.ts），只在这一次
// 浏览里，不记住。
//
// 对话让开了，可人还在这件事里：铺满的这段时间里对话有新回复，退出铺满那颗按钮上带一个点。
import type { ComputedRef, Ref } from 'vue'
import type { PagePanelsHandle } from '@/layouts/pagePanels'

import { computed, ref, watch } from 'vue'

import { useCommands } from '@/commands'
import { t } from '@/i18n'

/** 工作面板在布局区里的那一块。 */
export const WORK_PANEL = 'work'

export function useRoomFocus(opts: {
  panels: PagePanelsHandle
  /** 面板并排开着（不是浮层，也没收起）：只有这时才铺得满。 */
  docked: Ref<boolean>
}) {
  const focusMode: ComputedRef<boolean> = computed(() => opts.panels.maximized.value === WORK_PANEL)

  // 面板一收起、或换成浮层，铺满就不成立了。
  watch(
    opts.docked,
    (on) => {
      if (!on && focusMode.value) opts.panels.restore()
    },
    { immediate: true }
  )

  const chatNews = ref(false)
  watch(focusMode, (on) => {
    if (!on) chatNews.value = false
  })
  function noteChatNews() {
    if (focusMode.value) chatNews.value = true
  }

  function toggleFocus() {
    if (focusMode.value) opts.panels.restore()
    else opts.panels.maximize(WORK_PANEL)
  }
  function leaveFocus() {
    if (focusMode.value) opts.panels.restore()
  }
  // 铺满那颗按钮的图标和说法，跟着现在是哪一档变。
  const focusIcon = computed(() => (focusMode.value ? 'mdi-arrow-collapse' : 'mdi-arrow-expand'))
  const focusLabel = computed(() => (focusMode.value ? t('work.room.menu.exitFocus') : t('work.room.menu.focus')))
  /** 这颗按钮能不能按：外壳在给这一页排版、面板并排开着。 */
  const canFocus = computed(() => opts.panels.active.value && opts.docked.value)

  useCommands(() =>
    canFocus.value ? [{ id: 'room.focus', title: focusLabel.value, icon: focusIcon.value, run: toggleFocus }] : []
  )

  return { focusMode, canFocus, chatNews, noteChatNews, toggleFocus, leaveFocus, focusIcon, focusLabel }
}
