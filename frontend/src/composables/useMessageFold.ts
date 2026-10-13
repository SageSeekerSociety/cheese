import type { Ref } from 'vue'

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { foldHeight, overflowsFold } from '../lib/chatFold'
import { cancelMeasure, observeSize, queueMeasure } from '../lib/foldMeasure'

// 一段很长的正文收成一段阅读高度，底下给一颗「展开 / 收起」。要不要收，看渲染后的
// 高度（lib/chatFold），不看字数：代码块、表格、图片的高度都不是字数能推出来的。
//
// 频道里的一条队友回复和支线顶部那条初始消息共用它：两处的正文不一样（一条是消息
// 行，一条是页头挂着的那条消息），但「收不收、收多高、按钮怎么切」是同一件事，判据
// 和量高都在下面这两个库里，这里只把状态和生命周期收在一起。
//
// `el` 是**收起来的那一层**（`max-height` 写在它身上）。量的是它的第一个元素子节点
// ——正文那一层：收起来的那层高度被钉死，栏窄下来也不动，观察不到「换个宽度就不用
// 收了」；正文那一层不收，它一直报真实高度。
//
// `canFold` 说这一条现在收不收：还没落库的那条（正在写、正在送）不收，它的正文每帧
// 都在长。
//
// `toggleEl` 是那颗「展开 / 收起」（要的时候才给）：展开后它在正文末尾，正文高过一屏时
// 就掉到视野外，收回来得靠它——展开完把它带进视野。频道消息行没传，那一颗跟着行一起长。
export function useMessageFold(
  el: Ref<HTMLElement | null>,
  canFold: Ref<boolean>,
  revision?: Ref<unknown>,
  toggleEl?: Ref<HTMLElement | null>
) {
  const foldable = ref(false)
  const expanded = ref(false)
  const foldPx = ref(0)
  let unobserveFold: (() => void) | null = null
  // 这一层在批次里的身份（lib/foldMeasure）。
  const owner = {}

  const clamped = computed(() => canFold.value && foldable.value && !expanded.value)

  function target(): HTMLElement | null {
    return (el.value?.firstElementChild as HTMLElement | null) ?? null
  }

  // 读和写分开排进同一帧的批次（lib/foldMeasure）：首屏几十行一起挂上来时，先全部读
  // 完布局再全部写，不在每一行之间逼浏览器重算一次布局。
  function measure() {
    queueMeasure(
      owner,
      () => {
        const node = target()
        if (!node || !canFold.value) return null
        const px = foldHeight(Number.parseFloat(getComputedStyle(node).lineHeight))
        return { px, overflows: overflowsFold(node.scrollHeight, px) }
      },
      (m) => {
        if (!m) {
          foldable.value = false
          return
        }
        foldPx.value = m.px
        foldable.value = m.overflows
      }
    )
  }

  function observe() {
    unobserveFold?.()
    unobserveFold = null
    const node = target()
    if (node && canFold.value) unobserveFold = observeSize(node, measure)
    measure()
  }

  /** 最近的、纵向能滚的祖先——频道里是对话栏的 `.messages`。 */
  function scrollParent(node: HTMLElement | null): HTMLElement | null {
    let cur = node?.parentElement ?? null
    while (cur) {
      const overflowY = getComputedStyle(cur).overflowY
      if (overflowY === 'auto' || overflowY === 'scroll') return cur
      cur = cur.parentElement
    }
    return null
  }

  // 展开/收起只改这一条自己的高度，它上面的东西一个像素都没动，滚到哪就还是哪。
  // 会动的只剩一种情况：Chrome 的滚动锚定，或壳里的 ResizeObserver 顺手挪了一下。
  // 量这一行的顶、把差补回去。停在底部的人交给壳钉底，不抢。
  // 展开以后那颗「收起」在正文末尾：正文高过一屏时它掉到视野外，点开的人就找不回来了，
  // 所以补完滚动把它带进视野（已经在视野里就不动）。收起来的时候不动，那一颗就在原地。
  function toggle() {
    const row = (el.value?.closest('.im-row') as HTMLElement | null) ?? el.value
    const scroller = scrollParent(el.value)
    const beforeTop = row?.getBoundingClientRect().top ?? 0
    const pinned = scroller ? scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80 : false
    expanded.value = !expanded.value
    void nextTick(() => {
      requestAnimationFrame(() => {
        if (row && scroller && !pinned) scroller.scrollTop += row.getBoundingClientRect().top - beforeTop
        if (expanded.value) toggleEl?.value?.scrollIntoView?.({ block: 'nearest' })
      })
    })
  }

  onMounted(() => {
    observe()
    void nextTick(observe)
  })
  // `el` 从 null 变成元素（正文是异步取来的）、`canFold` 翻转、正文换了，都要重新量。
  watch([el, canFold], () => void nextTick(observe))
  if (revision) watch(revision, () => void nextTick(observe))
  onBeforeUnmount(() => {
    unobserveFold?.()
    cancelMeasure(owner)
  })

  return { foldable, expanded, clamped, foldPx, toggle }
}
