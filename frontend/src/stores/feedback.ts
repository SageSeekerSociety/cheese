/**
 * stores/feedback.ts — 反馈功能的**全部状态**，接真接口。
 *
 * 它是这一层存在的唯一理由：页面和组件一律通过 action / getter 读写，谁都不许自己
 * 发请求。上一轮它是纯内存的原型（动作同步改本地数组），这一轮函数体换成了网络调用，
 * 而**调用方几乎没有改** —— 那正是当初把它单独拆出来的目的。
 *
 * 三件不再由前端决定的事：
 *
 *   1. **权限**。「我是不是管理员」读服务端的 `GET /feedback/meta`（`meta.is_admin`），
 *      不再是一个可以拨的开关。上一轮那个「原型身份」开关整块删掉了 —— 它当时就写着
 *      「不是权限门」，现在门在服务端，客户端连猜都不该猜。
 *   2. **隐私**。私密条目由服务端 `WHERE` 决定谁能看见（提交者 ∪ 管理员 ∪ 提出它的房间），前端不做
 *      第二遍过滤：客户端过滤只能挡住界面，挡不住链接。上一轮那句「将来这层整个删掉」
 *      说的就是现在。
 *   3. **有哪些状态、按什么顺序走**。词表来自 `GET /feedback/meta`；颜色留在
 *      `lib/feedbackMeta.ts`（那是视觉决定，服务端不该知道 `--warn-wash`）。
 *
 * 列表的 Tab 和搜索都是**服务端**参数，所以切 Tab / 改搜索词会重新拉一页，而不是在
 * 本地数组上 filter。搜索键击用 250ms 防抖，而且只有最新一次请求的结果会被采纳
 * （见文件末尾那三个模块级计数器），否则慢的那次后到，会把快的那次覆盖掉。
 */

import type { FeedbackAdminPatch } from '@/api'
import type {
  FeedbackCard,
  FeedbackComment,
  FeedbackCounts,
  FeedbackCreateBody,
  FeedbackDetail,
  FeedbackKind,
  FeedbackMeta,
  FeedbackPriority,
  FeedbackProposal,
  FeedbackStatus,
  FeedbackVisibility,
} from '@/cx_types'

import { defineStore } from 'pinia'

import { ApiError } from '@/api'
import {
  acceptFeedbackProposal,
  createAdminFeedbackNote,
  createFeedback,
  createFeedbackComment,
  deleteFeedbackComment,
  dismissFeedbackProposal,
  getAdminFeedback as getAdminFeedbackDetail,
  getFeedback,
  getFeedbackCounts,
  getFeedbackMeta,
  likeFeedbackComment,
  listAdminFeedback,
  listFeedback,
  listFeedbackComments,
  listFeedbackProposals,
  listMyFeedback,
  markFeedbackRead,
  patchAdminFeedback,
  setAdminFeedbackStatus,
  supportFeedback,
  unlikeFeedbackComment,
  unsupportFeedback,
} from '@/api'
import { STATUS_LADDER } from '@/lib/feedbackMeta'

/** 公开列表的栏位。服务端 `PUBLIC_TABS` 是**同一个集合**：加一个栏位是后端改
 *  一处、前端跟着改一处类型的事。 */
export type FeedbackTab = 'all' | 'hot' | 'active' | 'resolved'
/** 管理端的栏位。同上，服务端 `ADMIN_TABS`。 */
export type AdminTab = 'public' | 'private' | 'agent' | 'security'

/** 搜索防抖，毫秒。250 是人停手和「它没反应」之间的那条线。 */
const SEARCH_DEBOUNCE_MS = 250
/** 一页拉多少条。这一版没有翻页 UI，所以给得比默认 20 宽，一次把列表读完。 */
const PAGE_SIZE = 50

/** 请求序号，用来丢掉过期响应。它们**不在 state 里**：一个自增的数字和定时器 id
 *  都不是界面状态，放进 state 只会让 Vue 去追踪一个没人渲染的值。 */
let listSeq = 0
let adminSeq = 0
let mineSeq = 0
let countsSeq = 0
let searchTimer: ReturnType<typeof setTimeout> | null = null

