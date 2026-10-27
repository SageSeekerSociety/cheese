// 右侧面板并排看两格：此刻这一格在左，上一格在右。
//
// 读者对照两份东西（新旧两版文档、方案和表格、页面和它的说明）时，来回切页签每次
// 都要重新找位置。并排时两格都挂着、都算「在看」（各自照常取数、跟随），右边那一格
// 点它的页签就换到左边。手机上面板这一格只有一列宽，不并排；面板窄于 MIN_WIDTH 也
// 收回一列。
import type { Ref } from 'vue'

import { computed, ref, watch, watchEffect } from 'vue'

export const SPLIT_MIN_WIDTH = 720

export function usePanelSplit(
  active: Ref<string>,
  /** `exists` 要读响应式的页签表：一格不经「关掉」没了时，并排靠它重算才收得回来。 */
  opts: { width: Ref<number>; phone: Ref<boolean>; exists: (key: string) => boolean }
) {
  const split = ref<string | null>(null)
  const previous = ref<string | null>(null)
  const allowed = computed(() => !opts.phone.value && opts.width.value >= SPLIT_MIN_WIDTH)

  watch(active, (now, before) => {
    // 点了右边那一格的页签：两格换个边，而不是把右边那格丢掉。
    if (split.value === now) split.value = before ?? null
    if (before && before !== 'chat') previous.value = before
  })
  watch(allowed, (ok) => {
    if (!ok) split.value = null
  })
  // 一格可能不经「关掉」就没了：没钉住的临时文件页签会被下一份文件原地顶掉。右边那格
  // 指着一个已经不在的页签时收回一列，不留一栏空白。
  watchEffect(() => {
    if (split.value && !opts.exists(split.value)) split.value = null
    if (previous.value && !opts.exists(previous.value)) previous.value = null
  })

  const canSplit = computed(
    () => allowed.value && !!previous.value && previous.value !== active.value && opts.exists(previous.value)
  )
  function toggle() {
    if (split.value) split.value = null
    else if (canSplit.value) split.value = previous.value
  }
  /** 这一格此刻画不画：左边那格，或者并排时右边那格。 */
  function shown(key: string): boolean {
    return active.value === key || split.value === key
  }
  /** 右边那一格的样子：排在后面、带分隔线（WorkPanel 里 `.tabpane--side`）。 */
  function sideClass(key: string): string | undefined {
    return split.value === key ? 'tabpane--side' : undefined
  }
  /** 右边那一格被关掉了：收回一列。 */
  function drop(key: string) {
    if (split.value === key) split.value = null
    if (previous.value === key) previous.value = null
  }
  return { split, canSplit, toggle, shown, sideClass, drop }
}
