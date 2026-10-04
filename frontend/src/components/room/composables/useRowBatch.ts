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

import { computed, nextTick, ref, watch } from 'vue'

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
  /** 时间线现在每一行的 id，按顺序（ChatTimeline 拿到的 rows）。 */
  rowIds: () => string[]
  /** 全部挂上之后：比如行太少撑不满一屏时，这时候再去要更早一页。 */
  onDone?: () => void
}

export function useRowBatch(deps: RowBatchDeps) {
  // 记的是「第一条已经挂上的行」的 id，不是一个行数：分批期间时间线还会变——新消息接在
  // 末尾、刷新回来的那一页整个换掉窗口。按 id 算，末尾长出来的行不会把上面挂好的行顶回
  // 去；这一行不在窗口里了（窗口被换掉），就当全部挂上，绝不会出现一行都不画。
  const firstShown = ref<string | null>(null)
  /** 开头还没挂上的行数。ChatTimeline 跳过前这么多行。 */
  const hidden = computed(() => {
    if (firstShown.value === null) return 0
    return Math.max(0, deps.rowIds().indexOf(firstShown.value))
  })
  const pending = computed(() => hidden.value > 0)
  // 每次开始、每次揭开全部 +1：上一轮还在路上的那一步认得出自己过期了。
  let generation = 0

  /** 往上补到只剩 `keep` 行没挂（0 = 全部挂上），屏幕上的内容不动。 */
  async function revealTo(keep: number, gen: number) {
    const el = deps.scrollRef.value
    const before = el ? { scrollTop: el.scrollTop, scrollHeight: el.scrollHeight } : null
    const stayBottom = deps.atBottom.value
    firstShown.value = keep > 0 ? deps.rowIds()[keep] ?? null : null
    await nextTick()
    if (gen !== generation) return
    const sc = deps.scrollRef.value
    if (!sc || !before) return
    if (stayBottom) sc.scrollTop = sc.scrollHeight
    // 这期间已经有人动过滚动位置（跳到某一条的 scrollIntoView、浏览器自己的滚动锚定），
    // 那一下才是对的，不拿挂行之前量的数去盖掉它。
    else if (sc.scrollTop === before.scrollTop) sc.scrollTop = scrollTopAfterPrepend(before, sc.scrollHeight)
  }

  async function run(gen: number) {
    while (hidden.value > 0) {
      await yieldToMain()
      if (gen !== generation) return
      await revealTo(Math.max(0, hidden.value - BATCH_ROWS), gen)
      if (gen !== generation) return
    }
    firstShown.value = null
    deps.onDone?.()
  }

  /** 这一屏要停在底部：先只挂最后一截，其余的分批补上。 */
  function start() {
    generation += 1
    const ids = deps.rowIds()
    const cut = ids.length - INITIAL_ROWS
    firstShown.value = cut > 0 ? ids[cut] : null
    if (cut > 0) void run(generation)
  }

  /** 马上全部挂上（换话题、要跳到某一条之前）。 */
  function revealAll() {
    generation += 1
    firstShown.value = null
  }

  // 人在补完之前往上翻了：一口气全部挂上（位置照补），之后记下的滚动位置才是相对整条
  // 时间线的。否则这时离开再回来，useChatScroll 记的偏移量少算了没挂的那一截。
  watch(deps.atBottom, (bottom) => {
    if (bottom || !pending.value) return
    generation += 1
    void revealTo(0, generation)
  })

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
