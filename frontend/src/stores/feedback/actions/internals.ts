/**
 * 四个下划线开头的**内部小工具**：不是给页面用的 API，是别的动作在自己人之间用的。
 *
 * 它们回答的是同一个问题 —— 「这一条现在在哪份数据里」。三份列表加详情各存一份，
 * 谁都不许自己按 id 去找：找漏一份的表现是「那个页面上的按钮点了没反应」，连错误
 * 都不报。
 */

import type { FeedbackCard, FeedbackComment, FeedbackDetail } from '@/cx_types'

interface InternalsSelf {
  items: FeedbackCard[]
  adminItems: FeedbackCard[]
  mineItems: FeedbackCard[]
  detail: FeedbackDetail | null
  detailId: string | null
  _detailIfCurrent(id: string): FeedbackDetail | null
}

export const internalsActions = {
  /* ---- 内部小工具（下划线开头：不是给页面用的 API） ---- */

  /** 一条反馈现在在**哪一份数据**里。三份列表都要找。前两份（公开、管理）是
   *  「点开列表再点支持」，`mineItems` 是同一件事发生在「我的反馈」页上——漏掉它
   *  的表现是那一页的支持按钮点了没反应（`toggleSupport` 找不到卡片就直接 return，
   *  连错误都不报）。详情那一份管的是深链进来的页面。
   *
   *  列表里没有就找详情：直接打开/刷新 `/feedback/<id>` 时三份列表都是空的
   *  （那一页不拉列表），而支持按钮只认这一份。 */
  _find(this: InternalsSelf, id: string): FeedbackCard | undefined {
    return (
      this.items.find((i) => i.id === id) ??
      this.adminItems.find((i) => i.id === id) ??
      this.mineItems.find((i) => i.id === id) ??
      (this.detail?.id === id ? this.detail : undefined)
    )
  },

  _patch(this: InternalsSelf, id: string, patch: Partial<FeedbackCard>): void {
    for (const list of [this.items, this.adminItems, this.mineItems]) {
      const card = list.find((i) => i.id === id)
      if (card) Object.assign(card, patch)
    }
    if (this.detail?.id === id) Object.assign(this.detail, patch)
  },

  /** 当前详情**并且**是这条时才返回它：慢响应回来时页面可能已经换了一条。 */
  _detailIfCurrent(this: InternalsSelf, id: string): FeedbackDetail | null {
    return this.detail && this.detailId === id ? this.detail : null
  },

  /** 楼里的一条评论。评论**只存在于详情那一份数据里**（卡片上只有条数），所以上面
   *  那两个动作都从这里拿 —— `_find` / `_patch` 管的是反馈级的字段，它们看不见
   *  `detail.thread`，拿它们改评论会静默什么都不做：按钮按得下去、有按下效果、
   *  数字一动不动，控制台里一句话也没有。 */
  _commentOf(this: InternalsSelf, id: string, commentId: string): FeedbackComment | undefined {
    return this._detailIfCurrent(id)?.thread.find((c) => c.id === commentId)
  },
}
