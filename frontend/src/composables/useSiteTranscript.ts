// 现场那一窗 transcript：读最近一页、往上翻更早的、过 MAX_WINDOW 封顶，以及 socket
// 上来的行落到哪里。从 `PanelSite.vue` 抽出来的一层——那一格贴着 1000 行的上限
// （.claude/rules/architecture.md），而「这一窗怎么长」本来就和「一行怎么画」是两件
// 事：props/events 那条边界，一直落在同一个文件里。
import type { Ref } from 'vue'
import type { Block } from '../cx_types'
import type { CapFocus } from '../lib/blockPaging'
import type { ScrollAnchor } from './useStickToBottom'

import { nextTick, reactive, ref } from 'vue'

import { getTranscript, SITE_PAGE_SIZE } from '../api'
import { capAround, MAX_WINDOW, scrollTopAfterPrepend, shouldLoadNewer, shouldLoadOlder } from '../lib/blockPaging'
import { beginMeasuredLayout, endMeasuredLayout } from '../lib/contentVisibility'
import { isForbidden } from '../lib/loadFailure'
import { cachedWindow, cursorOf, rememberWindow } from '../lib/siteWindow'

import { t } from '@/i18n'

/** 外面那一格和这一窗之间的接线：谁在滚、现在读的是谁、读回来的人往哪儿记。 */
export interface SiteTranscriptHooks {
  /** 现在开着哪段对话：房间的，或者任务的（没开就是 null）。 */
  topicId: () => string | null
  /** 这一格还在不在屏幕上：切走了就别在后台替它预取。 */
  active?: () => boolean
  /** 现在看的是谁：null = 全部（同 PanelSite 的 `viewing`）。 */
  viewing: () => string | null
  /** 这一窗自己的滚动容器。 */
  scrollRef: Ref<HTMLElement | null>
  /**
   * 读者是不是停在末尾。这个状态由他自己的滚动维护（`useStickToBottom`），不是
   * 每次更新时现量——请求在路上、内容在重排的时候量出来的那一瞬间都可能骗人。
   */
  following: Readonly<Ref<boolean>>
  /** 把这一窗钉到末尾。 */
  scrollToTail: () => void
  /** 记下此刻视口顶上那一行。 */
  captureAnchor: () => ScrollAnchor
  /** 内容换过之后把那一行放回原来的位置。 */
  restoreAnchor: (anchor: ScrollAnchor) => void
  /** 这一页/这一行又露面了哪些队友（PanelSite 的 `noteAgents`）。 */
  noteAgents: (blocks: Block[]) => void
  /** 每一轮从什么时候开始（PanelSite 的 `noteStarts`）。 */
  noteStarts: (starts: Record<string, string> | undefined) => void
}

