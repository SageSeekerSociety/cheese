/**
 * 「这一栏停在哪」——滚动位置、跟不跟新消息、以及重放风暴期间别抖。
 *
 * 只管滚动本身。往回翻历史（拉上一页、拼接、补偿位移）不在这里：
 * 那件事碰 `messages`、`props.topic`、`listBlocks`、缓存和错误横幅，是房间壳的活。
 * 壳自己在滚动事件里读 `scrollRef` 决定要不要去拉。
 */

import type { Ref } from 'vue'

import { nextTick, onScopeDispose, ref, watch } from 'vue'
import { useEventListener, useMutationObserver } from '@vueuse/core'

import { beginMeasuredLayout, endMeasuredLayout } from '../../../lib/contentVisibility'

/**
 * 每个话题停在哪，放在模块作用域，这样组件卸载再挂回来（比如去了别的页面再回来）
 * 位置还在——回到一个话题应该落在你离开的地方，而不是被甩到底部。
 *
 * 跟着偏移量一起存 `atBottom`，因为「在底部」是个**语义位置**：两次访问之间时间线
 * 的高度会变（离开期间到的消息已经在缓存里，合并框是异步填的），照着旧像素值还原
 * 会把最新一条留在折叠线以下——就是「最后一条消息晚一帧才蹦出来」那个 bug。
 *
 * 还存顶边压着的那一行（`anchor`）。像素只在内容一样高时才指得准，而回来时上面那些
 * 行多半还在估计高度上（`content-visibility` 的 `contain-intrinsic-size`），差出来的
 * 高度全堆在偏移量上——落下去屏幕上是另一条消息（真机 2026-10-24：退出支线回频道，
 * 手机上偏半屏）。按那一行把它摆回去，才是「回到你刚才看的那条」。
 */
interface ScrollAnchor {
  /** `data-row-id`：那一条消息。 */
  id: string
  /** 它压进这一栏顶边多少像素（负数是它还有一截露在上面）。 */
  offset: number
}
interface RememberedScroll {
  top: number
  atBottom: boolean
  anchor?: ScrollAnchor
}
const scrollMemory = new Map<string, RememberedScroll>()

/** 离底部多近还算「在底部」（px）。 */
const BOTTOM_THRESHOLD = 80

/** 重放静默多久算这一阵过去了（ms）。 */
const CATCH_UP_IDLE_MS = 200

/** 读的人上一次动滚轮、触屏、键盘之后多久内，滚动还算他滚的（ms）。触控板松手后的惯性一直在发 wheel。 */
const READER_INPUT_MS = 500

/** 时间线内容改写之后多久内，没人动却往上挪的那一下算改写挪的（ms）。 */
const REWRITE_MS = 500

/** 我们自己发起的那一下滚动，安静多久算停下了（ms）。平滑滚动每一帧都发滚动事件。 */
const OWN_SCROLL_IDLE_MS = 150

export interface ChatScroll {
  /** 绑在可滚动的那个容器上。 */
  scrollRef: Ref<HTMLElement | null>
  /** 绑在容器里装内容的那一层上——它变高就是「新消息来了」。 */
  contentRef: Ref<HTMLElement | null>
  /** 用户此刻是不是停在底部附近。 */
  atBottom: Ref<boolean>
  scrollToBottom(): void
  /** 有新内容时跟到底部——但只在用户本来就停在底部时。 */
  autoScroll(): void
  /** 进入重放追赶模式：这一阵里 `autoScroll` 不动手。 */
  beginCatchUp(): void
  /** 收到一帧。用来判断重放这一阵什么时候静下来。 */
  noteFrame(): void
  /** 记下这个话题现在停在哪，并刷新 `atBottom`（滚动事件里、以及切走时调）。 */
  rememberScroll(topicId: string | undefined): void
  /**
   * 说一声「接下来这一下滚动是我们自己发的」（跳到某一条）。这一阵里不把往上的移动
   * 当成读者往上翻，也不当成浏览器锚定夹的；停下之后再按落点定「在不在底部」。
   */
  beginOwnScroll(): void
  /** 还原这个话题上次停的地方。 */
  restoreScroll(topicId: string): void
  /** 这个话题再打开时会不会停在底部。 */
  restoresToBottom(topicId: string): boolean
  /** 不再记这个话题停在哪：再打开时落到底部。 */
  forgetScroll(topicId: string): void
}

