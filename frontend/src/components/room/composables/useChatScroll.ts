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

/**
 * 每个话题停在哪，放在模块作用域，这样组件卸载再挂回来（比如去了别的页面再回来）
 * 位置还在——回到一个话题应该落在你离开的地方，而不是被甩到底部。
 *
 * 跟着偏移量一起存 `atBottom`，因为「在底部」是个**语义位置**：两次访问之间时间线
 * 的高度会变（离开期间到的消息已经在缓存里，合并框是异步填的），照着旧像素值还原
 * 会把最新一条留在折叠线以下——就是「最后一条消息晚一帧才蹦出来」那个 bug。
 */
const scrollMemory = new Map<string, { top: number; atBottom: boolean }>()

/** 离底部多近还算「在底部」（px）。 */
const BOTTOM_THRESHOLD = 80

/** 重放静默多久算这一阵过去了（ms）。 */
const CATCH_UP_IDLE_MS = 200

/** 读的人上一次动滚轮、触屏、键盘之后多久内，滚动还算他滚的（ms）。触控板松手后的惯性一直在发 wheel。 */
const READER_INPUT_MS = 500

/** 时间线内容改写之后多久内，没人动却往上挪的那一下算改写挪的（ms）。 */
const REWRITE_MS = 500

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
  const topicId = options.topicId ?? (() => undefined)
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
  let rewrittenAt = -Infinity
  const touched = () => (readerInputAt = performance.now())
  // 读的人自己动手，就是他说了算：回填还没落到位的话，目标作废。只认这一栏上的输入 —
  // 别的页签里、输入框里敲的字不是「他在这一栏里翻了」。
  const readerTookOver = () => {
    touched()
    settlePendingTop()
  }
  for (const type of ['wheel', 'touchmove'] as const)
    useEventListener(scrollRef, type, readerTookOver, { passive: true })
  useEventListener(scrollRef, 'pointerdown', () => {
    pointerHeld = true
    readerTookOver()
  })
  // 键盘也在这栏里翻（PageUp / 方向键、带焦点的消息行）。
  useEventListener(scrollRef, 'keydown', readerTookOver)
  useEventListener(window, ['pointerup', 'pointercancel'], () => {
    if (!pointerHeld) return
    pointerHeld = false
    touched()
  })
  useEventListener(window, 'keydown', touched)
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
  let pendingTop: number | null = null
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
  function holdPendingTop(top: number) {
    pendingTop = top
    clearPendingSettle()
    pendingSettle = setTimeout(() => {
      pendingSettle = null
      if (pendingTop === null) return
      pendingTop = null
      const sc = scrollRef.value
      if (!sc || !sc.clientHeight) return
      atBottom.value = isAtBottom(sc)
      const id = showingNewest() ? topicId() : undefined
      if (id) scrollMemory.set(id, { top: sc.scrollTop, atBottom: atBottom.value })
    }, PENDING_SETTLE_MS)
  }
  function settlePendingTop() {
    clearPendingSettle()
    pendingTop = null
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
        if (pendingTop === null && !atBottom.value && showingNewest()) holdPendingTop(lastTop)
        return
      }
      if (pendingTop !== null) {
        if (!sc.clientHeight) return
        // 还没到想去的那个位置：内容此刻比记下位置时短（行是分批挂上来的），放不进去。
        // 「卡在当前末端」不是「他本来就停在底部」——把它当后者正是「退出支线被甩到
        // 最底」的来路。目标留着，等长够了再放；在这之前也不跟新消息。
        const want = pendingTop
        place(sc, want)
        if (sc.scrollTop >= want) settlePendingTop()
        atBottom.value = pendingTop === null && isAtBottom(sc)
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
  // `place` 写不进去（见 `pendingTop`），滚动事件也不来，留着旧的那两份，露出来、或者
  // 切走再回来时就会被放回一个指着别的内容的偏移——整段换过之后正是这样。
  function scrollToBottom() {
    settlePendingTop()
    // 叫的时候是哪个话题就记给哪个：同一拍里切了话题的话，nextTick 里读到的是新的那个，
    // 把它记成「停在底部」会盖掉它自己记下的位置。
    const id = topicId()
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
    // 回填还没落到位：这期间的「位置」都是它自己一次次试着放的结果（而且没放进去），
    // 读它只会把「还没到」记成「就在这里」。目标还等着，先不记。
    if (pendingTop !== null) {
      lastTop = el.scrollTop
      lastHeight = el.scrollHeight
      return
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
    scrollMemory.set(topicId, { top: el.scrollTop, atBottom: atBottom.value })
  }

  // 还原一个话题存下来的滚动位置。「在底部」（以及压根没存过）还原到**当前**的底部
  // 而不是记下来的偏移量——时间线可能比离开时更高了（后台刷新缓存已经拿到了离开期
  // 间到的消息），最新一条必须在第一帧就看得见。
  function restoreScroll(topicId: string) {
    void nextTick(() => {
      const el = scrollRef.value
      if (!el) return
      const saved = scrollMemory.get(topicId)
      settlePendingTop()
      if (saved && !saved.atBottom) {
        // 有布局也未必放得进去：这一帧内容还只有估高的那一份短，记下的偏移在它之外。
        // 放不到就不认这帧的结果，目标留给 ResizeObserver，等长够了再落（同上面那条）。
        holdPendingTop(saved.top)
        atBottom.value = false
        if (!el.clientHeight) return
        place(el, saved.top)
        if (el.scrollTop >= saved.top) settlePendingTop()
      } else {
        place(el, el.scrollHeight)
        atBottom.value = true
      }
    })
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
    restoreScroll,
    restoresToBottom,
    forgetScroll,
  }
}