export function useSiteTranscript(hooks: SiteTranscriptHooks) {
  const loading = ref(false)
  const errorMsg = ref<string | null>(null)
  // 401/403 是「不给你看」，不是「这次没读成」：那一格换成一句说清的话，且**不**
  // 给重试——再试一次还是同一个回答，摆一颗按了没用的按钮只会让人以为是自己点错
  // 了。判在这里（取数这一层），画面只收一个布尔值（docs/design-system.md §3.10）。
  const errorForbidden = ref(false)
  function fail(e: unknown): void {
    errorMsg.value = e instanceof Error ? e.message : t('work.room.site.loadFailed')
    errorForbidden.value = isForbidden(e)
  }
  function clearError(): void {
    errorMsg.value = null
    errorForbidden.value = false
  }

  // 现场: read-only transcript timeline. 手上这一窗是**当前视角**的：「全部」是房间里
  // 所有人的，「只看某个队友」时只有它自己的——过滤在服务端的分页里做，不是在读到
  // 的一页上再挑（那正是「切过去就等于加载全部」的来源）。
  const transcript = ref<Block[]>([])
  // 更早的现场还在库里没拉。和对话栏一样，只在读的人自己往上翻时才拉。
  const hasOlder = ref(false)
  // 手上这一窗的**最新**那一截被裁在了窗口下方（见 capSite）：读者往上翻着的时候裁
  // 的是离他最远的那个头，窗口的最尾就不再是最新的一条了。这里只记一个事实——下面还
  // 有、中间是断的：这时不能再从手上的游标往后读（接上去会在中间留一条从没取过的洞），
  // 读者回到末尾时整页重读一页最新的，窗口就重新连上了。他停在末尾时裁的是上面那一头，
  // 窗口的尾巴还连着最新的一条，这个记号不立，往后只取新行的那一路照常走。
  const hasNewer = ref(false)
  const loadingOlder = ref(false)
  let olderCursor: string | null = null
  // 这一窗最新那一条的游标：刷新时从这里往后读，而不是重读一整页。
  let newestCursor: string | null = null

  // 此刻才到的行（不是打开时先画出来的缓存、也不是往上翻出来的历史）：它们淡入
  // 一下，说明「刚来的是这条」。由 PanelSite 绑成 class，行的 `animationend` 清掉。
  //
  // 兜底也要有：记账行那样画不出一行的块永远不会触发 `animationend`（根本没有元素
  // 挂上去），只靠事件的话这个集合会一直攥着它。加进来时顺手排一个闹钟，演完了
  // 就没它的事。
  const arrived: Set<string> = reactive(new Set<string>())
  function arrive(id: string): void {
    arrived.add(id)
    setTimeout(() => arrived.delete(id), 1000)
  }

  // 手上这一窗是给哪个视角读的（null = 全部）。换视角时它和这一栏现在要读的东西就
  // 不是一回事了，重读时不能再和它合。
  let loadedFor: string | null = null
  // 手上这一窗是哪段对话的。换了对话又没读回来时，屏上那份是上一个对话的，失败要报出来。
  let loadedTopic: string | null = null

  //: 预取走在读者前面两页：打开时先多拉两页放在上面，之后每次读到边上，
  //: 后台再补到「手上这些 + 两页」。
  const PREFETCH_AHEAD = 2 * SITE_PAGE_SIZE
  //: 预取到此为止。窗口上限是 MAX_WINDOW，预取留一页给读者自己翻：涨到上限那一刻
  //: 裁掉的是离读者最远的那一头，预取补的那几页正是最近刚添上去的，别让它们当场被裁。
  const PREFETCH_CEILING = MAX_WINDOW - SITE_PAGE_SIZE

  // 一次只让一个「往上读」的请求在飞：读者翻到边上与后台预取可能同时要读上一页。
  let olderBusy = false

  /** 读上一页。`quiet` 是预取：不摆「加载更早的现场…」，读者看不见它。
   *  回来了没有（这一页真的合进窗口了）。 */
  async function loadOlder(quiet = false): Promise<boolean> {
    const tid = hooks.topicId()
    if (!tid || !olderCursor || olderBusy || !hasOlder.value) return false
    const author = hooks.viewing()
    olderBusy = true
    if (!quiet) loadingOlder.value = true
    try {
      const page = await getTranscript(tid, {
        limit: SITE_PAGE_SIZE,
        before: olderCursor,
        author,
      })
      // 读的人可能已经换了视角：这一页是上一位的，丢掉——合进来就是把别人的行塞进
      // 这个人的时间线。
      if (hooks.topicId() !== tid || hooks.viewing() !== author) return false
      hooks.noteAgents(page.data)
      hooks.noteStarts(page.turn_starts)
      // 量在行进去之前：一行的真实高度和它此刻报出来的可能不一样（长输出折起来、
      // 图片还没量到、离屏的块按估计高度占位），量出来的差补进去就会跳。测量帧让这一窗
      // 按真实高度铺开，量完再撤——见 lib/contentVisibility。位置在请求回来之后再取（和
      // 对话栏的分页器同一条理由：请求在飞的时候读的人可能还在滚，飞出前的位置会把他拉回去）。
      // 读者在不在末尾，按请求回来的这一刻说：他在等这一页的时候可能已经滚过。停在
      // 末尾的人往上多出一页不该把他挪走——钉回末尾；停在历史里的人按位置补。
      const following = hooks.following.value
      const el = hooks.scrollRef.value
      beginMeasuredLayout(el)
      const before = el ? { scrollTop: el.scrollTop, scrollHeight: el.scrollHeight } : null
      // 按 id 去重再往上拼：这一页的边界是「比游标更早」，和手上那一窗可能重叠（缓存
      // 的时候游标退到了一条更靠后的行上，见 `lib/siteWindow` 的兜底）。重叠的那几条
      // 已经在屏上，再拼一遍就是同一 id 出现两次——Vue 的列表会当场炸。
      const known = new Set(transcript.value.map((b) => b.id))
      transcript.value = [...page.data.filter((b) => !known.has(b.id)), ...transcript.value]
      hasOlder.value = page.has_more === true
      olderCursor = page.oldest_id ?? null
      // Prepending grows the content ABOVE the viewport; without this the reader
      // is thrown backwards by exactly that much (same fix as the chat's pager).
      await nextTick()
      if (following) hooks.scrollToTail()
      else if (el && before) el.scrollTop = scrollTopAfterPrepend(before, el.scrollHeight)
      endMeasuredLayout(el)
      // Trim AFTER the compensation, never before: the rows this drops are below the
      // viewport, so removing them moves nothing on screen, but they shrink
      // scrollHeight and compensating with a scrollHeight that already excludes them
      // pulls the reader up by their height on every page. See capAround.
      capSite()
      return true
    } catch (e) {
      // 读者自己翻的那一页失败了要报出来；后台预取失败不报——那几行还在屏上、
      // 也还是好的，把整栏换成一条红条等于一次读慢了就让人看不成现场（同 loadPage
      // 那条规矩）。预取的失败只是这一次没提前拿到，下次滚到边上再补。
      if (!quiet) fail(e)
      return false
    } finally {
      olderBusy = false
      if (!quiet) loadingOlder.value = false
    }
  }

  let prefetching = false
  /** 把上一页提前拿到手：读者往上翻时通常已经在屏上了。这次只有一页在飞，
   *  同一时刻不会和读者自己触发的那一页抢。 */
  async function prefetchOlder(): Promise<void> {
    if (prefetching) return
    prefetching = true
    try {
      const target = Math.min(PREFETCH_CEILING, transcript.value.length + PREFETCH_AHEAD)
      while ((hooks.active?.() ?? true) && hasOlder.value && !hasNewer.value && transcript.value.length < target) {
        if (!(await loadOlder(true))) break
      }
    } finally {
      prefetching = false
    }
  }

  /**
   * 手上这一窗涨过上限（`MAX_WINDOW`）时把它裁回来。裁哪一头由**读者此刻在哪儿**定
   * （`capAround` 的 `focus`）：停在末尾就留最新那一截；往上翻着就留他上下各半窗。
   *
   * 从前这里一律裁最新那一头——那是照着「往上翻的人不该丢刚拉上来的页」定的，可停在
   * 末尾的人被顺带裁了：新来的行一进窗口就被裁出去，他眼前的现场停在几分钟前不动，而
   * `hasNewer` 还立着，于是每三秒一次的刷新只能整页重读，重读完再裁、再停住。
   *
   * 裁掉最旧那一截不算丢：那几条还在库里，把往上翻的游标退到新的头一条就还能读回来
   * （`hasOlder` 立着）。裁掉最新那一截才是断了：窗口下面还有一段没取过的，
   * `hasNewer` 记下来，读者回到末尾时整页重读把它接上。
   */
  function capSite(): { droppedOlder: number; droppedNewer: number } | null {
    // 他在读哪一行：停在末尾时不必问（留最新那一截就是把他留在原地）；往上翻着就量
    // 一次视口顶上那一行。量不到（还没铺开、那一行刚被别处换掉）时退回留最旧那一截，
    // 那正是从前的规矩——至少不会把读者刚往上翻出来的历史删掉。
    let focus: CapFocus = 'newest'
    if (!hooks.following.value) {
      const anchor = hooks.captureAnchor()
      const at = anchor.id ? transcript.value.findIndex((b) => b.id === anchor.id) : -1
      focus = at >= 0 ? at : 'oldest'
    }
    const capped = capAround(transcript.value, MAX_WINDOW, focus)
    if (!capped) return null
    transcript.value = capped.keep
    if (capped.droppedNewer > 0) hasNewer.value = true
    if (capped.droppedOlder > 0) {
      hasOlder.value = true
      // 往上翻的起点跟着挪到新的头一条。这一条自己不带游标时退到窗口里最早一条带着
      // 游标的：多读一遍中间那几条会按 id 去掉，不会漏。一条都带不了游标就清掉起点
      // （往上翻这一路就停住），也不能留着旧的——旧游标指的是刚被裁掉的那一行，从它
      // 往回读会跳过被裁掉的那一段，中间留一个洞。
      olderCursor = transcript.value.map(cursorOf).find((c) => c !== null) ?? null
    }
    // 裁掉的那几条里有还没演完的「刚到的」：把它们的 id 一并忘掉，不然这个集合会一直
    // 攥着永远不再挂上的 id（`arrived` 只增不减）。
    const kept = new Set(transcript.value.map((b) => b.id))
    for (const id of [...arrived]) if (!kept.has(id)) arrived.delete(id)
    return { droppedOlder: capped.droppedOlder, droppedNewer: capped.droppedNewer }
  }

  // 和对话栏同一个判据：离顶还有一屏就开始拉上一页，已经有的人不用等。
  function onSiteScroll() {
    const el = hooks.scrollRef.value
    if (!el) return
    if (shouldLoadOlder(el.scrollTop, { hasMore: hasOlder.value, loading: loadingOlder.value })) {
      void loadOlder().then((landed) => {
        if (landed) void prefetchOlder()
      })
    }
    // 窗口被裁过、读的人又滚回了末尾：重读一页最新的把它接上（`load` 见 hasNewer 时
    // 会就地换成最新的一页，而不是往旧窗口上拼）。
    const fromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    if (shouldLoadNewer(fromBottom, { hasNewer: hasNewer.value, loading: loading.value })) {
      void load()
    }
  }

  // 一次只发一个「读最近一页」的请求。3 秒一次的刷新、每来一行 socket、一轮结束的
  // 重读，谁也拦不住谁：后端慢一点的时候它们会叠起来——每个都等别人让出连接和数据库
  // 连接，20 秒的读超时一到就翻成「请求等待超时」，而且先发出去的那个晚回来还会把
  // 新读的一页盖掉。请求在飞的时候只记下一个「还要再读一次」，回来之后再读。
  let loadingPage = false
  let reloadQueued = false

  // 这一栏读过一次之后，再读一遍是安静的：不换骨架屏、不跳回底部——换成骨架屏再
  // 换回来，就是整栏闪一下。判据是「读过一次」而不是「手上有几行」：一个还没人干过
  // 活的房间每读一次都回空，按行数判的话它就会每三秒把骨架屏摆出来一次，空状态和
  // 加载态来回闪。
  //
  // 每次读的都是**当前视角**的最近一页，和打开时一样。换了视角就是换了一个人的
  // 时间线：那一页到手之前手上那份还是上一位的（照旧渲染，不闪骨架屏），到手之后
  // 它整个换掉——不是往上摞。一个人可能有上千步，把上一位翻出来的历史带过去，就
  // 等于又「切一次加载全部」。
  //
  // 后台刷新失败不动屏上的东西：那几行还是好的，只是旧了三秒。把它们换成一条
  // 「请求等待超时」的红条，等于一次读慢了就让人看不成现场；下一次刷新会再试。
  // 打开（屏上还没有东西）、换视角、换对话这三种失败才报错——那几页本来就不在屏上。
  let loadedOnce = false
  async function loadPage() {
    const tid = hooks.topicId()
    if (!tid) return
    const author = hooks.viewing()
    const switched = author !== loadedFor
    const switchedTopic = tid !== loadedTopic
    // 第一次读：屏上当时什么都没有，摆骨架。之后一律安静地换。
    const firstRead = !loadedOnce
    // 打开、换视角、换对话：都算「重新开这一格」。前两种先把手上的那一窗画出来，
    // 这一帧就有人看的——有缓存时连骨架都不摆，它不是「还没读到」，只是旧了几秒。
    // 换对话必须进来：游标上一个对话的，不能接到新的上面。
    const opening = firstRead || switched || switchedTopic
    if (opening && errorMsg.value === null) loading.value = true
    let painted = false
    if (opening) {
      const held = cachedWindow(tid, author, SITE_PAGE_SIZE)
      if (held) {
        transcript.value = held.blocks
        hasOlder.value = held.hasMore
        olderCursor = held.oldestCursor
        newestCursor = held.newestCursor
        arrived.clear()
        hooks.noteAgents(held.blocks)
        loading.value = false
        hooks.scrollToTail()
        painted = true
      } else {
        // 这个视角没有手上的东西（第一次看，或换了对话）：游标一律从头来，别把上
        // 一段对话的接着用。屏上那几行照旧留着,直到这一页到手（`switched` 会把它
        // 换掉）——换成骨架屏再换回来，就是整栏闪一下。
        olderCursor = null
        newestCursor = null
      }
    }
    // 窗口被裁过、读的人又在历史中间：这一趟读回来的整页会被下面的守卫整个丢掉，
    // 白花一次请求（3 秒一次，能一直花下去）。他回到末尾时那次 load 才会真的接上，
    // 所以这里不必先发。
    if (hasNewer.value && !hooks.following.value && !firstRead && !switched && !switchedTopic) {
      return
    }
    // 接着问「从这一窗最新那条往后有什么」，而不是重读一整页：手上这一窗就是上一次
    // 读回来的，重读只会把同样的 120 条再传一遍。没有游标（第一次读、换了对话）时
    // 才整页读。窗口被裁过（`hasNewer`）也不走这一路——那时中间是断的，接不上。
    const forward = !hasNewer.value ? newestCursor : null
    try {
      const tx = await getTranscript(
        tid,
        forward ? { limit: SITE_PAGE_SIZE, after: forward, author } : { limit: SITE_PAGE_SIZE, author }
      )
      // 读的人可能已经又换了一个视角：这一页不是他现在要的，丢掉。骨架屏由下面的
      // finally 收掉，不能让它留在半路。
      if (hooks.topicId() !== tid || hooks.viewing() !== author) return
      loadedOnce = true
      loadedFor = author
      loadedTopic = tid
      clearError()
      hooks.noteAgents(tx.data)
      hooks.noteStarts(tx.turn_starts)
      // 跟不跟，读读者自己滚出来的那个状态，而不是此刻量一次：请求在路上的这几百毫秒
      // 里他可能已经往上翻了，而量到的那一下还停在底部——那就把他拽回去了。
      // 画过缓存的那一次不能再由「刚打开」来定：那一窗已经在屏上，读者完全可以已经
      // 在读它，这一刻他自己滚出来的状态说了算。
      const follow = painted ? hooks.following.value : switched || firstRead || hooks.following.value
      // 窗口下面被裁过、读的人还在历史中间：把最新的一页拼上去，会在两段之间留一个
      // 从没取过的洞。这时什么也别动——他回到末尾时，那次 load 会重新接上。
      if (!(hasNewer.value && !follow)) {
        // 不跟的时候先记下他读着的那一行：整窗换过之后浏览器可能已经把 scrollTop
        // 夹到了别处（见 useStickToBottom 的 ScrollAnchor）。
        const anchor = follow ? null : hooks.captureAnchor()
        if (forward !== null) {
          // 往前读的那一路：只往末尾接，**不碰** reader 的往上翻游标——它整份说的
          // 都是「上面还有没有」，而这一路从来不动它（`_newer_page` 里 has_more 恒
          // 为 false、oldest_id 恒为 null）。照抄过来的话，刚从缓存画出来的那一窗
          // 会当场失去往上翻的能力。
          appendSite(tx.data, follow && !opening)
          // 裁过之后，只要窗口的尾巴还是最新的一条（裁的是上面那一头，或者根本没涨过
          // 上限），游标就照常往前走，下一次只问后来的那几条。尾巴被裁掉的那一路（读者
          // 往上翻着、窗口下面断了一截）才停下——`capSite` 那时立起 `hasNewer`，接回
          // 走得靠「回到末尾时整页重读一页最新的」。
          const cut = capSite()
          if (!cut || cut.droppedNewer === 0) {
            // 空的一页也带着原样的游标（后端把它还回来），追平时手上这个不会丢。
            if (tx.newest_id !== undefined && tx.newest_id !== null) newestCursor = tx.newest_id
          }
        } else {
          const reset = switched || firstRead || switchedTopic || hasNewer.value
          // 换了对话就是另一条时间线：手上那几行一条都不能留下。
          transcript.value = mergeSite(tx.data, transcript.value, switched, switchedTopic)
          if (reset) {
            // 整页：上面有没有、读到哪儿，全以这一页为准。没有 `newest_id`（更旧的
            // 后端）就置空游标，下一次退回整页读。
            hasOlder.value = tx.has_more === true
            olderCursor = tx.oldest_id ?? null
          }
          newestCursor = tx.newest_id ?? null
          hasNewer.value = false
        }
        // Follow the tail on every open of a topic's 现场 — that is what "open on
        // the newest" means. 换视角也是「打开这个人的现场」，同样停在最新的那一条。
        if (follow) hooks.scrollToTail()
        else if (anchor) {
          await nextTick()
          hooks.restoreAnchor(anchor)
        }
        remember()
      }
      // 首屏已经在屏上了，再在后台把上面两页补出来：读者往上翻时不必等。
      void prefetchOlder()
    } catch (e) {
      if (firstRead || switched || switchedTopic) {
        fail(e)
      }
    } finally {
      loading.value = false
    }
  }

  /** 把这一次读回来的、比手上新的几条接在末尾。按 id 去重：往前读的边界取的是
   *  「那一刻及以后」，和手上可能有重叠。`animate` 时它们才淡入——读者停在末尾，
   *  这几条正是他此刻在看的；他在历史里时它们落在视口下方，动了也没人看见。 */
  function appendSite(rows: Block[], animate: boolean): void {
    if (!rows.length) return
    const known = new Set(transcript.value.map((b) => b.id))
    const fresh = rows.filter((b) => !known.has(b.id))
    if (!fresh.length) return
    if (animate) for (const b of fresh) arrive(b.id)
    transcript.value = [...transcript.value, ...fresh]
  }

  /** 把手上这一窗写给下一次打开。 */
  function remember(): void {
    const tid = hooks.topicId()
    if (!tid) return
    rememberWindow(
      tid,
      hooks.viewing(),
      {
        blocks: transcript.value,
        hasMore: hasOlder.value,
        oldestCursor: olderCursor,
        newestCursor,
      },
      SITE_PAGE_SIZE
    )
  }

  async function load(): Promise<void> {
    if (loadingPage) {
      reloadQueued = true
      return
    }
    loadingPage = true
    try {
      do {
        reloadQueued = false
        await loadPage()
      } while (reloadQueued)
    } finally {
      loadingPage = false
    }
  }

  /** 读者按下那颗「重试」：把上一次的失败收掉再读一次。
   *
   *  不收掉的话，`loadPage` 认的是「这一格已经报错了」，骨架屏不会出现（它只在
   *  `errorMsg` 为空时才摆），读者点下去看见的还是同一条红条——以为没反应。清掉
   *  之后这一趟就是一次普通的首次读：骨架屏摆出来，读回来就换上记录。 */
  function retry(): void {
    clearError()
    void load()
  }

  // 一页读回来的，和这一栏手上已有的，按 id 合成一份。读的请求在路上的时候 socket
  // 又送来了几行，它们比这一页新：留着，接在后面。手上那份里比这一页最老的一行还
  // 老的（往上翻出来的更早的记录），也留着。
  //
  // `switched` 是另一回事：手上那份是上一个视角的，它的「更早」不是这个人的更早。
  // 那时只留请求在路上时新到的几行 —— 比这一页最新的一条还新的，是真的还没进这一页
  // 的几步，不是翻出来的历史。
  //
  // `elsewhere` 更彻底：手上那份是**另一段对话**的。那几行一条也不属于这一条，连
  // 「刚到的」都不该留（`switched` 的「比最新一条还新」在这里没有意义，时间轴根本
  // 不是同一条）。换对话时整份丢掉。
  function mergeSite(page: Block[], held: Block[], switched = false, elsewhere = false): Block[] {
    if (elsewhere) return page
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
    // 一行属于哪段对话：任务里的带着任务的 id，房间自己的没有。
    if (block.conversation_id !== hooks.topicId() || block.kind !== 'event') return
    // Native activity is read from the journal projection, never from blocks.
    if (!('run_record' in block)) {
      void load()
      return
    }
    // 名册先记上：别的队友在干活，tab 得出现（切过去时才读得到它的记录）。
    hooks.noteAgents([block])
    const viewing = hooks.viewing()
    if (viewing !== null && block.author !== viewing) return
    const at = transcript.value.findIndex((b) => b.id === block.id)
    if (at >= 0) {
      const follow = hooks.following.value
      const anchor = follow ? null : hooks.captureAnchor()
      const next = transcript.value.slice()
      // socket 的 `run_record` 帧不带游标（只有读回来的那一页带）。原地换掉时把手上
      // 那条的游标留着：缓存写回去要靠它定位，丢了就等于告诉下次打开「上面没有更早
      // 的了」，往上翻整场失效。
      const cursor = (next[at].meta as { cursor?: unknown } | null | undefined)?.cursor
      const merged = cursor === undefined ? block : { ...block, meta: { ...block.meta, cursor } }
      next[at] = merged
      transcript.value = next
      if (follow) hooks.scrollToTail()
      else if (anchor) void nextTick(() => hooks.restoreAnchor(anchor))
      return
    }
    // 窗口被裁过：这一行落在窗口下面那一段里，接上去中间就是断的。读的人已经回到末尾
    // 的话，重读一页最新的把窗口接上；否则放着，等他回来。
    if (hasNewer.value) {
      if (hooks.following.value) void load()
      return
    }
    const follow = hooks.following.value
    const anchor = follow ? null : hooks.captureAnchor()
    const time = Date.parse(block.created_at)
    const next = transcript.value.slice()
    // 几乎总是接在末尾；偶尔晚到的一行按时间插回它的位置（一轮以它记下的时间排）。
    let i = next.length
    while (i > 0 && Date.parse(next[i - 1].created_at) > time) i -= 1
    next.splice(i, 0, block)
    transcript.value = next
    // 读者停在末尾时这一行就在他眼前落下来，淡入一下说明刚来的是它；他在历史里
    // 时它落在视口下方，没人看见，别动。`hasNewer` 那一路上面已经先返回了。
    if (follow) {
      arrive(block.id)
      hooks.scrollToTail()
    } else if (anchor) void nextTick(() => hooks.restoreAnchor(anchor))
    remember()
  }

  return {
    transcript,
    hasOlder,
    loading,
    loadingOlder,
    errorMsg,
    errorForbidden,
    arrived,
    load,
    retry,
    onSiteScroll,
    receive,
  }
}
