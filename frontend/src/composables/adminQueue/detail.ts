// 队列页的**光标**和它带出来的详情那一半：防抖、自动已读、指针与键盘挪光标。
//
// 「手上这一条是谁」只能有一份，也就是这里：光标、详情、已读、撤销条都挂在同一个 id
// 上。详情那一层（`AdminQueueDetail`）的注释把同一个理由写了一遍。拆出来的理由见
// `../useAdminQueue.ts` 的文件头。
import type { Ref } from 'vue'
import type { FeedbackCard } from '@/cx_types'
import type { AdminQueueStore } from './types'

import { nextTick } from 'vue'

/** 光标落定后多久去拉详情（F-06）。200ms 里按十次 `j` 只该产生一次请求 —— 中间的九次
 *  都在把这一颗定时器重排。 */
const DETAIL_DEBOUNCE_MS = 150

/** 详情真的画出来之后再等多久推已读游标（F-13）。800ms 是「人确实看了这一条」和
 *  「光标路过」之间的距离。 */
const READ_DELAY_MS = 800

/** 「指针刚刚点过队列」的时间窗。**行只报 `activate`，鼠标点击和 `j` 在这一条事件上
 *  长得一模一样**，而两者的意思不同：「点一行」是「打开它」，「按一下 `j`」只是「往下
 *  看一行」。窄屏上把后者也当成打开，就是每按一次 `j` 弹一次抽屉。所以靠时间窗分开。 */
const POINTER_WINDOW_MS = 400

/** 这一份要的那几个状态与推导值：`store`、断点、光标本尊、详情开关、可见行与它的下标。 */
export interface AdminQueueDetailDeps {
  store: AdminQueueStore
  isWide: Ref<boolean>
  cursorId: Ref<string | null>
  detailOpen: Ref<boolean>
  visible: Ref<FeedbackCard[]>
  cursorIndex: Ref<number>
}

/** 光标与详情：谁被选中、详情什么时候拉、什么时候算「看过」。 */
export function useAdminQueueDetail(deps: AdminQueueDetailDeps) {
  const { store, isWide, cursorId, detailOpen, visible, cursorIndex } = deps

  /* ---- 详情：防抖 + 自动已读 ---- */

  let detailTimer: ReturnType<typeof setTimeout> | undefined
  let readTimer: ReturnType<typeof setTimeout> | undefined
  let armedId: string | null = null

  function clearDetailTimers() {
    clearTimeout(detailTimer)
    clearTimeout(readTimer)
    detailTimer = undefined
    readTimer = undefined
  }

  /** 详情请求的**代次**：每次挪光标 +1。
   *
   *  150ms 防抖只把「同一段连按」并成一次请求，它挡不住的是**光标先动了、请求还没发**
   *  那一段时间：连按 `j` 时那支计时器每次都被重置，于是行上的选中、头上的编号一直在
   *  往前走，而详情区挂的还是最早那一条的正文 —— 那正是 F-06 要丢掉的「过期的东西」。
   *
   *  为什么不靠 store 里 `detailId` 那一条判据：它挡的是「响应比**新请求**晚到」，挡不住
   *  「响应比**光标**晚到」—— 后者发生在防抖窗口里，那期间新请求根本还没发出去，
   *  `detailId` 还是旧的，旧响应会被当成有效的收下。两件事都要有，缺一个就漏一种。 */
  let detailSeq = 0

  /** 光标记到哪一条上、`markRead` 要不要跟着推。**请求归请求，已读归已读**：光标路过
   *  一条不等于看过它，所以列表里按 `j` 走一趟不该把整个未读数清零。 */
  function armDetail(id: string | null, markRead: boolean) {
    clearDetailTimers()
    armedId = id
    // 代次 +1：从那一条还在路上的请求，到下面 `.then()` 里那次自动已读，全部作废。
    const seq = ++detailSeq
    // **作废在挪光标这一刻就发生**，不等防抖。连按 `j` 时那支计时器每次都被重置，等它
    // 到期等于让详情区整段连按期间挂着上一条的正文，而左侧选中的行、头上的编号已经是
    // 新的了（F-06）。先画回骨架，说的是实话。
    store.invalidateDetail(id)
    if (!id) return
    detailTimer = setTimeout(() => {
      detailTimer = undefined
      if (seq !== detailSeq) return
      void store.loadAdminDetail(id).then(() => {
        if (seq !== detailSeq) return
        // 拉失败、或这期间人已经挪到别处：不算「看过」，游标不动。
        if (armedId !== id || store.detailId !== id || !store.detail) return
        if (!markRead) return
        readTimer = setTimeout(() => {
          readTimer = undefined
          if (armedId === id) void store.markReadOnce(id)
        }, READ_DELAY_MS)
      })
    }, DETAIL_DEBOUNCE_MS)
  }

  /* ---- 光标 ---- */

  let pointerAt = 0

  function pointerJustUsed(): boolean {
    return Date.now() - pointerAt < POINTER_WINDOW_MS
  }

  function select(id: string | null, opts: { open?: boolean; pointer?: boolean } = {}) {
    const changed = id !== cursorId.value
    cursorId.value = id
    // 宽屏那一档没有「选中但看不到」这回事（整页接管），所以光标一挪就等于进了详情；
    // 窄屏上抽屉要人明说才开，所以只有指针点的那一次和 `Enter` 才算。
    armDetail(id, !!id && (isWide.value ? changed || !!opts.open : !!opts.open || !!opts.pointer))
    if (!id) return
    if (opts.open || opts.pointer || (isWide.value && changed)) detailOpen.value = true
  }

  /** `j` / `k` / `H` 挪光标。挪完把焦点也带过去：不然连按 `H` 之后焦点还留在旧行上，
   *  下一颗键又落回旧行（`AdminQueueList` 的 `move` 是同一个理由）。 */
  function step(delta: number, open = false) {
    const to = cursorIndex.value + delta
    if (to < 0 || to >= visible.value.length) return
    select(visible.value[to].id, { open })
    void nextTick(() => focusRow(visible.value[to].id))
  }

  /** 行元素按 id 找，不问组件要 ref：行 id 的约定（`fbrow-` / `ftrow-`）本来就是两个
   *  列表组件和这一层共用的（`aria-activedescendant` 也按它找）。 */
  function focusRow(id: string) {
    const row = document.getElementById(`fbrow-${id}`) ?? document.getElementById(`ftrow-${id}`)
    row?.querySelector<HTMLElement>('.fbrow__link')?.focus()
  }

  /** 两个列表报上来的「光标挪到第几行」。指针刚点过就按「点开」处理，见 `POINTER_WINDOW_MS`。 */
  function onActiveIndex(index: number) {
    const item = visible.value[index]
    if (item) select(item.id, { pointer: pointerJustUsed() })
  }

  /** 总表报上来的「选中这一行」。列表那边是 `update:activeIndex`（它按行号报），总表报
   *  的是 id —— 两者的形状不同是因为各自的光标不一样（roving tabindex vs 选中行）。 */
  function onTableActivate(id: string) {
    select(id, { pointer: pointerJustUsed() })
  }

  /** `Enter`（两个列表都只在这一条路上发 `open`）。行上那颗按钮发的是 `advance` ——
   *  推状态和看详情是两件事，混成一个会让人按一下就改掉一条状态。 */
  function openItem(id: string) {
    select(id, { open: true })
  }

  function onPointerDown() {
    pointerAt = Date.now()
  }

  return {
    armDetail,
    clearDetailTimers,
    select,
    step,
    onActiveIndex,
    onTableActivate,
    openItem,
    onPointerDown,
  }
}
