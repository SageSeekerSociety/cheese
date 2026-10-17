/**
 * 管理端：队列那一页，和它点开的详情里那些写操作（状态、优先级、指派、安全问题、
 * 内部备注），以及「已读游标推到此刻」那两下。
 *
 * 这一组动作改的是 admin 那一份 state，读的却常常是详情那一份 —— 光标挪到哪里、
 * 抽屉里现在是哪一条，两边必须同时成立。所以代次 `session.adminDetailSeq` 和 id
 * 一起判：读和写会落在**同一个 id** 上，光比 id 分不出谁新谁旧。
 */

import type { FeedbackAdminPatch } from '@/api'
import type { FeedbackCard, FeedbackCounts, FeedbackDetail, FeedbackPriority, FeedbackStatus } from '@/cx_types'
import type { AdminSort, AdminTab } from '../types'

import { message, PAGE_SIZE, SEARCH_DEBOUNCE_MS } from '../helpers'
import { session, takeCountsRev } from '../session'

import {
  createAdminFeedbackNote,
  getAdminFeedback as getAdminFeedbackDetail,
  listAdminFeedback,
  markFeedbackRead,
  patchAdminFeedback,
  setAdminFeedbackStatus,
} from '@/api'
import { t } from '@/i18n'
import { windowToApi } from '@/lib/feedbackWindows'

interface AdminSelf {
  adminTab: AdminTab
  adminQuery: string
  adminSort: AdminSort
  adminPageStart: number
  adminSince: string | null
  adminResolvedSince: string | null
  adminDeployedSince: string | null
  adminItems: FeedbackCard[]
  adminTotal: number
  adminLoading: boolean
  counts: FeedbackCounts
  detail: FeedbackDetail | null
  detailId: string | null
  detailLoading: boolean
  error: string | null
  adminHasPrev: boolean
  adminHasNext: boolean
  _commitCounts(next: FeedbackCounts, rev: number): void
  loadAdmin(): Promise<void>
  invalidateDetail(id: string | null): void
  _adminWrite(id: string, call: () => Promise<FeedbackDetail>): Promise<void>
  refreshCounts(): Promise<void>
}

