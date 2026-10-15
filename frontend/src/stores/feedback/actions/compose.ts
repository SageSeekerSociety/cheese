/**
 * 写一条反馈：那份**表单**（三个入口共用一份字段），以及会话里那张**提案卡**。
 *
 * 提案卡和表单住在一起，是因为它们在同一个动作上会合：卡上那一下提交走的就是
 * `submit`，只是请求打的是 `/feedback-proposals/{id}/accept`。分成两个模块的话，
 * 「表单里多一栏、卡上那条路漏了它」这种偏差就没人拦得住。
 *
 * `state.draft` 是**内容**，不是「表单开着没有」—— 壳自己知道自己在不在屏幕上。
 */

import type { FeedbackCard, FeedbackProposal } from '@/cx_types'
import type { FeedbackDraft } from '../types'

import { DRAFT_SAVE_DEBOUNCE_MS, emptyDraft, message, toCreateBody } from '../helpers'
import { dropListCaches, session } from '../session'

import { acceptFeedbackProposal, createFeedback, dismissFeedbackProposal, listFeedbackProposals } from '@/api'
import { t } from '@/i18n'
import {
  clearLiveFeedbackDraft,
  forgetFeedbackDraft,
  isDraftMeaningful,
  loadFeedbackDraft,
  parkFeedbackDraft,
  saveFeedbackDraft,
  takeParkedFeedbackDraft,
} from '@/lib/feedbackDraft'

interface ComposeSelf {
  draft: FeedbackDraft
  draftRestored: boolean
  submitting: boolean
  error: string | null
  lastSubmittedId: string | null
  items: FeedbackCard[]
  mineItems: FeedbackCard[]
  listRequested: number
  mineRequested: number
}

