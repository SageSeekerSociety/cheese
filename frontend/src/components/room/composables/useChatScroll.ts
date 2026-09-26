/**
 * 「这一栏停在哪」——滚动位置、跟不跟新消息、以及重放风暴期间别抖。
 *
 * 只管滚动本身，不带任何入参。往回翻历史（拉上一页、拼接、补偿位移）不在这里：
 * 那件事碰 `messages`、`props.topic`、`listBlocks`、缓存和错误横幅，是房间壳的活。
 * 壳自己在滚动事件里读 `scrollRef` 决定要不要去拉。
 */

import type { Ref } from 'vue'

import { nextTick, onScopeDispose, ref, watch } from 'vue'

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
}

export function useChatScroll(): ChatScroll {
  const scrollRef = ref<HTMLElement | null>(null)
  const contentRef = ref<HTMLElement | null>(null)

  /** 用户是不是停在（或接近）底部——决定新消息是自动跟随还是不碰他的位置。 */
  const atBottom = ref(true)

  function isAtBottom(el: HTMLElement): boolean {
    return el.scrollHeight - el.scrollTop - el.clientHeight < BOTTOM_THRESHOLD
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
  // A pane with no layout ignores `scrollTop` — the chat column is hidden while 专注模式
  // is on, and a topic opened then mounts its pane hidden. The saved position waits
  // here and is applied when the pane gets a size, which the observer below sees.
  let pendingTop: number | null = null
  watch([contentRef, scrollRef], ([content, pane]) => {
    contentObserver?.disconnect()
    contentObserver = null
    if (!content && !pane) return
    contentObserver = new ResizeObserver(() => {
      const sc = scrollRef.value
      if (!sc) return
      if (pendingTop !== null) {
        if (!sc.clientHeight) return
        sc.scrollTop = pendingTop
        pendingTop = null
        atBottom.value = isAtBottom(sc)
        return
      }
      if (atBottom.value && !isAtBottom(sc)) sc.scrollTop = sc.scrollHeight
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

  function scrollToBottom() {
    void nextTick(() => {
      const el = scrollRef.value
      if (el) {
        el.scrollTop = el.scrollHeight
        atBottom.value = true
      }
    })
  }

  // 只在用户没往上翻时跟随新消息。重放追赶期间逐帧的调用被压住，由 `noteFrame`
  // 在这一阵静下来之后滚一次。
  function autoScroll() {
    if (catchingUp) return
    if (atBottom.value) scrollToBottom()
  }

  function rememberScroll(topicId: string | undefined) {
    const el = scrollRef.value
    // A hidden pane has no position to report; what is remembered stays.
    if (!el || !topicId || !el.clientHeight) return
    atBottom.value = isAtBottom(el)
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
      pendingTop = null
      if (saved && !saved.atBottom) {
        if (!el.clientHeight) {
          pendingTop = saved.top
          atBottom.value = false
          return
        }
        el.scrollTop = saved.top
        atBottom.value = isAtBottom(el)
      } else {
        el.scrollTop = el.scrollHeight
        atBottom.value = true
      }
    })
  }

  onScopeDispose(() => {
    if (catchUpTimer) clearTimeout(catchUpTimer)
    catchUpTimer = null
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
  }
}
