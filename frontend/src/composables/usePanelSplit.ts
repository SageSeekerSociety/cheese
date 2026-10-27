// 右侧面板并排看两格：此刻这一格在左，上一格在右。
//
// 读者对照两份东西（新旧两版文档、方案和表格、页面和它的说明）时，来回切页签每次
// 都要重新找位置。并排时两格都挂着、都算「在看」（各自照常取数、跟随），右边那一格
// 点它的页签就换到左边。手机上面板这一格只有一列宽，不并排；面板窄于 MIN_WIDTH 也
// 收回一列。
import type { Ref } from 'vue'

import { computed, ref, watch } from 'vue'

export const SPLIT_MIN_WIDTH = 720

export function usePanelSplit(
  active: Ref<string>,
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

  const canSplit = computed(
    () => allowed.value && !!previous.value && previous.value !== active.value && opts.exists(previous.value)
  )
  function toggle() {
    split.value = split.value ? null : canSplit.value ? previous.value : null
  }
  /** 这一格此刻画不画：左边那格，或者并排时右边那格。 */
  function shown(key: string): boolean {
    return active.value === key || split.value === key
  }
  /** 右边那一格被关掉了：收回一列。 */
  function drop(key: string) {
    if (split.value === key) split.value = null
    if (previous.value === key) previous.value = null
  }
  return { split, canSplit, toggle, shown, drop }
}
