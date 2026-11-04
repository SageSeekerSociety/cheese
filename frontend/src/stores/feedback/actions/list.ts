/**
 * 读的那一侧：词表、计数、**公开列表**、**我的反馈**。
 *
 * 放在一个模块里，是因为它们改的是同一批 state，而且共用同一套「哪次响应算数」的
 * 办法 —— 每条路自己的请求序号（`listSeq` / `mineSeq`）加那把跨路的计数修订号
 * （`session.countsRev`）。这些判断只有写在一处才看得出彼此的关系：先发的那次后到，
 * 屏幕上就是数字跳回去一次、或者两份清单拼在一起。
 *
 * 每个动作都显式写着 `this: ListSelf`。动作体搬出了 `stores/feedback.ts`，类型系统
 * 得知道它们跑起来时手上是什么；`ListSelf` 只列这一组真正用到的字段，少写一个（或
 * store 上少了一个）调用点当场就红。
 */

import type { FeedbackListQuery } from '@/api'
import type { FeedbackCard, FeedbackCounts, FeedbackKind, FeedbackMeta, FeedbackStatus } from '@/cx_types'
import type { FeedbackTab } from '../types'

import { appendPage, message, PAGE_SIZE, SEARCH_DEBOUNCE_MS } from '../helpers'
import { session, takeCountsRev } from '../session'

import { getFeedbackCounts, getFeedbackMeta, listFeedback, listMyFeedback } from '@/api'
import { t } from '@/i18n'
import { statusesForTab } from '@/lib/feedbackMeta'

interface ListSelf {
  meta: FeedbackMeta | null
  metaChecked: boolean
  counts: FeedbackCounts
  items: FeedbackCard[]
  total: number
  loading: boolean
  loadingMore: boolean
  listRequested: number
  listRefreshing: boolean
  query: string
  tab: FeedbackTab
  filterAuthor: string
  filterKind: FeedbackKind | null
  filterStatus: FeedbackStatus | null
  filterDays: number | null
  mineItems: FeedbackCard[]
  mineTotal: number
  mineLoading: boolean
  mineLoadingMore: boolean
  mineRequested: number
  mineRefreshing: boolean
  error: string | null
  listHasMore: boolean
  mineHasMore: boolean
  _commitCounts(next: FeedbackCounts, rev: number): void
  _listQuestion(): { key: string; params: FeedbackListQuery }
  /** 改筛选 / 切 Tab / 清搜索之后重拉第一页 —— 定义在同组的 `loadList`。 */
  loadList(): Promise<void>
}

