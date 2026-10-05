// 现场那一窗 transcript：读最近一页、往上翻更早的、过 MAX_WINDOW 封顶，以及 socket
// 上来的行落到哪里。从 `PanelSite.vue` 抽出来的一层——那一格贴着 1000 行的上限
// （.claude/rules/architecture.md），而「这一窗怎么长」本来就和「一行怎么画」是两件
// 事：props/events 那条边界，一直落在同一个文件里。
import type { Ref } from 'vue'
import type { Block } from '../cx_types'

import { nextTick, ref } from 'vue'

import { getTranscript, SITE_PAGE_SIZE } from '../api'
import { capWindow, scrollTopAfterPrepend, shouldLoadNewer, shouldLoadOlder } from '../lib/blockPaging'
import { beginMeasuredLayout, endMeasuredLayout } from '../lib/contentVisibility'
import { shouldKeepPinning } from '../lib/siteLog'

import { t } from '@/i18n'

/** 外面那一格和这一窗之间的接线：谁在滚、现在读的是谁、读回来的人往哪儿记。 */
export interface SiteTranscriptHooks {
  /** 现在开着哪个话题（没开就是 null）。 */
  topicId: () => string | null
  /** 读的是房间里哪个任务的记录：null = 房间自己那一条线。 */
  taskId: () => string | null
  /** 现在看的是谁：null = 全部（同 PanelSite 的 `viewing`）。 */
  viewing: () => string | null
  /** 这一窗自己的滚动容器。 */
  scrollRef: Ref<HTMLElement | null>
  /** 这一页/这一行又露面了哪些队友（PanelSite 的 `noteAgents`）。 */
  noteAgents: (blocks: Block[]) => void
  /** 每一轮从什么时候开始（PanelSite 的 `noteStarts`）。 */
  noteStarts: (starts: Record<string, string> | undefined) => void
}