const EMPTY_COUNTS: FeedbackCounts = { all: 0, hot: 0, active: 0, resolved: 0, unread: 0 }

/** 提交抽屉里那一份表单。提交流程有两处（中心页的按钮、会话里的 Agent 卡片），
 *  它们打开的是同一个抽屉，所以「抽屉开着、内容是什么」放在 store 里而不是某个
 *  页面的 ref 上 —— 否则 AgentFeedbackCard 得把抽屉再实现一遍。 */
export interface FeedbackDraft {
  kind: FeedbackKind
  title: string
  body: string
  /** 附件名。**不上传** —— 见 SubmitFeedbackDrawer 里那段说明。 */
  attachments: string[]
  /** 「附带现场」：把 `fromAgent` 那三段会话信息一起提交。 */
  attachContext: boolean
  visibility: FeedbackVisibility
  /** Agent 发现的那条带过来的现场，勾了 `attachContext` 才随反馈一起提交。 */
  fromAgent?: {
    whatHappened: string
    repro: string
    evidence: string
    sessionId?: string
    environment?: string
  }
  /** 这张抽屉是从哪张提案卡打开的。有值走「发送」那条路（`accept`），
   *  没有就是人自己新提一条。 */
  proposal?: { topicId: string; blockId: string }
}

function emptyDraft(): FeedbackDraft {
  return {
    kind: 'bug',
    title: '',
    body: '',
    attachments: [],
    attachContext: false,
    visibility: 'public',
  }
}

/** 把表单折成请求体。**只有这一个地方做这件事**：两条提交路走的是同一个动作，
 *  各折一次的话，「可见范围」这种后来加的字段必然只会加进其中一份。 */
function toCreateBody(draft: FeedbackDraft): FeedbackCreateBody {
  const body = draft.body.trim()
  const agent = draft.attachContext ? draft.fromAgent : undefined
  return {
    kind: draft.kind,
    title: draft.title.trim(),
    // 摘要默认取正文第一行：它在列表里是给人扫的那一句，空着等于列表里有一条空白。
    summary: body.split('\n')[0]?.slice(0, 120) || draft.title.trim(),
    problem: body,
    visibility: draft.visibility,
    what_happened: agent?.whatHappened ?? null,
    repro: agent?.repro ?? null,
    evidence: agent?.evidence ?? null,
    session_id: agent?.sessionId ?? null,
    environment: agent?.environment ?? null,
  }
}

/** 后端把能读的原因写在 `message` 里（`ApiError` 会把它带上），照它显示 ——
 *  「HTTP 412 for /feedback/...」会让人去查一个服务端早就解释过的东西。 */
function message(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback
}

