// 打开话题时，时间线分批挂行：先挂最后 INITIAL_ROWS 行（就是第一屏看得见的那一截），
// 再每让出一次主线程往上补 BATCH_ROWS 行，直到全部挂上。
//
// 一个往回翻过几页的话题，缓存窗口里能有几百条。一次性挂上去，每一行都是一个带
// Markdown 渲染的组件，首屏要等它们全部建完才画得出来；而人一进来看的只是底部那一
// 截。这里不是虚拟化：**每一行最终都在 DOM 里**，Ctrl+F、读屏、复制整段都照旧。
//
// 只在「这一次要停在底部」时才分批（`useChatPanel` 判断：没有指定跳到某一条、记下的
// 位置是底部或者没记过）。停在中间某处的话，记下的是距顶部的偏移，行没挂全时它指的
// 不是同一个位置，所以那种情况一次挂完，和原来一样。
//
// 往上补行时：人停在底部，壳的 ResizeObserver 会钉住底部（useChatScroll）；人在
// 这段时间里往上翻了，就按 loadOlder 的同一个办法把上面长出来的高度补回 scrollTop
// （lib/blockPaging 的 scrollTopAfterPrepend），屏幕上的内容一个像素都不动。
//
// 补完之前，凡是要按 id 找某一条（跳转、定位）的地方先 `revealAll()`；「滚到顶就加载
// 更早一页」也先等补完——顶上还有没挂的行，那不是真的顶。
import type { Ref } from 'vue'

import { computed, nextTick, ref } from 'vue'

import { scrollTopAfterPrepend } from '../../../lib/blockPaging'
import { whenIdle } from '../../../lib/idle'

export const INITIAL_ROWS = 30
export const BATCH_ROWS = 40

type SchedulerWindow = Window & { scheduler?: { yield?: () => Promise<void> } }

/** 让出主线程一次：有 `scheduler.yield()` 用它（排在输入事件后面、又不会被饿死），没有退回空闲回调。 */
function yieldToMain(): Promise<void> {
  const yielder = (window as SchedulerWindow).scheduler?.yield
  if (typeof yielder === 'function') return yielder.call((window as SchedulerWindow).scheduler)
  return new Promise((resolve) => whenIdle(resolve, 100))
}

export interface RowBatchDeps {
  scrollRef: Ref<HTMLElement | null>
  atBottom: Ref<boolean>
  /** useChatScroll 的同名函数：这个话题再打开时会不会停在底部。 */
  restoresToBottom: (topicId: string) => boolean
  /** 时间线现在一共多少行（ChatTimeline 拿到的 rows）。 */
  rowCount: () => number
  /** 全部挂上之后：比如行太少撑不满一屏时，这时候再去要更早一页。 */
  onDone?: () => void
}

export function useRowBatch(deps: RowBatchDeps) {
  /** 开头还没挂上的行数。ChatTimeline 跳过前这么多行。 */
  const hidden = ref(0)
  const pending = computed(() => hidden.value > 0)
  // 每次开始、每次揭开全部 +1：上一轮还在路上的那一步认得出自己过期了。
  let generation = 0

  async function run(gen: number) {
    while (hidden.value > 0) {
      await yieldToMain()
      if (gen !== generation) return
      const el = deps.scrollRef.value
      const before = el ? { scrollTop: el.scrollTop, scrollHeight: el.scrollHeight } : null
      const stayBottom = deps.atBottom.value
      hidden.value = Math.max(0, hidden.value - BATCH_ROWS)
      await nextTick()
      if (gen !== generation) return
      const sc = deps.scrollRef.value
      if (sc && before) sc.scrollTop = stayBottom ? sc.scrollHeight : scrollTopAfterPrepend(before, sc.scrollHeight)
    }
    deps.onDone?.()
  }

  /** 这一屏要停在底部：先只挂最后一截，其余的分批补上。 */
  function start() {
    generation += 1
    hidden.value = Math.max(0, deps.rowCount() - INITIAL_ROWS)
    if (hidden.value > 0) void run(generation)
  }

  /** 马上全部挂上（换话题、要跳到某一条之前）。 */
  function revealAll() {
    generation += 1
    hidden.value = 0
  }

  /** 打开一个话题：要停在底部才分批；停在中间或要跳到某一条，就一次挂完。 */
  function startFor(topicId: string, focus: string | null) {
    if (!focus && deps.restoresToBottom(topicId)) start()
    else revealAll()
  }

  /**
   * 要按 id 找某一条之前调：还没挂完就先全部挂上，等这一帧渲染完再跑 `then`，返回
   * true；已经挂完返回 false，调用方照常往下做。
   */
  function deferUntilRevealed(then: () => void): boolean {
    if (!pending.value) return false
    revealAll()
    void nextTick(then)
    return true
  }

  return { hidden, pending, start, startFor, revealAll, deferUntilRevealed }
}