export const composeActions = {
  /* ---- 提交表单 ---- */

  /** **准备一份草稿**，不做「打开」这件事 —— 三个入口都先调它，然后把壳拉起来
   *  （两个列表入口 push 到 `/feedback/new`，会话里那张提案卡开对话框）。
   *
   *  `preset` 只有提案卡那条路会传：它整份替换表单内容，所以人自己打了一半的那份
   *  先挪去 parked。 */
  openSubmit(this: ComposeSelf, preset: Partial<FeedbackDraft> = {}): void {
    const incoming = Object.keys(preset).length > 0
    if (incoming) {
      // 人已经打了一半的那份挪去 parked、而不是丢掉：这一步整份替换表单内容，
      // 而替换不是他的本意（他点的是会话里那张卡）。下一次裸开表单时它会自己回来。
      parkFeedbackDraft(this.draft)
      this.draft = { ...emptyDraft(), ...preset }
      // 卡片带过来的那份是**新**的一份，不是从盘上捞的：那条「已恢复草稿」的提示
      // 跟着它一起清掉，否则刚从卡上进来的人会看到一句「已恢复上次没写完的草稿」，
      // 而屏幕上这份明明是芝士刚递过来的。
      this.draftRestored = false
      // 卡片带过来的那份也是「打了一半的东西」—— 关掉、刷新、再打开，它该还在。
      saveFeedbackDraft(this.draft)
    } else if (isDraftMeaningful(this.draft)) {
      // 手上这份就是最新的（`touchDraft` 一直在往盘上写），接着写。
    } else {
      // 手上是空的：先把上次被替换下去的那份拿回来，没有再取「上次没写完的」。
      // 这一步是给**页面自己刷新**（发版时 service worker 会 reload，见 pwa.ts）
      // 和「关掉又想起来」用的 —— 少这一步，几百字在用户眼皮底下消失。
      const restored = takeParkedFeedbackDraft() ?? loadFeedbackDraft()
      this.draft = { ...emptyDraft(), ...(restored ?? {}) }
      // 捞回来这件事**要在界面上说出来**（见 SubmitFeedbackForm 的那条提示）：
      // 打开表单看到一段不是自己刚打的字，不说一声就像串了别人的内容。
      this.draftRestored = isDraftMeaningful(restored)
    }
    // 上一次那条失败的原话是给**上一次**那个表单看的。新开一份时清掉，否则刚进
    // 来的人会看到一句「提交失败」，而他什么都还没提交。
    this.error = null
  },

  /** 表单里改了任何一栏，调用方在字段的 `update:model-value` 上打一下这里。
   *
   *  **落盘要防抖**：每敲一个字写一次 `localStorage` 是不必要的（而且中文输入法
   *  在候选阶段就会发事件）。防抖窗口是 `DRAFT_SAVE_DEBOUNCE_MS`，关表单和提交
   *  各会额外收一次尾（见 `closeSubmit` / `submit`），所以窗口里那几下不会漏。
   *
   *  **不传内容进来**：读的是 `this.draft` 那一刻的值。让调用方把值传进来就等于
   *  每个字段自己折一份，而它们迟早会漏掉一栏。 */
  touchDraft(this: ComposeSelf): void {
    if (session.draftTimer) clearTimeout(session.draftTimer)
    session.draftTimer = setTimeout(() => {
      session.draftTimer = null
      saveFeedbackDraft(this.draft)
    }, DRAFT_SAVE_DEBOUNCE_MS)
  },

  /** 关掉表单不是「不要了」：把防抖窗口里那几下收尾写下去。**不负责关界面** ——
   *  壳自己关（页面 push 走、对话框合上），这里只管草稿和那一次性的提示。 */
  closeSubmit(this: ComposeSelf): void {
    if (session.draftTimer) clearTimeout(session.draftTimer)
    session.draftTimer = null
    saveFeedbackDraft(this.draft)
    this.lastSubmittedId = null
  },

  /** 「丢弃草稿」：盘上**两份槽位**一起抹掉，表单回到空白。
   *
   *  两份都要抹是有意的：只清「正在写的」那一格，人会看到自己刚说不要的那份**下次
   *  打开又一字不差地回来**（它从 parked 那一格回来了），而「丢弃」这个词承诺的正是
   *  不回来。 */
  discardDraft(this: ComposeSelf): void {
    if (session.draftTimer) clearTimeout(session.draftTimer)
    session.draftTimer = null
    forgetFeedbackDraft()
    this.draft = emptyDraft()
    this.draftRestored = false
    this.error = null
  },

  /** 提交。返回新条目的 **uuid**（路由用它），失败回 null 并把原因写进 error。
   *
   *  两条路都从这里走：普通提交是 `POST /feedback`；从提案卡来的那条是
   *  `POST /topics/{id}/feedback-proposals/{block_id}/accept` —— 后者的作者是提案
   *  的 agent、提交者是按下发送的人，两个都由服务端从卡和会话里取，所以客户端
   *  连作者名都不用传（传了也不算数）。
   *
   *  **提交完不关任何界面**：谁把表单摆在屏幕上，谁负责收它（页面 replace 到详情
   *  页，对话框自己合上）。store 在这里关界面的话，两个壳会各关一次，而第二次关
   *  的是别人。 */
  async submit(this: ComposeSelf): Promise<string | null> {
    const draft = this.draft
    // 必填两栏都查：按钮 disabled 不是替代品 —— 这个 action 还有别的调用点（会话里
    // 那张卡、以后的快捷键），少查一栏就会出现一条只有标题、没有任何正文的反馈。
    // 提交的门槛是「标题 + 说明」，`repro` / `expectation` 保持选填，理由见
    // SubmitFeedbackForm 的注释。
    if (!draft.title.trim() || !draft.body.trim() || this.submitting) return null
    this.submitting = true
    this.error = null
    try {
      const body = toCreateBody(draft)
      const detail = draft.proposal
        ? await acceptFeedbackProposal(draft.proposal.topicId, draft.proposal.blockId, body)
        : await createFeedback(body)
      this.lastSubmittedId = detail.id
      this.draft = emptyDraft()
      this.draftRestored = false
      // 这份东西已经变成一条反馈了，盘上那份不该再留着：否则下次打开表单它整份
      // 弹回来，看着像「刚提交的那条又回到草稿里了」。**只清「正在写的」那一格** ——
      // 被替换下去的那份（有人自己打了一半的）和这次提交无关，还得在。
      if (session.draftTimer) clearTimeout(session.draftTimer)
      session.draftTimer = null
      clearLiveFeedbackDraft()
      // 列表和各种计数都变了：清掉，让下次挂载重新拉，而不是在这里手改数组
      // （手改的那份迟早和下一页对不上）。`mineItems` 也得清 —— 刚提的这条
      // 属于「我提的」，留着旧的会让「我的反馈」少一条，而那正是这一页要回答的
      // 那个问题。
      //
      // **缓存要一起丢**：只清屏幕上那份的话，下次挂载会命中缓存、把提交之前那
      // 一页原样画回来，再在背后换成新的 —— 那一下看上去就是「我刚提的反馈不见
      // 了」，而它其实只是还没被那次重拉带上。
      dropListCaches()
      this.items = []
      this.mineItems = []
      // 偏移量跟着数组一起归零：两者说的是同一件事（手上这份从哪儿来），
      // 只清一个的话下一次「加载更多」会从旧偏移量上接着要。
      this.listRequested = 0
      this.mineRequested = 0
      // 顺手把刚落地的这条写进详情缓存：提交之后紧接着去的就是它的详情页，而手上
      // 这一份就是服务端刚回的**整条**。这里能这么写不是猜的 —— `POST /feedback`
      // 和 `GET /feedback/{id}` 在服务端走的是同一个 `_detail(...)` 序列化，形状
      // 一样（见 routes/feedback.py）。
      session.detailCache.set(detail.id, detail)
      return detail.id
    } catch (error) {
      this.error = message(error, t('feedback.errors.submitFailed'))
      // 失败时**盘上那份留着**，而且这里补一次收尾：提交是防抖窗口里最可能发生的
      // 「人停下来了」，而那一下不该让人在刷新后丢掉刚写完的东西。
      if (session.draftTimer) clearTimeout(session.draftTimer)
      session.draftTimer = null
      saveFeedbackDraft(this.draft)
      return null
    } finally {
      this.submitting = false
    }
  },

  /* ---- 提案卡 ---- */

  /** 这个话题里还活着的提案卡。**已经「不用」过的不会回来** —— 那件事记在服务端
   *  （指纹），不是组件状态（原型那份一刷新就回来）。 */
  async loadProposals(this: ComposeSelf, topicId: string): Promise<FeedbackProposal[]> {
    try {
      return await listFeedbackProposals(topicId)
    } catch {
      // 提案是「顺路问一句」，拉不到就当没有 —— 不能因为它让对话栏报错。
      return []
    }
  },

  /** 记下一次「不用」。服务端没记下就抛出去 —— 不在这里咽下：卡要把这一下放回
   *  屏幕上（人上次那一下没算数，他还得知道），咽下它等于装作成功。 */
  async dismissProposal(this: ComposeSelf, topicId: string, blockId: string): Promise<void> {
    await dismissFeedbackProposal(topicId, blockId)
  },
}