export const useFeedbackStore = defineStore('feedback', {
  state: () => ({
    /* ---- 词表 ---- */
    meta: null as FeedbackMeta | null,
    /** 「问过服务端了」—— 成功也好失败也好，问过就是问过。页面用它区分「还不知道
     *  我是不是管理员」和「确定不是」：少了它，meta 还在飞的那一帧画出来的是
     *  「你的账号不在管理员名单里」，一个真管理员先看到的是一句假话。 */
    metaChecked: false,
    /* ---- 公开列表 ---- */
    items: [] as FeedbackCard[],
    total: 0,
    counts: { ...EMPTY_COUNTS } as FeedbackCounts,
    loading: false,
    query: '',
    tab: 'all' as FeedbackTab,
    /* ---- 管理端列表 ---- */
    adminItems: [] as FeedbackCard[],
    adminTotal: 0,
    adminLoading: false,
    adminTab: 'public' as AdminTab,
    /* ---- 我的反馈（`/feedback/mine`）。和上面那份公开列表是**两套数据**，
       不是同一份的两个视图：公开列表按栏位筛全平台，这一份按「和我的关系」筛，
       服务端的 WHERE 就不是同一个。 ---- */
    mineItems: [] as FeedbackCard[],
    mineTotal: 0,
    mineLoading: false,
    /* ---- 详情。`detail` 是**当前这一条**；换一条时整个对象换掉。 ---- */
    detail: null as FeedbackDetail | null,
    detailLoading: false,
    /** 详情页正在看哪一条。用来判断「这次回来的详情是不是我要的那条」—— 慢响应
     *  后到时，先到的那条已经渲染了，后到的会把页面换成另一条。 */
    detailId: null as string | null,
    /** 顶层评论正在取下一页。 */
    moreCommentsLoading: false,
    /** 哪几栋楼正在取楼内的下一页回复，按顶层评论 id。
     *  **这不只是给按钮画个转圈的**：同一个游标取两遍会把同一段回复追加两遍，屏幕上
     *  是两条一模一样的回复（`key` 还会撞）。所以真正的门在 action 里，这个状态是
     *  给按钮看的。 */
    moreRepliesLoading: {} as Record<string, boolean>,
    /* ---- 抽屉：open 控制显隐，draft 是那一份表单。两处入口共用。 ---- */
    submitOpen: false,
    draft: emptyDraft(),
    submitting: false,
    /** 上一次失败**原话**。页面直接显示它，不另写一句「操作失败」。 */
    error: null as string | null,
    /** 提交成功后给页面的一个一次性提示（v-snackbar 读它）。 */
    lastSubmittedId: null as string | null,
  }),

  getters: {
    /** 我是不是平台管理员。**服务端说了算**；meta 没到之前一律按「不是」。 */
    isAdmin(state): boolean {
      return !!state.meta?.is_admin
    },
    /** 状态梯子：服务端给的那份，没到之前用本地兜底（见 lib/feedbackMeta.ts）。 */
    statusLadder(state): FeedbackStatus[] {
      const fromServer = state.meta?.status_ladder
      return fromServer?.length ? fromServer : STATUS_LADDER
    },
    /** 「热门」的门槛。前端不写死 5。 */
    hotSupports(state): number {
      return state.meta?.hot_supports ?? 5
    },
    tabCounts(state): Record<FeedbackTab, number> {
      return {
        all: state.counts.all,
        hot: state.counts.hot,
        active: state.counts.active,
        resolved: state.counts.resolved,
      }
    },
  },

  actions: {
    /* ---- 词表 ---- */

    /** 拉词表和「我是不是管理员」。页面挂载时调一次；失败不致命 —— 颜色和标签
     *  本地都有一份，梯子也有兜底，所以这里不把错误抛给页面。 */
    async loadMeta(): Promise<void> {
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

    /* ---- 列表 ---- */

    /** 拉当前 Tab + 搜索词的那一页。 */
    async loadList(): Promise<void> {
      const seq = ++listSeq
      this.loading = true
      this.error = null
      try {
        const page = await listFeedback({ tab: this.tab, q: this.query.trim(), pageSize: PAGE_SIZE })
        // 只有最后一次请求的结果算数：防抖挡不住「先发的那次后到」。
        if (seq !== listSeq) return
        this.items = page.data
        this.total = page.total
        this.counts = page.counts
      } catch (error) {
        if (seq !== listSeq) return
        this.error = message(error, '反馈列表加载失败')
        this.items = []
        this.total = 0
      } finally {
        if (seq === listSeq) this.loading = false
      }
    },

    /** 切栏位。必然重新拉 —— 栏位是**服务端**的筛选，本地过滤是第二份实现。 */
    setTab(tab: FeedbackTab): void {
      if (this.tab === tab) return
      this.tab = tab
      void this.loadList()
    },

    /** 改搜索词。防抖之后重新拉：搜索也在服务端，它搜的字段比前端能拿到的那几个多。 */
    setQuery(query: string): void {
      this.query = query
      if (searchTimer) clearTimeout(searchTimer)
      searchTimer = setTimeout(() => {
        searchTimer = null
        void this.loadList()
      }, SEARCH_DEBOUNCE_MS)
    },

    /** 只改搜索词、不发请求。用在「清除筛选」上：那个动作要立刻回到全量，
     *  而不是等 250ms（点了按钮却没动静，读起来像坏了）。 */
    async clearQuery(): Promise<void> {
      this.query = ''
      if (searchTimer) clearTimeout(searchTimer)
      searchTimer = null
      await this.loadList()
    },

    /* ---- 我的反馈 ---- */

    /** 拉「和我有关的那一摞」：我提的 + 我替谁提的 + 指派给我的。三个来源合成一个
     *  清单是**服务端**的定义（`/feedback/mine`），前端不再分栏 —— 分栏要有「这条
     *  为什么在我的清单里」，而那个字段服务端不给；按 handle 在前端猜一遍等于把
     *  可见性规则抄第二份，抄错的那天就是有人看见不该看见的条目。
     *
     *  访客（没登录）拿到的也是这一条路：服务端回空列表而不是 401，所以这里和公开
     *  列表一样只是「没有」。 */
    async loadMine(): Promise<void> {
      const seq = ++mineSeq
      this.mineLoading = true
      this.error = null
      try {
        const page = await listMyFeedback({ pageSize: PAGE_SIZE })
        if (seq !== mineSeq) return
        this.mineItems = page.data
        this.mineTotal = page.total
      } catch (error) {
        if (seq !== mineSeq) return
        this.error = message(error, '「我的反馈」加载失败')
        this.mineItems = []
        this.mineTotal = 0
      } finally {
        if (seq === mineSeq) this.mineLoading = false
      }
    },

    /* ---- 详情 ---- */

    /** 拉一条的全部内容。看不见的 id 服务端回 404，这里如实把它记成「没有」。 */
    async loadDetail(id: string): Promise<void> {
      this.detailId = id
      this.detail = null
      // 翻页的进度跟着这一条走：换一条反馈之后，上一条的「还剩几页」和「哪几栋楼
      // 正在取回复」都不成立了，留着只会让新页面上的按钮一进来就是转圈状态。
      this.moreCommentsLoading = false
      this.moreRepliesLoading = {}
      this.detailLoading = true
      this.error = null
      try {
        const detail = await getFeedback(id)
        if (this.detailId !== id) return
        this.detail = detail
      } catch (error) {
        if (this.detailId !== id) return
        this.error = message(error, '这条反馈打不开')
      } finally {
        if (this.detailId === id) this.detailLoading = false
      }
    },

    /* ---- 支持 ---- */

    /** 支持 / 取消支持。**回的是写完之后的服务端计数**，不是本地 ±1：两个人同时
     *  点会各自渲染出一个从来没存在过的数字。 */
    async toggleSupport(id: string): Promise<void> {
      const card = this._find(id)
      if (!card) return
      try {
        const result = card.supported ? await unsupportFeedback(id) : await supportFeedback(id)
        this._patch(id, { supports: result.count, supported: result.supported })
        // 栏位上的数字也要跟着动：「热门」是服务端按「支持数 ≥ 门槛且未解决」现算的，
        // 就在这一下跨过门槛的条目，本地那份计数当场就对不上了（列表 6 条、Tab 上写着
        // 5）。计数只能问服务端，前端再算一遍就是第二份实现。
        void this.refreshCounts()
      } catch (error) {
        // 办完了的反馈会被拒（412）。那不是故障，是「别再点了」—— 如实显示服务端
        // 那句话，比一个静默失败好。
        this.error = message(error, '操作失败')
      }
    },

    /** 重新问一次计数。失败不报错：Tab 上的数字晚一拍更新，不值得打断人。 */
    async refreshCounts(): Promise<void> {
      const seq = ++countsSeq
      try {
        const counts = await getFeedbackCounts()
        if (seq !== countsSeq) return
        this.counts = counts
      } catch {
        // 见上。
      }
    },

    /** 清掉上一次失败的原话。页面上的错误条靠它自己的关闭按钮调 —— 不这么做的话，
     *  一次失败会一直挂在页面上，直到下一个动作顺手把它覆盖。 */
    clearError(): void {
      this.error = null
    },

    /* ---- 评论 ---- */

    /** 发一条评论。`parentId` 指向**任意**一条评论：层级由服务端折上去，前端不判断。
     *  「这条在回谁」（`reply_to_handle`）也是服务端写下来的 —— 折到顶层之后前端再也
     *  推不出来，猜一个的结果是每个人看到的指代都不一样。
     *
     *  **返回成功与否**，调用方靠它决定「草稿留着还是清掉」：一次 500 之后把用户写了
     *  两段的话清掉，是这一版要修掉的东西之一。 */
    async addComment(id: string, body: string, parentId?: string): Promise<boolean> {
      const text = body.trim()
      if (!text) return false
      try {
        const created = await createFeedbackComment(id, text, parentId ?? null)
        const detail = this._detailIfCurrent(id)
        if (detail) {
          detail.thread = [...detail.thread, created]
          detail.comments += 1
          // 新回复要把它那一栋的**总数**也加一。那个数是「这栋楼一共几条回复」，
          // 客户端拿它和手上条数比较来决定「展开更多」是摊开还是去取下一页 ——
          // 少加这一个，刚发出去的回复会让这两个数对不上。
          //
          // 认的是**服务端折过的**目标（`created.parent_id`），不是这里的 `parentId`：
          // 回一条回复时，服务端会把它折到那栋楼的顶层，而 `parentId` 指的是一条
          // 回复（它自己没有楼，`reply_count` 恒为 0）。按 `parentId` 找就会加到
          // 一条没有楼的回复上，屏幕上那栋楼的总数永远差一条。
          if (created.parent_id) {
            const top = detail.thread.find((c) => c.id === created.parent_id)
            if (top) top.reply_count += 1
          }
        }
        return true
      } catch (error) {
        this.error = message(error, '评论发送失败')
        return false
      }
    },

    /** 往下翻**顶层评论**那一层。游标来自详情（`thread_next_cursor`），取完就没有了。
     *
     *  这里挡重复点击不是优化：同一个游标取两遍，同一个 `items` 会被追加两遍 ——
     *  屏幕上出现两条一模一样的评论，`:key` 也撞。 */
    async loadMoreComments(id: string): Promise<void> {
      const detail = this._detailIfCurrent(id)
      const cursor = detail?.thread_next_cursor
      if (!detail || !cursor || this.moreCommentsLoading) return
      this.moreCommentsLoading = true
      this.error = null
      try {
        const page = await listFeedbackComments(id, { after: cursor })
        const current = this._detailIfCurrent(id)
        if (!current) return
        current.thread = [...current.thread, ...page.items]
        current.thread_next_cursor = page.next_cursor
      } catch (error) {
        this.error = message(error, '评论加载失败')
      } finally {
        this.moreCommentsLoading = false
      }
    },

    /** 取**某一栋楼里**的下一段回复。游标挂在顶层评论那一行上（`replies_next_cursor`），
     *  和上面那条一样由服务端发、客户端原样送回去。 */
    async loadMoreReplies(id: string, parentId: string): Promise<void> {
      const detail = this._detailIfCurrent(id)
      const anchor = detail?.thread.find((c) => c.id === parentId)
      const cursor = anchor?.replies_next_cursor
      if (!detail || !anchor || !cursor || this.moreRepliesLoading[parentId]) return
      this.moreRepliesLoading = { ...this.moreRepliesLoading, [parentId]: true }
      this.error = null
      try {
        const page = await listFeedbackComments(id, { parentId, after: cursor })
        const current = this._detailIfCurrent(id)
        // 请求在飞的时候可能已经换了详情；换了就什么都不做（`_detailIfCurrent` 会
        // 挡住）。也可能这一栋楼整个被删了 —— 那 `kept` 里找不到锚点，同样收手。
        const kept = current?.thread.find((c) => c.id === parentId)
        if (!current || !kept) return
        current.thread = [...current.thread, ...page.items]
        kept.replies_next_cursor = page.next_cursor
      } catch (error) {
        this.error = message(error, '回复加载失败')
      } finally {
        const next = { ...this.moreRepliesLoading }
        delete next[parentId]
        this.moreRepliesLoading = next
      }
    },

    /** 点赞 / 取消点赞一条评论。**回的是写完之后的服务端计数**，不是本地 ±1 —— 和
     *  `toggleSupport` 同一句：两个人同时点会各自渲染出一个从来没存在过的数字。
     *
     *  这里**不**调 `refreshCounts()`，和 `toggleSupport` 有意不同：评论点赞不进任何
     *  一个 Tab 的计数，「热门」看的是反馈级的支持数。没头没脑地多问一次，只是一次白
     *  跑的请求。 */
    async toggleCommentLike(id: string, commentId: string): Promise<void> {
      const comment = this._commentOf(id, commentId)
      if (!comment) return
      try {
        const result = comment.liked
          ? await unlikeFeedbackComment(id, commentId)
          : await likeFeedbackComment(id, commentId)
        // 重新取一次而不是改上面那个引用：请求在飞的时候页面可能已经换了详情，也可能
        // 有人又点了一下。`_commentOf` 会挡住前一种（拿不到就什么都不做）。
        const current = this._commentOf(id, commentId)
        if (!current) return
        current.likes = result.count
        current.liked = result.liked
      } catch (error) {
        this.error = message(error, '操作失败')
      }
    },

    /** 删一条评论。删得掉谁由服务端的 `can_delete` 说了算，按钮也是照它画的；这里
     *  不发第二遍请求去问，只把服务端已经答过的那件事照做。
     *
     *  **本地也要按服务端的规则把楼里的回复一起拿掉。** 服务端删顶层评论时会连带删掉
     *  它下面的回复（那是「一条回复挂在一个查不到的父亲下」的那个孤儿），前端少做这
     *  一步，屏幕上就正是那个形状：父亲没了、回复还挂着，再一次刷新它们又都不见了。
     *  这是本仓库接受的那种镜像（和 `lib/feedbackMeta.ts::isClosed` 同一个道理：前后端
     *  都要回答同一个问题时，两份实现里至少一份要写明它跟的是哪一条）。 */
    async deleteComment(id: string, commentId: string): Promise<void> {
      try {
        await deleteFeedbackComment(id, commentId)
        const detail = this._detailIfCurrent(id)
        if (!detail) return
        const gone = detail.thread.filter((c) => c.id === commentId || c.parent_id === commentId)
        const kept = detail.thread.filter((c) => c.id !== commentId && c.parent_id !== commentId)
        detail.comments -= gone.length
        // 被删掉的是楼里的回复，那一栋的总数也要减。这只对**手上已经有的**那几条
        // 生效：回复分页之后，删掉一条还没取回来的回复（理论上做不到，屏幕上没有
        // 就没有按钮）这里数不到，那一栋的总数会偏高一条。偏高只会让「加载更多
        // 回复」多一次必然取到空页的点击，然后游标到底、按钮消失，不会留下一个
        // 少显示的回复。
        for (const row of gone) {
          if (!row.parent_id) continue
          const top = kept.find((c) => c.id === row.parent_id)
          if (top) top.reply_count = Math.max(0, top.reply_count - 1)
        }
        detail.thread = kept
      } catch (error) {
        this.error = message(error, '删除失败')
      }
    },

    /* ---- 提交抽屉 ---- */

    openSubmit(preset: Partial<FeedbackDraft> = {}): void {
      this.draft = { ...emptyDraft(), ...preset }
      this.submitOpen = true
    },

    closeSubmit(): void {
      this.submitOpen = false
      this.lastSubmittedId = null
    },

    /** 附件名只留在这一屏里 —— 上传还没接，见 SubmitFeedbackDrawer。 */
    addAttachment(name: string): void {
      if (!name || this.draft.attachments.includes(name)) return
      this.draft.attachments.push(name)
    },

    removeAttachment(name: string): void {
      this.draft.attachments = this.draft.attachments.filter((n) => n !== name)
    },

    /** 提交。返回新条目的 **uuid**（路由用它），失败回 null 并把原因写进 error。
     *
     *  两条路都从这里走：普通提交是 `POST /feedback`；从提案卡来的那条是
     *  `POST /topics/{id}/feedback-proposals/{block_id}/accept` —— 后者的作者是提案
     *  的 agent、提交者是按下发送的人，两个都由服务端从卡和会话里取，所以客户端
     *  连作者名都不用传（传了也不算数）。 */
    async submit(): Promise<string | null> {
      const draft = this.draft
      if (!draft.title.trim() || this.submitting) return null
      this.submitting = true
      this.error = null
      try {
        const body = toCreateBody(draft)
        const detail = draft.proposal
          ? await acceptFeedbackProposal(draft.proposal.topicId, draft.proposal.blockId, body)
          : await createFeedback(body)
        this.submitOpen = false
        this.lastSubmittedId = detail.id
        this.draft = emptyDraft()
        // 列表和各种计数都变了：清掉，让下次挂载重新拉，而不是在这里手改数组
        // （手改的那份迟早和下一页对不上）。`mineItems` 也得清 —— 刚提的这条
        // 属于「我提的」，留着旧的会让「我的反馈」少一条，而那正是这一页要回答的
        // 那个问题。
        this.items = []
        this.mineItems = []
        return detail.id
      } catch (error) {
        this.error = message(error, '提交失败')
        return null
      } finally {
        this.submitting = false
      }
    },

    /* ---- 管理端 ---- */

    async loadAdmin(): Promise<void> {
      const seq = ++adminSeq
      this.adminLoading = true
      this.error = null
      try {
        const page = await listAdminFeedback({ tab: this.adminTab, pageSize: PAGE_SIZE })
        if (seq !== adminSeq) return
        this.adminItems = page.data
        this.adminTotal = page.total
        this.counts = page.counts
      } catch (error) {
        if (seq !== adminSeq) return
        this.error = message(error, '管理队列加载失败')
        this.adminItems = []
      } finally {
        if (seq === adminSeq) this.adminLoading = false
      }
    },

    setAdminTab(tab: AdminTab): void {
      if (this.adminTab === tab) return
      this.adminTab = tab
      void this.loadAdmin()
    },

    /** 管理端点开一条。详情走**管理端**那个端点（`/admin/feedback/{id}`）：它和公开
     *  那个回的字段一样，但前者的可见性判据是「管理员」，后者是「并集」。 */
    async loadAdminDetail(id: string): Promise<void> {
      this.detailId = id
      this.detail = null
      this.detailLoading = true
      this.error = null
      try {
        const detail = await getAdminFeedbackDetail(id)
        if (this.detailId !== id) return
        this.detail = detail
      } catch (error) {
        if (this.detailId !== id) return
        this.error = message(error, '这条反馈打不开')
      } finally {
        if (this.detailId === id) this.detailLoading = false
      }
    },

    /** 把未读游标推到此刻。 */
    async markRead(): Promise<void> {
      try {
        await markFeedbackRead()
        this.counts = { ...this.counts, unread: 0 }
      } catch {
        // 游标推不动不值得报错 —— 下次打开还会再试一次。
      }
    },

    /** 推一个状态。服务端把状态和它那条时间线写在同一个事务里，所以这里回的是
     *  **刷新之后的整条详情**，不是「我把状态改成了什么」的本地推断。 */
    async setStatus(id: string, status: FeedbackStatus): Promise<void> {
      await this._adminWrite(id, () => setAdminFeedbackStatus(id, status))
    },

    async setPriority(id: string, priority: FeedbackPriority): Promise<void> {
      await this._adminWrite(id, () => patchAdminFeedback(id, { priority } satisfies FeedbackAdminPatch))
    },

    /** 指派 / 取消指派。空串是「清掉」—— 服务端把 `''` 和 `null` 分开读，
     *  后者是「这次别动它」。 */
    async assign(id: string, handle: string | null): Promise<void> {
      await this._adminWrite(id, () =>
        patchAdminFeedback(id, { assignee_handle: handle ?? '' } satisfies FeedbackAdminPatch)
      )
    },

    /** 标 / 取消标「安全问题」。标上之后这条对同事就不见了 —— 所以服务端顺手写一条
     *  时间线，这里回的是刷新后的详情。 */
    async setSecurity(id: string, security: boolean): Promise<void> {
      await this._adminWrite(id, () => patchAdminFeedback(id, { security } satisfies FeedbackAdminPatch))
    },

    /** 加一条内部备注。**只增不改**：回的是整条详情，`notes` 里就有新的那条。 */
    async addNote(id: string, body: string): Promise<void> {
      const text = body.trim()
      if (!text) return
      await this._adminWrite(id, () => createAdminFeedbackNote(id, text))
    },

    /** 管理端写操作共用的外壳：一次请求、一次刷新、一处错误处理。
     *
     *  `id` 是**这次写的是哪一条**，两个地方要用它：回填详情时对一下「抽屉里现在还
     *  是它吗」，以及列表刷新后的口径。 */
    async _adminWrite(id: string, call: () => Promise<FeedbackDetail>): Promise<void> {
      this.error = null
      try {
        const detail = await call()
        // 只在**还是这一条**时回填：写请求在飞的时候人可能已经点开了另一条，慢响应
        // 回来会把 `detailId` 拉回旧的那条 —— 新条目的抽屉要么显示「这条反馈打不开」，
        // 要么因为 `detailLoading` 永远不再复位而卡在「加载中…」。
        if (this.detailId === id) this.detail = detail
        // 那一行也变了，而且可能在**别的一栏**里（改了安全问题就从公开栏挪进安全栏），
        // 所以整页重新拉一次。以前这里只是把数组清空、指望「下次挂载重拉」，可管理端
        // 这一页在抽屉关掉时既不重新挂载也不重新拉取 —— 于是表格画出来的是「这一栏
        // 没有反馈」这个假状态，不切栏位、不刷新页面就回不来。
        await this.loadAdmin()
      } catch (error) {
        this.error = message(error, '操作失败')
      }
    },

    /* ---- 提案卡 ---- */

    /** 这个话题里还活着的提案卡。**已经「不用」过的不会回来** —— 那件事记在服务端
     *  （指纹），不是组件状态（原型那份一刷新就回来）。 */
    async loadProposals(topicId: string): Promise<FeedbackProposal[]> {
      try {
        return await listFeedbackProposals(topicId)
      } catch {
        // 提案是「顺路问一句」，拉不到就当没有 —— 不能因为它让对话栏报错。
        return []
      }
    },

    /** 「不用」。落一行，之后同指纹的卡不会再出现。 */
    async dismissProposal(topicId: string, blockId: string): Promise<void> {
      try {
        await dismissFeedbackProposal(topicId, blockId)
      } catch (error) {
        this.error = message(error, '操作失败')
      }
    },

    /* ---- 内部小工具（下划线开头：不是给页面用的 API） ---- */

    /** 一条反馈现在在**哪一份数据**里。三份列表都要找。前两份（公开、管理）是
     *  「点开列表再点支持」，`mineItems` 是同一件事发生在「我的反馈」页上——漏掉它
     *  的表现是那一页的支持按钮点了没反应（`toggleSupport` 找不到卡片就直接 return，
     *  连错误都不报）。详情那一份管的是深链进来的页面。
     *
     *  列表里没有就找详情：直接打开/刷新 `/feedback/<id>` 时三份列表都是空的
     *  （那一页不拉列表），而支持按钮只认这一份。 */
    _find(id: string): FeedbackCard | undefined {
      return (
        this.items.find((i) => i.id === id) ??
        this.adminItems.find((i) => i.id === id) ??
        this.mineItems.find((i) => i.id === id) ??
        (this.detail?.id === id ? this.detail : undefined)
      )
    },

    _patch(id: string, patch: Partial<FeedbackCard>): void {
      for (const list of [this.items, this.adminItems, this.mineItems]) {
        const card = list.find((i) => i.id === id)
        if (card) Object.assign(card, patch)
      }
      if (this.detail?.id === id) Object.assign(this.detail, patch)
    },

    /** 当前详情**并且**是这条时才返回它：慢响应回来时页面可能已经换了一条。 */
    _detailIfCurrent(id: string): FeedbackDetail | null {
      return this.detail && this.detailId === id ? this.detail : null
    },

    /** 楼里的一条评论。评论**只存在于详情那一份数据里**（卡片上只有条数），所以上面
     *  那两个动作都从这里拿 —— `_find` / `_patch` 管的是反馈级的字段，它们看不见
     *  `detail.thread`，拿它们改评论会静默什么都不做：按钮按得下去、有按下效果、
     *  数字一动不动，控制台里一句话也没有。 */
    _commentOf(id: string, commentId: string): FeedbackComment | undefined {
      return this._detailIfCurrent(id)?.thread.find((c) => c.id === commentId)
    },
  },
})