export function useSiteTranscript(hooks: SiteTranscriptHooks) {
  const loading = ref(false)
  // 房间自己那条线不带 task；任务的才带。
  const taskScope = () => {
    const task = hooks.taskId()
    return task ? { task } : {}
  }
  const errorMsg = ref<string | null>(null)

  // 现场: read-only transcript timeline. 手上这一窗是**当前视角**的：「全部」是房间里
  // 所有人的，「只看某个队友」时只有它自己的——过滤在服务端的分页里做，不是在读到
  // 的一页上再挑（那正是「切过去就等于加载全部」的来源）。
  const transcript = ref<Block[]>([])
  // 更早的现场还在库里没拉。和对话栏一样，只在读的人自己往上翻时才拉。
  const hasOlder = ref(false)
  // 手上这一窗涨过上限以后，**最新**的那一截被裁在了窗口下方（见 capSite）。对话栏
  // 把裁下来的那一截收在 `newestHeld` 里、靠按游标取回来的下一页接上去；现场没有
  // 「取更新的」那一路，所以这里只记一个事实：窗口下面还有、而且中间是断的。读的人
  // 回到末尾时重读一页最新的，窗口就重新连上了。
  const hasNewer = ref(false)
  const loadingOlder = ref(false)

  // 手上这一窗是给哪个视角读的（null = 全部）。换视角时它和这一栏现在要读的东西就
  // 不是一回事了，重读时不能再和它合。
  let loadedFor: string | null = null

  // 读的人是不是正看着最底下。新的一行来了，只有这时才跟着往下走；往上翻着看旧记录
  // 的人，不能被一行新的拽回底部。
  function atTail(): boolean {
    const el = hooks.scrollRef.value
    return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 48
  }

  // A single `scrollTop = scrollHeight` at nextTick does NOT work here, which is
  // how this shipped broken the first time: the panel renders its spinner first,
  // so the container is one viewport tall with nothing to scroll, the assignment
  // clamps to 0, and the timeline lays out underneath — leaving the reader on the
  // oldest entry, the exact bug this exists to fix. Measured on the deployed page:
  // scrollHeight 500 at +40ms, 2066 at +120ms, scrollTop 0 throughout.
  // So keep re-pinning while the height is still moving (see shouldKeepPinning).
  function scrollSiteToTail(): void {
    let lastHeight = -1
    let frames = 0
    const pin = (): void => {
      const el = hooks.scrollRef.value
      if (!el) return
      el.scrollTop = el.scrollHeight
      if (shouldKeepPinning(el.scrollHeight, lastHeight, frames)) {
        lastHeight = el.scrollHeight
        frames += 1
        requestAnimationFrame(pin)
      }
    }
    nextTick(() => requestAnimationFrame(pin))
  }

  async function loadOlder() {
    const tid = hooks.topicId()
    const oldest = transcript.value[0]
    if (!tid || !oldest || loadingOlder.value || !hasOlder.value) return
    const author = hooks.viewing()
    loadingOlder.value = true
    try {
      const page = await getTranscript(tid, {
        ...taskScope(),
        limit: SITE_PAGE_SIZE,
        before: oldest.id,
        author,
      })
      // 读的人可能已经换了视角：这一页是上一位的，丢掉——合进来就是把别人的行塞进
      // 这个人的时间线。
      if (hooks.topicId() !== tid || hooks.viewing() !== author) return
      hooks.noteAgents(page.data)
      hooks.noteStarts(page.turn_starts)
      // 量在行进去之前：这一窗里的离屏行带着 content-visibility（PanelSite 的
      // .site-act），报的是估计高度，量出来的差补进去就会跳。测量帧让这一窗按真实高度
      // 铺开，量完再撤——见 lib/contentVisibility。位置在请求回来之后再取（和对话栏的
      // 分页器同一条理由：请求在飞的时候读的人可能还在滚，飞出前的位置会把他拉回去）。
      const el = hooks.scrollRef.value
      beginMeasuredLayout(el)
      const before = el ? { scrollTop: el.scrollTop, scrollHeight: el.scrollHeight } : null
      transcript.value = [...page.data, ...transcript.value]
      hasOlder.value = page.has_more === true
      // Prepending grows the content ABOVE the viewport; without this the reader
      // is thrown backwards by exactly that much (same fix as the chat's pager).
      await nextTick()
      const sc = hooks.scrollRef.value
      if (sc && before) sc.scrollTop = scrollTopAfterPrepend(before, sc.scrollHeight)
      endMeasuredLayout(el)
      // Trim AFTER the compensation, never before: the rows this drops are below the
      // viewport, so removing them moves nothing on screen, but they shrink
      // scrollHeight and compensating with a scrollHeight that already excludes them
      // pulls the reader up by their height on every page. See capWindow.
      capSite()
    } catch (e) {
      errorMsg.value = e instanceof Error ? e.message : t('work.room.site.loadFailed')
    } finally {
      loadingOlder.value = false
    }
  }

  /**
   * 手上这一窗涨过上限时，把**最新**的那一截从屏上挪走（`capWindow` 保留最旧的
   * `MAX_WINDOW` 条，和对话栏同一个数、同一段理由）。挪走而不是删数据：读的人正往上
   * 翻，这一截本来就在视口下方；而且他是往上翻的，能继续往前翻的前提恰恰是**最旧的
   * 那一头不动**——从那一头裁，就等于把他刚拉上来的页当场删掉，翻到哪儿删到哪儿。见
   * components/room/composables/useTimeline 的 `capNewest`。
   *
   * 裁过之后窗口最尾就不再是最新的一条：下面还有，中间还是断的。`hasNewer` 记下这件
   * 事——现场没有「取更新的」那一路，不能像对话栏那样按游标把它接回来，只能在读的人
   * 回到末尾时重读一页最新的。
   */
  function capSite(): void {
    const capped = capWindow(transcript.value)
    if (!capped) return
    transcript.value = capped.keep
    hasNewer.value = true
  }

  // 和对话栏同一个判据：离顶还有一屏就开始拉上一页，已经有的人不用等。
  function onSiteScroll() {
    const el = hooks.scrollRef.value
    if (!el) return
    if (shouldLoadOlder(el.scrollTop, { hasMore: hasOlder.value, loading: loadingOlder.value })) {
      void loadOlder()
    }
    // 窗口被裁过、读的人又滚回了末尾：重读一页最新的把它接上（`load` 见 hasNewer 时
    // 会就地换成最新的一页，而不是往旧窗口上拼）。
    const fromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    if (shouldLoadNewer(fromBottom, { hasNewer: hasNewer.value, loading: loading.value })) {
      void load()
    }
  }

  // 这一栏已经有东西时，再读一遍是安静的：不换骨架屏、不跳回底部——换成骨架屏再
  // 换回来，就是整栏闪一下。
  //
  // 每次读的都是**当前视角**的最近一页，和打开时一样。换了视角就是换了一个人的
  // 时间线：那一页到手之前手上那份还是上一位的（照旧渲染，不闪骨架屏），到手之后
  // 它整个换掉——不是往上摞。一个人可能有上千步，把上一位翻出来的历史带过去，就
  // 等于又「切一次加载全部」。
  async function load() {
    const tid = hooks.topicId()
    if (!tid) return
    const author = hooks.viewing()
    const switched = author !== loadedFor
    const quiet = transcript.value.length > 0
    const follow = switched || !quiet || atTail()
    if (!quiet) loading.value = true
    errorMsg.value = null
    try {
      const tx = await getTranscript(tid, { ...taskScope(), limit: SITE_PAGE_SIZE, author })
      // 读的人可能已经又换了一个视角：这一页不是他现在要的，丢掉。骨架屏由下面的
      // finally 收掉，不能让它留在半路。
      if (hooks.topicId() !== tid || hooks.viewing() !== author) return
      loadedFor = author
      hooks.noteAgents(tx.data)
      hooks.noteStarts(tx.turn_starts)
      // 窗口下面被裁过、读的人还在历史中间：把最新的一页拼上去，会在两段之间留一个
      // 从没取过的洞。这时什么也别动——他回到末尾时，那次 load 会重新接上。
      if (!(hasNewer.value && !follow)) {
        transcript.value = mergeSite(tx.data, transcript.value, switched)
        hasNewer.value = false
        if (switched || !quiet) hasOlder.value = tx.has_more === true
      }
      // Follow the tail on every open of a topic's 现场 — that is what "open on
      // the newest" means. 换视角也是「打开这个人的现场」，同样停在最新的那一条。
      if (follow) scrollSiteToTail()
    } catch (e) {
      errorMsg.value = e instanceof Error ? e.message : t('work.room.site.loadFailed')
    } finally {
      loading.value = false
    }
  }

  // 一页读回来的，和这一栏手上已有的，按 id 合成一份。读的请求在路上的时候 socket
  // 又送来了几行，它们比这一页新：留着，接在后面。手上那份里比这一页最老的一行还
  // 老的（往上翻出来的更早的记录），也留着。
  //
  // `switched` 是另一回事：手上那份是上一个视角的，它的「更早」不是这个人的更早。
  // 那时只留请求在路上时新到的几行 —— 比这一页最新的一条还新的，是真的还没进这一页
  // 的几步，不是翻出来的历史。
  function mergeSite(page: Block[], held: Block[], switched = false): Block[] {
    if (!page.length) return held.length ? held : page
    const ids = new Set(page.map((b) => b.id))
    const first = Date.parse(page[0].created_at)
    const last = Date.parse(page[page.length - 1].created_at)
    // 窗口被裁过：手上这窗最尾的一条已经远远老于这一页最头的一条，两边也没有一条共
    // 用。说明它们之间那几页是被裁掉的、不是取过的——拼起来会在中间留一个洞。这时只
    // 留新读的这一页（同 blockPaging 的 mergeRefreshedTail：两段接不上就丢掉旧的那一
    // 半）。`switched` 那一路本来就不留旧的一半，不必再判。
    const overlaps = held.some((b) => ids.has(b.id))
    if (!switched && held.length > 0 && !overlaps && Date.parse(held[held.length - 1].created_at) < first) {
      return [...page, ...held.filter((b) => !ids.has(b.id) && Date.parse(b.created_at) >= last)]
    }
    const older = switched ? [] : held.filter((b) => !ids.has(b.id) && Date.parse(b.created_at) < first)
    const newer = held.filter((b) => !ids.has(b.id) && Date.parse(b.created_at) >= last)
    return [...older, ...page, ...newer]
  }

  /**
   * socket 上来的一行（对话栏收到，一路转过来）：新的接在末尾，已有的原地换掉。
   * 别的话题的、别的对话（房间或任务）的、不是事件的，都不归这一栏。只看一个队友时，别人的行
   * 也不归这一栏——手上这条时间线就是那个人的。
   */
  function receive(block: Block): void {
    if (block.topic_id !== hooks.topicId() || block.kind !== 'event' || (block.task_id ?? null) !== hooks.taskId())
      return
    // 名册先记上：别的队友在干活，tab 得出现（切过去时才读得到它的记录）。
    hooks.noteAgents([block])
    const viewing = hooks.viewing()
    if (viewing !== null && block.author !== viewing) return
    const at = transcript.value.findIndex((b) => b.id === block.id)
    if (at >= 0) {
      const next = transcript.value.slice()
      next[at] = block
      transcript.value = next
      return
    }
    // 窗口被裁过：这一行落在窗口下面那一段里，接上去中间就是断的。读的人已经回到末尾
    // 的话，重读一页最新的把窗口接上；否则放着，等他回来。
    if (hasNewer.value) {
      if (atTail()) void load()
      return
    }
    const follow = atTail()
    const time = Date.parse(block.created_at)
    const next = transcript.value.slice()
    // 几乎总是接在末尾；偶尔晚到的一行按时间插回它的位置（一轮以它记下的时间排）。
    let i = next.length
    while (i > 0 && Date.parse(next[i - 1].created_at) > time) i -= 1
    next.splice(i, 0, block)
    transcript.value = next
    if (follow) scrollSiteToTail()
  }

  return { transcript, hasOlder, loading, loadingOlder, errorMsg, load, onSiteScroll, receive }
}
