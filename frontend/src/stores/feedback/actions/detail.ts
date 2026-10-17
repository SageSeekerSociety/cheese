/**
 * 一条反馈的详情那一侧：打开它、支持它、在它下面评论、把它删掉。
 *
 * 这一组动作共用两个东西：`state.detail` 那一份**当前正看着的**数据（评论只存在于
 * 它里面），以及详情缓存 `session.detailCache`。删除和点赞都就地改 `detail` 上的
 * 字段，所以它们必须走 `_detailIfCurrent` / `_commentOf` 拿引用，而不是自己按 id 找 ——
 * 请求在飞的时候页面可能已经换了另一条。
 */

import type { FeedbackCard, FeedbackComment, FeedbackDetail } from '@/cx_types'

import { message } from '../helpers'
import { session } from '../session'

import {
  ApiError,
  createFeedbackComment,
  deleteFeedback,
  deleteFeedbackComment,
  getFeedback,
  likeFeedbackComment,
  listFeedbackComments,
  supportFeedback,
  unlikeFeedbackComment,
  unsupportFeedback,
} from '@/api'
import { t } from '@/i18n'

interface DetailSelf {
  detail: FeedbackDetail | null
  detailId: string | null
  detailLoading: boolean
  moreCommentsLoading: boolean
  moreRepliesLoading: Record<string, boolean>
  items: FeedbackCard[]
  adminItems: FeedbackCard[]
  mineItems: FeedbackCard[]
  error: string | null
  _find(id: string): FeedbackCard | undefined
  _patch(id: string, patch: Partial<FeedbackCard>): void
  _detailIfCurrent(id: string): FeedbackDetail | null
  _commentOf(id: string, commentId: string): FeedbackComment | undefined
  refreshCounts(): Promise<void>
}