export function useChatScroll(
  options: {
    /**
     * 这一栏此刻显示的是不是最新的一段。停在历史中间时，「在底部」只是这一段的
     * 底部：不钉底、不跟新消息，位置也不记（回来时显示的是最新的一段）。
     */
    showingNewest?: () => boolean
    /** 此刻这一栏是哪个话题：落到底部时替它记下「停在底部」。 */
    topicId?: () => string | undefined
    /**
     * 和滚动面板同处一栏、输入区也在其中的那一层（面板根）。滚动面板的直接父元素
     * 可能只是时间线自己的包装层，输入区是那个包装层的兄弟——观察它看不到输入区。
     * 不传就用直接父元素（面板把输入区和滚动框放在同一层时它就够）。
     */
    column?: () => HTMLElement | null
  } = {}
): ChatScroll {
  const showingNewest = options.showingNewest ?? (() => true)
  /** 此刻这一栏读的是哪一段对话。`rememberScroll` 的入参是「要记哪一段」，是两件事。 */
  const currentTopicId = options.topicId ?? (() => undefined)
  const scrollRef = ref<HTMLElement | null>(null)
  const contentRef = ref<HTMLElement | null>(null)

  /** 用户是不是停在（或接近）底部——决定新消息是自动跟随还是不碰他的位置。 */
  const atBottom = ref(true)

  function isAtBottom(el: HTMLElement): boolean {
    return el.scrollHeight - el.scrollTop - el.clientHeight < BOTTOM_THRESHOLD
  }

  // Where this pane last was, as far as this composable knows: what the last scroll
  // event reported, or what it last wrote itself. Leaving the bottom means scrolling
  // UP; a scroll event that did not move up was not the reader leaving.
  //
  // The browser fires such events on its own. A topic opened pinned to the bottom grows
  // after that first pin: rows that were never on screen count at their
  // content-visibility estimate (room-row.css) until they render, and replies fill in
  // after first paint. Scroll anchoring then moves scrollTop down by part of the growth
  // and fires a scroll event that can arrive before the resize observer has followed
  // the growth. Judged by distance alone, that event read as "the reader scrolled up":
  // atBottom went false, the observer stopped following, and the topic was left a
  // screen or more above its newest message.
  //
  // It moves scrollTop UP the same way, and `lastTop` alone reads that as the reader
  // too: the content above shrinks (rows that were off screen take their real height,
  // or a long reply folds down to a reading height) while the reply below keeps
  // growing. So an upward move counts as the reader only when the content height did
  // not change in that same frame — the shrink and the growth move scrollTop, the
  // reader does not.
  //
  // A third way, with the height back where it was by the time the event arrives:
  // the timeline's rows are rewritten in place (every message's body is redrawn when
  // the room's topic titles are refreshed). Newly inserted blocks carry
  // content-visibility and are laid out at their estimate first, so the content is
  // briefly shorter and the browser clamps scrollTop to that shorter bottom; then
  // the real heights come back. The resize observer sees no change and the scroll
  // event finds the same height it last saw, so neither rule above catches it — the
  // pane was left hundreds of pixels up with 「回到最新」 showing, in WebKit (the
  // desktop app) every time a task in the project changed. So an upward move also
  // does not count when the reader gave no scrolling input and the content, or what
  // shares the pane's column, was just rewritten; a move up with input, or with nothing
  // rewritten (find in page), still does.
  let lastTop = 0
  let lastHeight = 0
  let readerInputAt = -Infinity
  let pointerHeld = false
  let pointerTop = 0
  let rewrittenAt = -Infinity
  const touched = () => (readerInputAt = performance.now())
  // 读的人自己动手，就是他说了算：回填还没落到位的话，目标作废。只认**真的挪了位置**
  // 的那几种手势（见下面 pointermove 与 keydown 的判据）：点一下消息不算，别的页签、
  // 输入框里敲的字也不算。
  const readerTookOver = () => {
    touched()
    settlePending()
    cancelOwnScroll()
  }
  for (const type of ['wheel', 'touchmove'] as const)
    useEventListener(scrollRef, type, readerTookOver, { passive: true })
  // 「人在滚」= 位置真的动了。按一下不是滚：单击一条消息、点住又松手、手指在消息上
  // 点一下，位置一个像素都没动，可按住期间这一份却被当成读者在滚，于是下面那条「刚被
  // 改写、没人动却往上挪」的兜底失效，停住的人被留在半路（「回到最新」冒出来，真机
  // 2026-10-24 反馈）。所以按住之后要位置变了才算——回填的目标也照这一条作废，不然点
  // 一下消息就把「回到原来那条」取消了。
  useEventListener(scrollRef, 'pointerdown', () => {
    pointerHeld = true
    pointerTop = scrollRef.value?.scrollTop ?? 0
  })
  useEventListener(window, 'pointermove', () => {
    const el = scrollRef.value
    if (!pointerHeld || !el || el.scrollTop === pointerTop) return
    pointerTop = el.scrollTop
    readerTookOver()
  })
  useEventListener(window, ['pointerup', 'pointercancel'], () => {
    pointerHeld = false
  })
  // 键盘同理：只有会挪这一栏的那几个键算，而且得是**这一栏会跟着动**的那一下。
  // 键名定不了这件事——同一个键在输入框里和落在面板上做的不是一件事，而输入框里的
  // keydown 一路冒到 window：
  // - 发消息的输入框在面板**外面**，它里面的按键（打字的空格、挪光标的箭头、翻它自己
  //   的 PageUp）挪的都是它自己，这一栏一动不动。中文输入法每打一个词按一次空格，这
  //   一下要是不排掉，停下来读的人的改写兜底就一直是废的（2026-10-24 真机反馈）。所以
  //   落在输入框里、又不在面板里的按键，一个都不算。
  // - 其余的都算：落在面板里的（包括时间线里的就地编辑器——改自己的消息时，光标走出
  //   可视范围、翻页键，挪的都是这一栏），以及压根不在输入框里的。合并基线上每个键都
  //   算，这里只把上面那一类摘出去，别的一律照旧。
  const MOVES_THE_PANE = new Set(['ArrowUp', 'ArrowDown', 'Home', 'End', ' ', 'Spacebar', 'PageUp', 'PageDown'])
  useEventListener(window, 'keydown', (e) => {
    if (!MOVES_THE_PANE.has(e.key)) return
    const el = scrollRef.value
    const target = e.target instanceof Element ? e.target : null
    const inPane = !!(el && target && el.contains(target))
    const inEditable = !!target?.closest('input, textarea, [contenteditable]:not([contenteditable="false"])')
    if (inEditable && !inPane) return
    // 这个键在这一栏里算读者在滚；他真挪了位置的话，随后的滚动事件会让回填的目标作废
    // （见 rememberScroll），不用在这里再判一次。
    touched()
  })
  // The pane's column too: the composer zone under the pane leaving and coming back in
  // one task clamps it the same way, with the timeline untouched. The pane's direct
  // parent is not that column: the timeline sits in its own wrapper (the floating
  // bookmark rail is positioned against it), and the composer zone is the wrapper's
  // sibling. Watching the parent alone, nobody saw the composer come back:
  // `rewrittenAt` stayed old, the clamp read as the reader scrolling up, and a pane
  // parked at the bottom was left above it with 「回到最新」 showing — in WebKit (the
  // desktop app) every time the member-activity line came and went. So the host hands
  // in the element that holds both; without one, the parent is still the best guess.
  useMutationObserver(
    () => [contentRef.value, options.column?.() ?? scrollRef.value?.parentElement],
    (mutations) => {
      if (
        mutations.some((m) => {
          const el = m.target instanceof Element ? m.target : m.target.parentElement
          return !el?.closest('.composer-editor')
        })
      ) {
        rewrittenAt = performance.now()
      }
    },
    { childList: true, subtree: true, characterData: true }
  )
  function layoutMoved(now: number): boolean {
    const reader = pointerHeld || now - readerInputAt < READER_INPUT_MS
    return !reader && now - rewrittenAt < REWRITE_MS
  }

  function place(el: HTMLElement, top: number) {
    el.scrollTop = top
    lastTop = el.scrollTop
    lastHeight = el.scrollHeight
  }

  /**
   * 按**真实高度**把这一栏放到 `top`。
   *
   * 记下的偏移是在真实高度里量的：把位置放回去就得用同一把尺子。离屏的行带着
   * `content-visibility`，此刻报的还是 `contain-intrinsic-size` 那个估计值（见
   * lib/contentVisibility）——照估计值落，落点之后会随真实高度一起漂，屏幕上是另一条
   * 消息（真机 2026-10-24：退出支线回频道，偏了半屏）。所以量之前先挂测量帧，落完再撤。
   */
  function placeAtRealHeight(el: HTMLElement, top: number) {
    beginMeasuredLayout(el)
    place(el, top)
    endMeasuredLayout(el)
  }

  /** 此刻压在顶边的那一行，和它压进去多少：像素偏移在高度变了之后指不准，它不会。
   *  扫的是这一窗（PAGE_SIZE 条封顶，不是整条时间线），一次记住位置读一遍。 */
  function anchorNow(el: HTMLElement): ScrollAnchor | undefined {
    const top = el.getBoundingClientRect().top
    for (const row of el.querySelectorAll<HTMLElement>('[data-row-id]')) {
      const r = row.getBoundingClientRect()
      if (r.height === 0) continue
      if (r.bottom > top) return { id: row.dataset.rowId ?? '', offset: Math.round(top - r.top) }
    }
    return undefined
  }

  function rowById(el: HTMLElement, id: string): HTMLElement | null {
    for (const row of el.querySelectorAll<HTMLElement>('[data-row-id]')) {
      if (row.dataset.rowId === id) return row
    }
    return null
  }

  /**
   * 把 `anchor` 那一行摆回它记录时的位置（压进顶边 `offset` 像素）。答放没放到：
   * 那一行还没挂上来、或这一栏还没长到能把它摆上去，就答 false，交给观察者等下一帧。
   */
  function placeAtAnchor(el: HTMLElement, anchor: ScrollAnchor): boolean {
    const row = rowById(el, anchor.id)
    if (!row) return false
    beginMeasuredLayout(el)
    // 记下时这一行的顶边在这一栏顶边上方 `offset` 像素处。现在它偏了多少，就把
    // scrollTop 补多少：内容往上挪一像素，这一行也跟着上去一像素。
    const drift = (r: HTMLElement) => r.getBoundingClientRect().top - el.getBoundingClientRect().top + anchor.offset
    place(el, el.scrollTop + drift(row))
    const landed = Math.abs(drift(row)) <= 2
    endMeasuredLayout(el)
    return landed
  }

  // 把这一栏钉在底部——在用户停在那儿的时候。时间线的高度会在切话题的第一帧**之后**
  // 才变（合并框 / 接受卡是异步填的、图片要解码、流式输出会重排），没有这个的话
  // 纠正只能等后面某次异步滚动（fetch 回来、追赶的空闲定时器），尾巴就看得见地晚
  // 一拍才蹦出来。ResizeObserver 的回调跑在布局之后、绘制之前：重钉和变高落在**同
  // 一帧**里，所以一帧错的都不会被画出来。
  //
  // 盯的是**两个**元素，不是内容一个：内容变高是「新消息来了」，而容器变矮是
  // 「地方变小了」——手机弹出软键盘缩的正是这个容器（`--keyboard-inset` 减的就是
  // 它），内容高度一个像素都没动。只盯内容时，键盘一起来回调一次都不发，停在底部
  // 的人就看着最新几条滑到键盘底下（真机反馈 2026-09-17）。两个都在同一个
  // observer 里，重钉只有一条路径，不会互相打架。
  let contentObserver: ResizeObserver | null = null
  // A pane with no layout ignores `scrollTop`, and where it was is the browser's to
  // forget: 专注模式 hides the chat column, a topic opened while another tab is on
  // screen mounts its pane hidden, and on a phone every step away from 对话 hides it
  // (`v-show`) — it then reports 0 for all three numbers. Chromium writes the number
  // back when the pane is shown again; Safari does not, and the reader lands at the very
  // top of a conversation they were reading in the middle of (真机反馈 2026-10-12：点开
  // 一份预览再关掉，位置没了). So the position waits here, and the observer below
  // applies it as soon as the pane has a size again.
  // 回填还没落到位时要放回的那一处。有 `anchor` 就按那一行摆（高度变了也指得准），
  // 没有就退回像素偏移。`top` 一直留着：到期兜底要用它判断还差多少。
  let pending: { top: number; anchor?: ScrollAnchor } | null = null
  // 回填等多久算等不到：内容一直没长到记下那个位置（时间线真的短了，或有行再也不挂），
  // 落回当前能到的位置就是当时最好的答案。**必须**有这一条：没有的话目标永远留着，
  // 这一栏就再也不跟新消息了。
  const PENDING_SETTLE_MS = 1500
  let pendingSettle: ReturnType<typeof setTimeout> | null = null
  function clearPendingSettle() {
    if (pendingSettle !== null) clearTimeout(pendingSettle)
    pendingSettle = null
  }
  /** 定一个回填目标，并给它一个到期时间。 */
  function holdPending(target: { top: number; anchor?: ScrollAnchor }) {
    pending = target
    clearPendingSettle()
    pendingSettle = setTimeout(() => {
      pendingSettle = null
      if (pending === null) return
      pending = null
      const sc = scrollRef.value
      if (!sc || !sc.clientHeight) return
      atBottom.value = isAtBottom(sc)
      const id = showingNewest() ? currentTopicId() : undefined
      if (id) scrollMemory.set(id, { top: sc.scrollTop, atBottom: atBottom.value, anchor: anchorNow(sc) })
    }, PENDING_SETTLE_MS)
  }
  function settlePending() {
    clearPendingSettle()
    pending = null
  }

  /** 把还没落位的回填再放一次；放到了就收工。答放到了没有（本来就没有目标也算放到了）。 */
  function applyPending(sc: HTMLElement): boolean {
    if (pending === null) return true
    const want = pending
    let landed: boolean
    if (want.anchor) {
      // 按那一行摆：内容比记下时高还是矮，它都指得准。
      landed = placeAtAnchor(sc, want.anchor)
    } else {
      placeAtRealHeight(sc, want.top)
      landed = sc.scrollTop >= want.top - 1
    }
    if (landed) settlePending()
    atBottom.value = pending === null && isAtBottom(sc)
    return landed
  }
  watch([contentRef, scrollRef], ([content, pane]) => {
    contentObserver?.disconnect()
    contentObserver = null
    if (!content && !pane) return
    contentObserver = new ResizeObserver(() => {
      const sc = scrollRef.value
      if (!sc) return
      if (!sc.clientHeight) {
        // 刚没了布局（或是还藏在别处），而人正读在这一段中间：趁现在记下他停在哪。
        // 藏起来的那一刻这张 0×0 的回调就会发一次，那时 `lastTop` 还是他最后报的位
        // 置；之后再读这个元素只会读到 0。停在底部的人不用记——下面那条分支会把他带
        // 到**当前**的底部，离开期间到的消息才看得见。
        // 藏起来的那一刻还不知道顶边是哪一行（这一栏已经没布局了），只能记像素。
        if (pending === null && !atBottom.value && showingNewest()) holdPending({ top: lastTop })
        return
      }
      // 跳完了、这一栏当时还没有布局：现在有高度了，按落点把「在不在底部」定回来。
      if (ownScrollAwaitingLayout) {
        settleOwnScroll()
        return
      }
      // 回填还没落到位：内容此刻比记下时短（行是分批挂上来的），放不进去。「卡在当前
      // 末端」不是「他本来就停在底部」——把它当后者正是「退出支线被甩到最底」的来路。
      // 目标留着，等长够了再放；在这之前也不跟新消息。
      if (pending !== null) {
        applyPending(sc)
        return
      }
      // 停在底部的人，内容一变高就在同一帧跟到底，差一个像素也跟。BOTTOM_THRESHOLD
      // 只回答「他算不算停在底部」；拿它当重钉的门槛的话，要攒够 80px 才跳一次，
      // 流式输出和展开的卡片都是一格一格地往上蹦，攒的那一截一直藏在视口下面。
      if (atBottom.value && showingNewest() && sc.scrollHeight - sc.scrollTop - sc.clientHeight > 1)
        place(sc, sc.scrollHeight)
    })
    if (content) contentObserver.observe(content)
    if (pane) contentObserver.observe(pane)
  })

  // 追赶模式：socket 刚（重）连上时，broker 会把一轮进行中的所有缓冲帧**一次性重放**
  // 出来。逐帧渲染加逐帧滚动会让这一栏在长轮次上闪好几秒——所以这一阵里帧照收但不
  // 滚，等它静下来再滚一次。
  let catchingUp = false
  let catchUpTimer: ReturnType<typeof setTimeout> | null = null

  function beginCatchUp() {
    catchingUp = true
    noteFrame()
  }

  function noteFrame() {
    if (!catchingUp) return
    if (catchUpTimer) clearTimeout(catchUpTimer)
    catchUpTimer = setTimeout(() => {
      catchingUp = false
      autoScroll()
    }, CATCH_UP_IDLE_MS)
  }

  // 要的是最新那条，就连同「藏起来时停在哪」和记下的位置一起改掉：这一栏藏着时
  // `place` 写不进去（见 `pending`），滚动事件也不来，留着旧的那两份，露出来、或者
  // 切走再回来时就会被放回一个指着别的内容的偏移——整段换过之后正是这样。
  function scrollToBottom() {
    settlePending()
    // 要的就是底部：正在进行的「跳到某一条」到此为止，别等它停下来的那一拍再改口。
    cancelOwnScroll()
    // 叫的时候是哪个话题就记给哪个：同一拍里切了话题的话，nextTick 里读到的是新的那个，
    // 把它记成「停在底部」会盖掉它自己记下的位置。
    const id = currentTopicId()
    void nextTick(() => {
      const el = scrollRef.value
      if (el) {
        place(el, el.scrollHeight)
        atBottom.value = true
      }
      if (id && showingNewest()) scrollMemory.set(id, { top: el?.scrollTop ?? 0, atBottom: true })
    })
  }

  // 只在用户没往上翻时跟随新消息。重放追赶期间逐帧的调用被压住，由 `noteFrame`
  // 在这一阵静下来之后滚一次。
  function autoScroll() {
    if (catchingUp || !showingNewest()) return
    if (atBottom.value) scrollToBottom()
  }

  function rememberScroll(topicId: string | undefined) {
    const el = scrollRef.value
    // A hidden pane has no position to report; what is remembered stays.
    if (!el || !topicId || !el.clientHeight) return
    // 我们自己发的那一下还在滚（跳到某一条）：只把位置记进 lastTop/lastHeight，记下的
    // 位置和「在不在底部」都等它停下再定。半路上判，头几帧还在底部、后面又被 `clamped`
    // 那条兜底读成锚定，人就被钉回原处。
    if (ownScrollTimer !== null) {
      if (currentTopicId() === ownScrollTopic) {
        armOwnScroll()
        lastTop = el.scrollTop
        lastHeight = el.scrollHeight
        return
      }
      // 滚到一半切走了话题：离开的这一段该照常记下它此刻停在哪（回来要落在那儿），
      // 而这一跳不算「我们自己发的」了。
      cancelOwnScroll()
    }
    // 回填还没落到位：这期间的「位置」都是它自己一次次试着放的结果（而且没放进去），
    // 读它只会把「还没到」记成「就在这里」。目标还等着，先不记——除非这一下滚动是读的
    // 人自己弄的（刚刚在这一栏里滚过轮/触过屏/按过会翻页的键）。他的手才是准的，目标作废。
    if (pending !== null) {
      // pointerdown 本身不算（见上面那条：按一下不是滚），readerInputAt 只有真的挪了
      // 位置的手势才会刷新。
      if (performance.now() - readerInputAt >= READER_INPUT_MS) {
        lastTop = el.scrollTop
        lastHeight = el.scrollHeight
        return
      }
      settlePending()
    }
    const movedUp = el.scrollTop < lastTop
    const relaid = el.scrollHeight !== lastHeight
    const clamped = movedUp && layoutMoved(performance.now())
    lastTop = el.scrollTop
    lastHeight = el.scrollHeight
    // Pinned and not scrolled up: still pinned, however far the content grew below
    // (see `lastTop`). The resize observer follows it to the new bottom. An upward move
    // in a frame where the content height moved is the browser's anchoring, not the
    // reader; nor is one left by a rewrite, which no resize follows, so go back now.
    atBottom.value = isAtBottom(el) || (atBottom.value && (!(movedUp && !relaid) || clamped))
    if (clamped && atBottom.value && showingNewest() && !isAtBottom(el)) place(el, el.scrollHeight)
    if (!showingNewest()) return
    scrollMemory.set(topicId, {
      top: el.scrollTop,
      atBottom: atBottom.value,
      // 停在底部的人不需要锚点（回来就是要最新那条）；停在中间才按这一行摆回去。
      anchor: atBottom.value ? undefined : anchorNow(el),
    })
  }

  // 还原一个话题存下来的滚动位置。「在底部」（以及压根没存过）还原到**当前**的底部
  // 而不是记下来的偏移量——时间线可能比离开时更高了（后台刷新缓存已经拿到了离开期
  // 间到的消息），最新一条必须在第一帧就看得见。
  function restoreScroll(topicId: string) {
    void nextTick(() => {
      const el = scrollRef.value
      if (!el) return
      const saved = scrollMemory.get(topicId)
      settlePending()
      if (saved && !saved.atBottom) {
        // 有布局也未必放得进去：这一帧内容还只有估高的那一份短，要放的位置在它之外。
        // 放不到就不认这帧的结果，目标留给 ResizeObserver，等长够了再落（同上面那条）。
        holdPending({ top: saved.top, anchor: saved.anchor })
        atBottom.value = false
        if (!el.clientHeight) return
        applyPending(el)
      } else {
        place(el, el.scrollHeight)
        atBottom.value = true
      }
    })
  }

  // ── 我们自己发起的滚动（跳到某一条）──────────────────────────────────────
  //
  // 它既不是读的人在滚，也不是浏览器被就地改写夹的。可两条判据都把它当成后两者：
  // 平滑滚动的头几帧离底部还不到一屏，照位置算会把「跟新消息」重新打开；而只要这一栏
  // 在跳转前后被就地改写过一次（展开浮条、跳过去时离屏的行成批挂上来、agent 在跑时
  // 输入框上方那行「用时」每秒跳），上面那条 `clamped` 兜底就把这次往上的移动读成
  // 锚定，当场钉回底部。真机 2026-10-26：停在对话最底下点左上角书签，被拽回来，
  // 到不了设为书签的那一条。
  //
  // 所以这一阵里 `atBottom` 先不动；滚到停下来（安静一个 OWN_SCROLL_IDLE_MS）再按
  // 真正停下的位置定它——跳到最后一条时跟新消息要照常接上。
  let ownScrollTimer: ReturnType<typeof setTimeout> | null = null
  // 这一跳是给哪个话题发的：到点时按它落账，不是按「此刻这一栏读的是哪个」——
  // 150ms 里切走了话题，读的是新话题，落到它头上的会是一个指着别的内容的位置。
  // `scrollToBottom` 出于同一个理由先抓住话题 id。
  let ownScrollTopic: string | undefined
  // 滚完了、可这一栏没有布局（`v-show` 藏着、另一个页签在前）：看不出落在哪，
  // 等观察者看到它有高度了再落账。留着这个标记，别把 `atBottom=false` 撂在那儿。
  let ownScrollAwaitingLayout = false

  function armOwnScroll() {
    if (ownScrollTimer !== null) clearTimeout(ownScrollTimer)
    ownScrollTimer = setTimeout(() => {
      ownScrollTimer = null
      settleOwnScroll()
    }, OWN_SCROLL_IDLE_MS)
  }

  /** 这一下滚完了：落点在哪就按哪定。 */
  function settleOwnScroll() {
    const el = scrollRef.value
    if (!el || !el.clientHeight) {
      ownScrollAwaitingLayout = true
      return
    }
    ownScrollAwaitingLayout = false
    // 跳到一半切走了话题：这一跳不算数，别把新话题按旧落点写掉。按此刻的位置把
    // 「在不在底部」判回来——不然它停在 `beginOwnScroll` 设的 false 上，要等下一次
    // 滚动事件才有人改。
    if (ownScrollTopic !== undefined && currentTopicId() !== ownScrollTopic) {
      cancelOwnScroll()
      return
    }
    lastTop = el.scrollTop
    lastHeight = el.scrollHeight
    atBottom.value = isAtBottom(el)
    const id = showingNewest() ? ownScrollTopic : undefined
    if (id)
      scrollMemory.set(id, {
        top: el.scrollTop,
        atBottom: atBottom.value,
        anchor: atBottom.value ? undefined : anchorNow(el),
      })
  }

  /** 往后的滚动是我们自己发的（跳到某一条、闪一下那一下落位）。 */
  function beginOwnScroll() {
    // 读的人点名要去某一条，回填中的目标就作废了。
    settlePending()
    // 上一次跳完时这一栏没有布局、还等着落账：这一跳取代它，别让观察者按旧的那一跳
    // 把新的这一跳在半路上结掉。
    ownScrollAwaitingLayout = false
    // 这一阵不跟新消息：落到哪一条由停下来的那一刻说了算（`settleOwnScroll`）。
    atBottom.value = false
    ownScrollTopic = currentTopicId()
    armOwnScroll()
  }

  /**
   * 不算我们自己发的了：读的人接了手，或者又要钉回底部。
   *
   * 撤的时候按此刻的位置把「在不在底部」定回来：这一下手势可能一个像素都没挪
   * （已经贴着底部，或只想往下滚一点点），随后的滚动事件不会来，`atBottom` 就会
   * 一直停在 `beginOwnScroll` 设的 false——「回到最新」一直亮着、新消息也不再跟。
   */
  function cancelOwnScroll() {
    if (ownScrollTimer !== null) clearTimeout(ownScrollTimer)
    ownScrollTimer = null
    ownScrollAwaitingLayout = false
    const el = scrollRef.value
    if (el?.clientHeight) atBottom.value = isAtBottom(el)
  }

  /** 不再记这个话题停在哪：再打开时落到底部。 */
  function forgetScroll(topicId: string) {
    scrollMemory.delete(topicId)
  }

  /** 这个话题再打开时会不会停在底部：没记过，或者记下的就是底部。 */
  function restoresToBottom(topicId: string): boolean {
    const saved = scrollMemory.get(topicId)
    return !saved || saved.atBottom
  }

  onScopeDispose(() => {
    if (catchUpTimer) clearTimeout(catchUpTimer)
    catchUpTimer = null
    cancelOwnScroll()
    clearPendingSettle()
    contentObserver?.disconnect()
    contentObserver = null
  })

  return {
    scrollRef,
    contentRef,
    atBottom,
    scrollToBottom,
    autoScroll,
    beginCatchUp,
    noteFrame,
    rememberScroll,
    beginOwnScroll,
    restoreScroll,
    restoresToBottom,
    forgetScroll,
  }
}