export const adminActions = {
  /* ---- 管理端 ---- */

  async loadAdmin(this: AdminSelf): Promise<void> {
    const seq = ++session.adminSeq
    // 这一页也带着一份计数（栏位上的数），和公开列表那份是**同一份**。所以它
    // 也要在发请求前领号 —— 不领的话它就绕过了 `_commitCounts` 那道门。
    const rev = takeCountsRev()
    this.adminLoading = true
    this.error = null
    try {
      const page = await listAdminFeedback({
        tab: this.adminTab,
        q: this.adminQuery.trim(),
        sort: this.adminSort,
        // 窗口存原文、发折算：state 里是深链带来的原文（'7d' 或一个日期），发给
        // 服务端的那一刻才折成日期 —— 后端的三个参数是 datetime，'7d' 原样发出去
        // 是 422（理由与折法都在 `lib/feedbackWindows.ts`）。`null`（没有这个窗口）
        // 折成 `undefined`：查询串里「没给」和「给了个空值」是两件事，前者才是
        // 「这段日子不筛」。
        since: this.adminSince ? windowToApi(this.adminSince) : undefined,
        resolvedSince: this.adminResolvedSince ? windowToApi(this.adminResolvedSince) : undefined,
        deployedSince: this.adminDeployedSince ? windowToApi(this.adminDeployedSince) : undefined,
        pageStart: this.adminPageStart,
        pageSize: PAGE_SIZE,
      })
      if (seq !== session.adminSeq) return
      this.adminItems = page.data
      this.adminTotal = page.total
      this._commitCounts(page.counts, rev)
    } catch (error) {
      if (seq !== session.adminSeq) return
      this.error = message(error, t('feedback.errors.queueLoadFailed'))
      this.adminItems = []
    } finally {
      if (seq === session.adminSeq) this.adminLoading = false
    }
  },

  setAdminTab(this: AdminSelf, tab: AdminTab): void {
    if (this.adminTab === tab) return
    this.adminTab = tab
    // 换栏位回到第一页：第 3 页的第 4 条在另一个栏位里没有意义，留着页码会
    // 直接落到一个空页上 —— 看着像「这一栏一条都没有」。
    this.adminPageStart = 0
    void this.loadAdmin()
  },

  setAdminSort(this: AdminSelf, sort: AdminSort): void {
    if (this.adminSort === sort) return
    this.adminSort = sort
    this.adminPageStart = 0
    void this.loadAdmin()
  },

  /** 三个日期窗口：看板上「7 日新增 / 7 日解决 / 7 日上线」点进来时带着的那一段。
   *  和栏位、排序同一条规矩 —— 换一个窗口就是换了一个问题，页码必须归零，否则
   *  第 3 页落在另一段时间里，看着像「这段时间一条都没有」。
   *
   *  三个分开而不是合成一个 action：链接上从来只带其中一个，而「都没变」时这三
   *  个都直接返回、连请求都不发 —— 合成一个之后，从别的页切回队列时的「清空窗口」
   *  那一次会白拉一页。 */
  setAdminSince(this: AdminSelf, since: string | null): void {
    if (this.adminSince === since) return
    this.adminSince = since
    this.adminPageStart = 0
    void this.loadAdmin()
  },

  setAdminResolvedSince(this: AdminSelf, since: string | null): void {
    if (this.adminResolvedSince === since) return
    this.adminResolvedSince = since
    this.adminPageStart = 0
    void this.loadAdmin()
  },

  setAdminDeployedSince(this: AdminSelf, since: string | null): void {
    if (this.adminDeployedSince === since) return
    this.adminDeployedSince = since
    this.adminPageStart = 0
    void this.loadAdmin()
  },

  /** 搜索框。**防抖在 action 里，不在页面上** —— 页面拿到的还是「用户打进来的
   *  每一个字」，由这里决定什么时候真去打服务端。
   *
   *  过期的响应必须扔：手速快时 `cai` 和 `caiy` 两个请求会同时在飞，先发的
   *  那次**可能后到**，把结果换成上一个词的那一页，而输入框里是新的词。所以
   *  每次新的输入都把序号推一格，回来晚的看到序号变了就直接丢。 */
  setAdminQuery(this: AdminSelf, q: string): void {
    this.adminQuery = q
    this.adminPageStart = 0
    if (session.adminQueryTimer !== null) clearTimeout(session.adminQueryTimer)
    session.adminQueryTimer = setTimeout(() => {
      session.adminQueryTimer = null
      void this.loadAdmin()
    }, SEARCH_DEBOUNCE_MS)
  },

  clearAdminQuery(this: AdminSelf): void {
    if (session.adminQueryTimer !== null) {
      clearTimeout(session.adminQueryTimer)
      session.adminQueryTimer = null
    }
    if (!this.adminQuery) return
    this.adminQuery = ''
    this.adminPageStart = 0
    void this.loadAdmin()
  },

  adminPrev(this: AdminSelf): void {
    if (!this.adminHasPrev) return
    this.adminPageStart = Math.max(0, this.adminPageStart - PAGE_SIZE)
    void this.loadAdmin()
  },

  adminNext(this: AdminSelf): void {
    if (!this.adminHasNext) return
    this.adminPageStart += PAGE_SIZE
    void this.loadAdmin()
  },

  /** 光标挪到 `id` 上了：详情区手上那一份当场作废。
   *
   *  和 `loadAdminDetail` 开头那三行是同一件事，区别只在**什么时候**做。那个要等
   *  队列页那 150ms 防抖到期，而连按 `j`/`k` 时那支计时器每次都被重置 —— 于是整段
   *  连按期间详情区挂的一直是**上一条**的正文，左侧选中的行、头上的编号却已经是
   *  新的（F-06 说的就是这个）。作废必须发生在挪光标的那一刻。
   *
   *  `detailId` 一并前移不是顺手，是必须的：它还兼着「响应回来时这一条还算不算数」
   *  的判据（见下面 `loadAdminDetail`）。只清 `detail` 而不改它，那条还在路上的
   *  上一条的响应回来时会被当成这一条收下 —— 刚清掉的旧正文又贴回来了。
   *
   *  `id` 为 null（列表被筛空、光标没了）：作废，而且不该停在「正在拉」上。 */
  invalidateDetail(this: AdminSelf, id: string | null): void {
    // 代次 +1：**已经在路上**的那些管理端详情响应从这一刻起全部作废。作废必须
    // 发生在这里而不是等页面那 150ms 防抖到期 —— 防抖只把「同一段连按」并成一次
    // 请求，它挡不住「光标先动了、请求还没发」那段时间里回来的一份旧响应：那期间
    // `detailId` 还是旧的，只按 id 判会把上一条的正文贴回来（F-06）。
    session.adminDetailSeq += 1
    this.detailId = id
    this.detail = null
    this.detailLoading = id !== null
    this.error = null
  },

  /** 管理端点开一条。详情走**管理端**那个端点（`/admin/feedback/{id}`）：它和公开
   *  那个回的字段一样，但前者的可见性判据是「管理员」，后者是「并集」。
   *
   *  **它不用 `session.detailCache`**，两件事都刻意：这一份可能是同一台机器上**另一个人**
   *  的会话留下来的，共享那份缓存等于让下一个读者先画出一份他本来就看不见的内容；
   *  而且管理端的响应带的字段集不一样，混进同一个 id 的槽里，公开那一页会先画一个
   *  形状不对的对象。缓存是给「同一个人反复开同一条公开详情」用的。 */
  async loadAdminDetail(this: AdminSelf, id: string): Promise<void> {
    // 作废那一步只有一处写法，避免「清了 detail 忘了推 detailId」这种半截状态。
    this.invalidateDetail(id)
    // 取号**必须在 `invalidateDetail` 之后**：那一下自己就把代次 +1 了。这一份响应
    // 只有在「从这一刻起到它落地为止，没有人作废过、也没有人写过这条」时才作数 ——
    // 后者是它和 `detailId` 那条判据的分工：读和写落在**同一个 id** 上，id 一样
    // 判不出先后，只有代次能（见 `_adminWrite`）。
    const seq = session.adminDetailSeq
    try {
      const detail = await getAdminFeedbackDetail(id)
      if (seq !== session.adminDetailSeq || this.detailId !== id) return
      this.detail = detail
    } catch (error) {
      if (seq !== session.adminDetailSeq || this.detailId !== id) return
      this.error = message(error, t('feedback.errors.openFailed'))
    } finally {
      // 被作废时**不复位** `detailLoading`：那面旗现在归更晚的那一次（新的读，或者
      // 刚写完那一下），这里复位等于替它宣布「不拉了」。
      if (seq === session.adminDetailSeq && this.detailId === id) this.detailLoading = false
    }
  },

  /** 人**按下**的那个已读（`M` 键、页头上那个按钮）：把未读游标推到此刻。
   *
   *  和下面那个自动的分开，而不只是「都推一次游标」：这一个**每次都要真发请求**。
   *  页面自己触发的那个带一个 id 去重集合（同一条只推一次），拿它当人手那一下的
   *  实现，就会出现「按了没反应」—— 打开一条超过 800ms 之后再按 `M`，那一按会被
   *  集合直接早退掉：未读数不减、徽标不动、也没有任何提示。手动那一下是幂等的
   *  （服务端推的就是同一个游标），但它不该是静默的。 */
  async markRead(this: AdminSelf): Promise<void> {
    try {
      await markFeedbackRead()
      // 这一下把游标推过了**此刻还开着的这一条**，所以页面那个自动已读再为它发一次
      // 请求就没有意义了 —— 记进集合里，让它 800ms 后那次自己跳过。
      // **只在成功之后记**：失败时不能记，否则自动那一条路也不会再试了。
      if (this.detailId) session.markedReadIds.add(this.detailId)
      // 当场领号：这一份是**此刻**最新的东西，比任何还在路上的响应都新 ——
      // 早一步发出的那次 loadList 回来时不该把未读数再点回去。
      this._commitCounts({ ...this.counts, unread: 0 }, takeCountsRev())
    } catch {
      // 游标推不动不值得报错 —— 下次打开还会再试一次。
    }
  },

  /** 进详情时**自动**走的那个已读：同一个 id 只真发一次请求。
   *
   *  和上面那个分开，因为触发的人不同：`markRead` 是人按下 M 键或按钮，按两次
   *  就是两次表态；这一条是页面自己触发的，而一条详情在一段时间里会被开开关关
   *  好几次（抽屉、返回列表再点开），每开一次推一次游标，等于把同一件事重复说了
   *  好几遍。
   *
   *  失败时把 id 放回去：游标没推成，这一条在服务端仍然是未读，记成「已标记」
   *  会让它再也清不掉 —— 那正是这一轮要修的那个死档。 */
  async markReadOnce(this: AdminSelf, id: string): Promise<void> {
    if (session.markedReadIds.has(id)) return
    session.markedReadIds.add(id)
    try {
      await markFeedbackRead()
    } catch {
      session.markedReadIds.delete(id)
      return
    }
    // 先把徽标归零，再问一次服务端：`markFeedbackRead()` 推的是**整个人**的游标
    // （一直推到此刻），所以读过这一条之后没有一条还是未读的。本地减一是在前端
    // 算一遍服务端的账，而那张账本（谁在什么时候动过我的条目）前端看不见。
    // 当场领号（同 `markRead`）：这是一次本地动作，没有请求可等。
    this._commitCounts({ ...this.counts, unread: 0 }, takeCountsRev())
    void this.refreshCounts()
  },

  /** 推一个状态。服务端把状态和它那条时间线写在同一个事务里，所以这里回的是
   *  **刷新之后的整条详情**，不是「我把状态改成了什么」的本地推断。 */
  async setStatus(this: AdminSelf, id: string, status: FeedbackStatus): Promise<void> {
    await this._adminWrite(id, () => setAdminFeedbackStatus(id, status))
  },

  async setPriority(this: AdminSelf, id: string, priority: FeedbackPriority): Promise<void> {
    await this._adminWrite(id, () => patchAdminFeedback(id, { priority } satisfies FeedbackAdminPatch))
  },

  /** 指派 / 取消指派。空串是「清掉」—— 服务端把 `''` 和 `null` 分开读，
   *  后者是「这次别动它」。 */
  async assign(this: AdminSelf, id: string, handle: string | null): Promise<void> {
    await this._adminWrite(id, () =>
      patchAdminFeedback(id, { assignee_handle: handle ?? '' } satisfies FeedbackAdminPatch)
    )
  },

  /** 标 / 取消标「安全问题」。标上之后这条对同事就不见了 —— 所以服务端顺手写一条
   *  时间线，这里回的是刷新后的详情。 */
  async setSecurity(this: AdminSelf, id: string, security: boolean): Promise<void> {
    await this._adminWrite(id, () => patchAdminFeedback(id, { security } satisfies FeedbackAdminPatch))
  },

  /** 加一条内部备注。**只增不改**：回的是整条详情，`notes` 里就有新的那条。 */
  async addNote(this: AdminSelf, id: string, body: string): Promise<void> {
    const text = body.trim()
    if (!text) return
    await this._adminWrite(id, () => createAdminFeedbackNote(id, text))
  },

  /** 管理端写操作共用的外壳：一次请求、一次刷新、一处错误处理。
   *
   *  `id` 是**这次写的是哪一条**，两个地方要用它：回填详情时对一下「抽屉里现在还
   *  是它吗」，以及列表刷新后的口径。 */
  async _adminWrite(this: AdminSelf, id: string, call: () => Promise<FeedbackDetail>): Promise<void> {
    this.error = null
    try {
      const detail = await call()
      // 只在**还是这一条**时回填：写请求在飞的时候人可能已经点开了另一条，慢响应
      // 回来会把 `detailId` 拉回旧的那条 —— 新条目的抽屉要么显示「这条反馈打不开」，
      // 要么因为 `detailLoading` 永远不再复位而卡在「加载中…」。
      // 换掉 `state.detail` 本身，**不写回 `session.detailCache`**（这一份来自管理端端点，
      // 见 `session.detailCache` 那段）。缓存里那份留着旧值，下一次 `loadDetail` 会先画它、
      // 再被重拉覆盖 —— 代价是一次往返，换来的是「缓存里只存公开端点回的东西」
      // 这条不变量，它比省掉的那一次往返值钱。
      if (this.detailId === id) {
        this.detail = detail
        this.detailLoading = false
        // 代次 +1：**写之前发出去、写之后才回来**的那次读从此作废。没有这一下，
        // 一份比这次写更旧的快照会贴回来盖掉它，而屏幕上看着就是「这一按没生效」——
        // 管理员再按一次，于是状态白走一格。读和写落在同一个 id 上，只按 id 判
        // 分不出谁新谁旧，这条代次是唯一能判的那个（e2e 里真的踩到过：
        // 面板显示的还是「已收录」，而库里已经写成了「处理中」）。
        session.adminDetailSeq += 1
      }
      // 那一行也变了，而且可能在**别的一栏**里（改了安全问题就从公开栏挪进安全栏），
      // 所以整页重新拉一次。以前这里只是把数组清空、指望「下次挂载重拉」，可管理端
      // 这一页在抽屉关掉时既不重新挂载也不重新拉取 —— 于是表格画出来的是「这一栏
      // 没有反馈」这个假状态，不切栏位、不刷新页面就回不来。
      await this.loadAdmin()
    } catch (error) {
      this.error = message(error, t('feedback.errors.actionFailed'))
    }
  },
}