export const detailActions = {
  /* ---- 详情 ---- */

  /** 拉一条的全部内容。看不见的 id 服务端回 404，这里如实把它记成「没有」。
   *
   *  命中缓存就**先画旧的那份、不置空**：置空的那一下整页会闪成骨架，而「打一
   *  条刚看过的反馈」恰恰是最常见的那次导航（列表点进去、返回、再点进去）。
   *  **背后照样重拉**，回来才替换 —— 和 `loadList` 一个口径：缓存只是先画。
   *
   *  它**不碰**管理端那条路，原因写在 `session.detailCache` 上。
   */
  async loadDetail(this: DetailSelf, id: string): Promise<void> {
    this.detailId = id
    const cached = session.detailCache.get(id) ?? null
    this.detail = cached
    // 翻页的进度跟着这一条走：换一条反馈之后，上一条的「还剩几页」和「哪几栋楼
    // 正在取回复」都不成立了，留着只会让新页面上的按钮一进来就是转圈状态。
    // 这一句在缓存命中时也不能省：楼里的「正在取回复」挂的是**上一个 id**。
    this.moreCommentsLoading = false
    this.moreRepliesLoading = {}
    this.detailLoading = cached === null
    this.error = null
    try {
      let detail: FeedbackDetail
      let writes: number
      do {
        writes = session.detailWrites
        detail = await getFeedback(id)
        if (this.detailId !== id) return
      } while (writes !== session.detailWrites)
      this.detail = detail
      session.detailCache.set(id, detail)
    } catch (error) {
      if (this.detailId !== id) return
      // 失败回到「这条反馈打不开」那一态（见 `loadList` 那段：页面只有那几种
      // 画法，旧内容和错误条同时挂着是第五种）。
      this.detail = null
      this.error = message(error, t('feedback.errors.openFailed'))
      // 服务端说这条不该看见了（403/404）：缓存里那份现在是**错**的，留着它会在
      // 每一次进来时先画一条不该出现的内容。其余失败（网络抖了一下）**留着** —
      // 那一份仍然是这个人自己的、也仍然大概是对的，下次进来还能先画上。
      if (error instanceof ApiError && (error.status === 403 || error.status === 404)) session.detailCache.delete(id)
    } finally {
      if (this.detailId === id) this.detailLoading = false
    }
  },

  /* ---- 支持 ---- */

  /** 支持 / 取消支持。**回的是写完之后的服务端计数**，不是本地 ±1：两个人同时
   *  点会各自渲染出一个从来没存在过的数字。 */
  async toggleSupport(this: DetailSelf, id: string): Promise<void> {
    const card = this._find(id)
    if (!card) return
    try {
      const result = card.supported ? await unsupportFeedback(id) : await supportFeedback(id)
      session.detailWrites += 1
      this._patch(id, { supports: result.count, supported: result.supported })
      // 栏位上的数字也要跟着动：「热门」是服务端按「支持数 ≥ 门槛且未解决」现算的，
      // 就在这一下跨过门槛的条目，本地那份计数当场就对不上了（列表 6 条、Tab 上写着
      // 5）。计数只能问服务端，前端再算一遍就是第二份实现。
      void this.refreshCounts()
    } catch (error) {
      // 办完了的反馈会被拒（412）。那不是故障，是「别再点了」—— 如实显示服务端
      // 那句话，比一个静默失败好。
      this.error = message(error, t('feedback.errors.actionFailed'))
    }
  },

  /* ---- 评论 ---- */

  /** 发一条评论。`parentId` 指向**任意**一条评论：层级由服务端折上去，前端不判断。
   *  「这条在回谁」（`reply_to_handle`）也是服务端写下来的 —— 折到顶层之后前端再也
   *  推不出来，猜一个的结果是每个人看到的指代都不一样。
   *
   *  **返回成功与否**，调用方靠它决定「草稿留着还是清掉」：一次 500 之后把用户写了
   *  两段的话清掉，是这一版要修掉的东西之一。 */
  async addComment(this: DetailSelf, id: string, body: string, parentId?: string): Promise<boolean> {
    const text = body.trim()
    if (!text) return false
    try {
      const created = await createFeedbackComment(id, text, parentId ?? null)
      session.detailWrites += 1
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
      this.error = message(error, t('feedback.errors.commentSendFailed'))
      return false
    }
  },

  /** 往下翻**顶层评论**那一层。游标来自详情（`thread_next_cursor`），取完就没有了。
   *
   *  这里挡重复点击不是优化：同一个游标取两遍，同一个 `items` 会被追加两遍 ——
   *  屏幕上出现两条一模一样的评论，`:key` 也撞。 */
  async loadMoreComments(this: DetailSelf, id: string): Promise<void> {
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
      this.error = message(error, t('feedback.errors.commentsLoadFailed'))
    } finally {
      this.moreCommentsLoading = false
    }
  },

  /** 取**某一栋楼里**的下一段回复。游标挂在顶层评论那一行上（`replies_next_cursor`），
   *  和上面那条一样由服务端发、客户端原样送回去。 */
  async loadMoreReplies(this: DetailSelf, id: string, parentId: string): Promise<void> {
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
      this.error = message(error, t('feedback.errors.repliesLoadFailed'))
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
  async toggleCommentLike(this: DetailSelf, id: string, commentId: string): Promise<void> {
    const comment = this._commentOf(id, commentId)
    if (!comment) return
    try {
      const result = comment.liked
        ? await unlikeFeedbackComment(id, commentId)
        : await likeFeedbackComment(id, commentId)
      session.detailWrites += 1
      // 重新取一次而不是改上面那个引用：请求在飞的时候页面可能已经换了详情，也可能
      // 有人又点了一下。`_commentOf` 会挡住前一种（拿不到就什么都不做）。
      const current = this._commentOf(id, commentId)
      if (!current) return
      current.likes = result.count
      current.liked = result.liked
    } catch (error) {
      this.error = message(error, t('feedback.errors.actionFailed'))
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
  async deleteComment(this: DetailSelf, id: string, commentId: string): Promise<void> {
    try {
      await deleteFeedbackComment(id, commentId)
      session.detailWrites += 1
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
      this.error = message(error, t('feedback.errors.deleteFailed'))
    }
  },

  /** 删掉**整条反馈** —— 作者删自己的，平台管理员删任何一条。
   *
   *  能不能删不在这里判：按钮出不出现看服务端回的 `can_delete`（和路由上那一处
   *  `may_delete_feedback` 是同一个判据）。这里只负责删完把自己手上那几份数据一起
   *  收干净 —— **少收一处，下一页就会画出一条点不开的反馈**，而那比不删更糟。
   */
  async deleteFeedback(this: DetailSelf, id: string): Promise<boolean> {
    this.error = null
    try {
      await deleteFeedback(id)
    } catch (error) {
      this.error = message(error, t('feedback.errors.deleteFailed'))
      return false
    }
    // 三份列表都在内：`adminItems` 是管理端那条路（管理员从队列点进来删的，删完
    // 回队列时那一条不该还在）。
    this.items = this.items.filter((row) => row.id !== id)
    this.mineItems = this.mineItems.filter((row) => row.id !== id)
    this.adminItems = this.adminItems.filter((row) => row.id !== id)
    session.detailCache.delete(id)
    if (this.detailId === id) {
      this.detailId = null
      this.detail = null
    }
    // 计数里它还占着一格。重问一次 —— 数字由服务端数，不在前端减一。
    void this.refreshCounts()
    return true
  },
}