export const listActions = {
  /* ---- 词表 ---- */

  /** 拉词表和「我是不是管理员」。页面挂载时调一次；失败不致命 —— 颜色和标签
   *  本地都有一份，梯子也有兜底，所以这里不把错误抛给页面。 */
  async loadMeta(this: ListSelf): Promise<void> {
    try {
      this.meta = await getFeedbackMeta()
    } catch {
      // 见上：词表拿不到不该让整页空白。
    } finally {
      // 失败也算「问过」：否则页面会永远停在「正在确认权限」，比看到一句
      // 「你的账号不在管理员名单里」更难查。
      this.metaChecked = true
    }
  },

  /* ---- 计数 ---- */

  /** `counts` 的**唯一**写入口 —— 全文件只有这一个地方写 `this.counts`。
   *
   *  为什么是一个动作而不是几处赋值：那几处各自只回答「我这份数字是何时问到的」，
   *  没有一处知道别处的事，于是「先发的后到」这种顺序问题只能靠每个调用点自己
   *  小心；漏一个的表现是屏幕上的未读数跳回去一次、下一轮刷新又自己好了 ——
   *  最难查的那一种。
   *
   *  `rev` 要在**发出请求那一刻**用 `takeCountsRev()` 领，不能等拿到响应再领：
   *  那时候领到的号说的是「我什么时候回来的」，正好是这里要挡的那件事。本地
   *  动作（推已读游标）没有请求可等，当场领一个 —— 它就是此刻最新的东西。 */
  _commitCounts(this: ListSelf, next: FeedbackCounts, rev: number): void {
    // 号相等时放行（`<` 而不是 `<=`）：同一个号只可能来自同一次领号。
    // 号大的赢，与到达顺序无关。
    if (rev < session.countsRev) return
    session.countsRev = rev
    this.counts = next
  },

  /** 重新问一次计数。失败不报错：Tab 上的数字晚一拍更新，不值得打断人。
   *
   *  这条路上**只有计数**，没有一个列表可以顺手替它做顺序判断，所以它完全靠
   *  `_commitCounts` 那把号：晚发的不会被早发的盖掉。
   */
  async refreshCounts(this: ListSelf): Promise<void> {
    const rev = takeCountsRev()
    try {
      const counts = await getFeedbackCounts()
      this._commitCounts(counts, rev)
    } catch {
      // 见上。
    }
  },

  /** 清掉上一次失败的原话。页面上的错误条靠它自己的关闭按钮调 —— 不这么做的话，
   *  一次失败会一直挂在页面上，直到下一个动作顺手把它覆盖。 */
  clearError(this: ListSelf): void {
    this.error = null
  },

  /* ---- 列表 ---- */

  /** 拉当前 Tab + 搜索词的那一页。
   *
   *  **命中缓存的定义是「问的还是同一个问题」**（Tab + 搜索词）。命中就先把手上
   *  那一页画出来（`loading` 保持 false，不闪骨架），然后**照样重拉一次**：缓存
   *  只是先画，不是答案 —— 这中间别人可能提了新的、管理员可能改了状态，而这一页
   *  上没有任何东西能知道。回来之后照常替换，有没有缓存走的是同一条路。
   */
  /** 公开列表这一趟问的**是哪个问题** —— 栏位 + 搜索词 + 四个筛选，一次算成
   *  `{key, params}`：`key` 是缓存指纹，`params` 是请求体。
   *
   *  **两样必须同源，而且只有这一处**。上一版把它们分头写在 `loadList` 里，于是
   *  `loadMoreList` 自己手写了一份只有 `{tab, q}` 的请求 —— 表现是「第 2 页起筛选
   *  悄悄失效，不带筛选的行拼在筛选结果下面」，而两份各自看着都对。指纹漏掉筛选的
   *  表现则是「换一个筛选、界面换了、列表还是上一份缓存」。
   */
  _listQuestion(this: ListSelf): { key: string; params: FeedbackListQuery } {
    const q = this.query.trim()
    const author = this.filterAuthor.trim()
    // 「最近 N 天」在这里折成一个**时刻**发给服务端：窗口的对齐由服务端那套
    // （半开的 UTC 日）说了算，前端只负责说「从现在往回 N 天」。
    const since = this.filterDays === null ? null : new Date(Date.now() - this.filterDays * 86400_000).toISOString()
    return {
      key: [this.tab, q, author, this.filterKind, this.filterStatus, since].join('\u0000'),
      params: {
        tab: this.tab,
        q,
        pageSize: PAGE_SIZE,
        author,
        kind: this.filterKind,
        status: this.filterStatus,
        since,
      },
    }
  },

  async loadList(this: ListSelf): Promise<void> {
    const seq = ++session.listSeq
    // 号要在**发请求之前**领，见 `_commitCounts`。
    const rev = takeCountsRev()
    const { key, params } = this._listQuestion()
    const cached = session.listCache?.key === key ? session.listCache : null
    if (cached) {
      this.items = cached.items
      this.total = cached.total
      // 缓存里那一份就是「上一次翻到哪儿」——偏移量接着它走，不接着这次还在飞的
      // 第一页走（`listRefreshing` 保证这两件事不会同时在跑）。
      this.listRequested = cached.items.length
      this.loading = false
      this.listRefreshing = true
    } else {
      this.loading = true
    }
    this.error = null
    try {
      const page = await listFeedback(params)
      // 只有最后一次请求的结果算数：防抖挡不住「先发的那次后到」。
      if (seq !== session.listSeq) return
      this.items = page.data
      this.total = page.total
      this.listRequested = page.data.length
      session.listCache = { key, items: page.data, total: page.total }
      this._commitCounts(page.counts, rev)
    } catch (error) {
      if (seq !== session.listSeq) return
      this.error = message(error, t('feedback.errors.listLoadFailed'))
      // 失败回到「一条都没有 + 一句错」那一态，**和没有缓存时完全一样** ——
      // 页面只有那几种状态（加载中 / 有内容 / 一条都没有 / 拉失败），让旧内容
      // 和错误条同时挂着是第五种，没人设计过它，而「重试」那个按钮正好长在空
      // 态里：不清空就等于把它一起藏起来。
      this.items = []
      this.total = 0
      this.listRequested = 0
      session.listCache = null
    } finally {
      // 两个标志只有**最后那一次**请求收得回来：被顶掉的那次提前关掉的话，正在
      // 飞的那次重拉就没人替它关门了（`listRefreshing` 只在缓存命中那一路置位）。
      if (seq === session.listSeq) {
        this.loading = false
        this.listRefreshing = false
      }
    }
  },

  /** 切栏位。必然重新拉 —— 栏位是**服务端**的筛选，本地过滤是第二份实现。
   *
   *  原先选中的状态如果在新栏位里不可能出现（比如「已收录」再点「已完成」），
   *  **这一次就一起清掉**，和换栏位合成同一次请求。分两步（先发一次、清掉再发）
   *  会先拉回一个必然空的页，而那一页正是「看着空列表猜为什么」的来源。 */
  setTab(this: ListSelf, tab: FeedbackTab): void {
    if (this.tab === tab) return
    this.tab = tab
    if (this.filterStatus !== null && !statusesForTab(tab, this.meta?.statuses).includes(this.filterStatus)) {
      this.filterStatus = null
    }
    void this.loadList()
  },

  /** 改一个筛选就重拉。**不防抖**：下拉是离散的一次选择，不像搜索那样每敲一个字
   *  都变；而这一下必然换一批数据，本地筛是第二份实现。 */
  setFilter(
    this: ListSelf,
    patch: {
      author?: string
      kind?: FeedbackKind | null
      status?: FeedbackStatus | null
      days?: number | null
    }
  ): void {
    if (patch.author !== undefined) this.filterAuthor = patch.author
    if (patch.kind !== undefined) this.filterKind = patch.kind
    if (patch.status !== undefined) this.filterStatus = patch.status
    if (patch.days !== undefined) this.filterDays = patch.days
    void this.loadList()
  },

  /** 有筛选在生效没有。页面据此决定要不要画「清除筛选」那一颗。 */
  hasFilters(this: ListSelf): boolean {
    return (
      this.filterAuthor.trim() !== '' ||
      this.filterKind !== null ||
      this.filterStatus !== null ||
      this.filterDays !== null
    )
  },

  /** 一次清干净：**栏位、搜索词、四个筛选**，然后拉一次。
   *
   *  **只发一次请求**，不是清完让每个控件各自触发一遍（那会发四个，而且中间三个
   *  页面都停在半筛状态）。栏位和搜索词也在内，是因为空态那颗「清除筛选」和筛选条
   *  那颗调的是同一个动作：被筛选筛空的人按它，期望的是回到「什么都没筛」的样子。
   */
  clearFilters(this: ListSelf): void {
    this.filterAuthor = ''
    this.filterKind = null
    this.filterStatus = null
    this.filterDays = null
    this.tab = 'all'
    this.query = ''
    void this.loadList()
  },

  /** 改搜索词。防抖之后重新拉：搜索也在服务端，它搜的字段比前端能拿到的那几个多。 */
  setQuery(this: ListSelf, query: string): void {
    this.query = query
    if (session.searchTimer) clearTimeout(session.searchTimer)
    session.searchTimer = setTimeout(() => {
      session.searchTimer = null
      void this.loadList()
    }, SEARCH_DEBOUNCE_MS)
  },

  /** 只改搜索词、不发请求。用在「清除筛选」上：那个动作要立刻回到全量，
   *  而不是等 250ms（点了按钮却没动静，读起来像坏了）。 */
  async clearQuery(this: ListSelf): Promise<void> {
    this.query = ''
    if (session.searchTimer) clearTimeout(session.searchTimer)
    session.searchTimer = null
    await this.loadList()
  },

  /** 取列表的下一页，接在手上这份后面。
   *
   *  **四道门都在这里，不在按钮上**：`disabled` 只挡得住鼠标，回车、快速双击、
   *  以及慢响应期间的那一下都会绕过去。四道各挡一个具体的坏结果 ——
   *  `loadingMore` 挡「同一段追加两遍」，`loading` 挡「第一页还在飞的时候偏移量
   *  按旧清单算、屏幕上出现一段谁也记不得的空档」，`listRefreshing` 挡同一件事的
   *  另一半（缓存命中时 `loading` 是故意不上锁的，见那个字段），`!listHasMore`
   *  挡「到底了还在取」。 */
  async loadMoreList(this: ListSelf): Promise<void> {
    if (this.loadingMore || this.loading || this.listRefreshing) return
    if (!this.listHasMore) return
    // 这一页属于**发它时那个问题**（栏位 + 搜索词）。`seq` 是那一刻的号：栏位或
    // 搜索词在飞的这段时间里变了的话，回来的是上一个问题的第 N 页，接上去就是
    // 两份清单拼在一起 —— 所以它回来了也只配丢掉。
    const seq = session.listSeq
    const rev = takeCountsRev()
    // **问题只有一份**（见 `_listQuestion`）。上一版这一行是手写的 `{tab, q, …}`，
    // 于是第 2 页起**四个筛选整个丢掉**：筛选结果后面接着一堆不匹配的行，而两份
    // 各自看着都对。
    const { key, params } = this._listQuestion()
    // 偏移量是**取回来过的行数**，不是 `items.length`（见 `listRequested`）。
    const pageStart = this.listRequested
    this.loadingMore = true
    try {
      const page = await listFeedback({ ...params, pageStart })
      if (seq !== session.listSeq) return
      this.items = appendPage(this.items, page.data)
      this.total = page.total
      // 服务端这次真的交出了几行，偏移量就往前走几行 —— 去重丢掉的那几行也算
      // 「服务端已经给过了」，重复要它们才是这一页卡死的原因。
      this.listRequested += page.data.length
      // 缓存跟着长：翻进详情页再退回来时，用户已经展开的那几页还在，不必从头再
      // 点一遍。代价是下一次 `loadList` 命中它之后会重拉第一页、把长度收回 50 ——
      // 这是「缓存只是先画」那句话本来就承认的事（见 `loadList`）。
      session.listCache = { key, items: this.items, total: page.total }
      this._commitCounts(page.counts, rev)
    } catch (error) {
      if (seq !== session.listSeq) return
      // **不清空已经画出来的那些** —— 这一点和 `loadList` 的失败处理正好相反，
      // 因为失败的东西不一样：那里失败的是「整页的内容」，这里只是「再多一点」。
      // 翻页失败退回第一页，是拿「看不见任何东西」去换一个本来就没拿到的增量。
      // 两个页面都已经会画「列表非空时的失败」（error + 有内容 → 列表上方一条），
      // 所以这句话放出去有人接。
      this.error = message(error, t('feedback.errors.loadMoreFailed'))
    } finally {
      // 无条件还门：这个标志说的是「这一次调用在飞」，和 `seq` 是不是还是自己
      // 无关。挂在 seq 上的话，一次被栏位切换作废的翻页会把门永远锁上。
      this.loadingMore = false
    }
  },

  /* ---- 我的反馈 ---- */

  /** 拉「和我有关的那一摞」：我提的 + 我替谁提的 + 指派给我的。三个来源合成一个
   *  清单是**服务端**的定义（`/feedback/mine`），前端不再分栏 —— 分栏要有「这条
   *  为什么在我的清单里」，而那个字段服务端不给；按 handle 在前端猜一遍等于把
   *  可见性规则抄第二份，抄错的那天就是有人看见不该看见的条目。
   *
   *  访客（没登录）拿到的也是这一条路：服务端回空列表而不是 401，所以这里和公开
   *  列表一样只是「没有」。 */
  async loadMine(this: ListSelf): Promise<void> {
    const seq = ++session.mineSeq
    // 这一页没有参数，所以「问的还是同一个问题」永远成立 —— 有就拿去先画。
    // 它回答的是「我提的/指派给我的」，所以**身份一变就必须已经清掉**：
    // `resetFeedbackCaches()` 管这件事。
    const cached = session.mineCache
    if (cached) {
      this.mineItems = cached.items
      this.mineTotal = cached.total
      this.mineRequested = cached.items.length
      this.mineLoading = false
      this.mineRefreshing = true
    } else {
      this.mineLoading = true
    }
    this.error = null
    try {
      const page = await listMyFeedback({ pageSize: PAGE_SIZE })
      if (seq !== session.mineSeq) return
      this.mineItems = page.data
      this.mineTotal = page.total
      this.mineRequested = page.data.length
      session.mineCache = { items: page.data, total: page.total }
    } catch (error) {
      if (seq !== session.mineSeq) return
      this.error = message(error, t('feedback.errors.mineLoadFailed'))
      // 同 `loadList`：失败就是失败那一态，和没有缓存时一模一样 —— 页面上
      // 「拉失败」和「你还没有提过反馈」是两句分开的话，但都得是**清空之后**
      // 才画得出来。
      this.mineItems = []
      this.mineTotal = 0
      this.mineRequested = 0
      session.mineCache = null
    } finally {
      if (seq === session.mineSeq) {
        this.mineLoading = false
        this.mineRefreshing = false
      }
    }
  },

  /** 「我的反馈」的下一页。门和 `loadMoreList` 一样，少一道「问题变了」——
   *  这一页没有参数，它的问题永远是同一个（「和我有关的那些」），变的只有身份，
   *  而身份换了会走 `resetFeedbackCaches()` 那条路。 */
  async loadMoreMine(this: ListSelf): Promise<void> {
    if (this.mineLoadingMore || this.mineLoading || this.mineRefreshing) return
    if (!this.mineHasMore) return
    const seq = session.mineSeq
    const pageStart = this.mineRequested
    this.mineLoadingMore = true
    try {
      const page = await listMyFeedback({ pageSize: PAGE_SIZE, pageStart })
      if (seq !== session.mineSeq) return
      this.mineItems = appendPage(this.mineItems, page.data)
      this.mineRequested += page.data.length
      this.mineTotal = page.total
      session.mineCache = { items: this.mineItems, total: page.total }
    } catch (error) {
      if (seq !== session.mineSeq) return
      // 同 `loadMoreList`：已经画出来的留着，失败只在列表上方说一句。
      this.error = message(error, t('feedback.errors.loadMoreFailed'))
    } finally {
      this.mineLoadingMore = false
    }
  },
}
