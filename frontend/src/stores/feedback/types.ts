/**
 * 反馈 store 对外说出口的**类型**。
 *
 * 它们是这一层的说法，不是服务端的形状（那些在 `@/api` 与 `@/cx_types` 里）：栏位和
 * 排序各是一个集合的两半，前端这一半得跟着服务端那一半改。
 *
 * 原来它们和 state、动作一起挤在 `stores/feedback.ts`（1708 行）里 —— 那个文件里
 * 任何两处改动都会撞在一起。类型单独一份之后，`stores/feedback` 仍然把它们原样转出
 * 去，调用方看不见这次搬家。
 */

import type { FeedbackKind, FeedbackVisibility } from '@/cx_types'

/** 公开列表的栏位。服务端 `PUBLIC_TABS` 是**同一个集合**：加一个栏位是后端改
 *  一处、前端跟着改一处类型的事。`active` 已删（2026-10-28）：它和状态药丸里的
 *  「处理中」是同一个切口，两处控件管一个轴。 */
export type FeedbackTab = 'all' | 'hot' | 'resolved'
/** 管理端的栏位。同上，服务端 `ADMIN_TABS`。 */
export type AdminTab = 'public' | 'private' | 'agent' | 'security'
/** 列表的排序。服务端 `SORTS` —— `new` 按时间倒序，`supports` 按支持数。
 *  管理台那个「最新 / 最热」切换就是这个类型的两半。 */
export type AdminSort = 'new' | 'supports'

/** 提交表单里那一份**内容**。三个入口共用它：反馈中心和「我的反馈」走独立页面
 *  （`/feedback/new`），会话里那张提案卡走对话框，而字段只有这一份
 *  （`SubmitFeedbackForm` 的两个壳）—— 各写一遍的话，「可见范围」这种后来才加的字段
 *  必然只会加进其中一份。
 *
 *  **「表单开着没有」不在这里**：壳自己知道自己在不在屏幕上（路由在不在、对话框开不
 *  开），store 里再留一个布尔就是同一个事实的第二份拷贝。 */
export interface AttachmentDraft {
  /** 服务端给的 id。提交时只发它。 */
  id: string
  name: string
  bytes: number
}

export interface FeedbackDraft {
  kind: FeedbackKind
  title: string
  body: string
  /** 「怎么重现」。只有 bug 那一类会问（见 `REPRO_KINDS`）—— 建议类要的是
   *  「你希望它变成什么样」，问它「怎么重现」是在问一个不存在的东西。 */
  repro: string
  /** 「你以为会发生什么」。bug 和建议都问：一句话就能把「哪里不对」和
   *  「你想让它怎样」分开，而这两件事在正文里常常混成一段读不出要求的话。 */
  expectation: string
  /** 标签。后端 `FeedbackCreate.tags` 一直收（`list[str]`，上限 20），卡片也一直在
   *  渲染 `item.tags`，只是表单以前没做。用来横向归类（登录 / 移动端 / 性能）。 */
  tags: string[]
  /** 已经传上去的附件，**按选中的顺序**。表单在选文件（或粘贴）那一刻就上传，这里
   *  存的是服务端给回来的 id 和显示要用的名字与大小 —— 提交时只把 id 发出去。
   *
   *  草稿落盘时它们**跟着一起存**（`lib/feedbackDraft.ts`）：一份 8MB 的截图在选了
   *  之后、提交之前刷新页面，字节早在服务端了，丢掉这三个 id 就得让人重选一遍。 */
  attachments: AttachmentDraft[]
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
    /** 提这张卡的那位 AI 队友（卡的 `author_handle`）：表单上「来自 X 的排查」叫它。 */
    agentHandle?: string
  }
  /** 这一份是从哪张提案卡打开的。有值走「发送」那条路（`accept`），
   *  没有就是人自己新提一条。 */
  proposal?: { topicId: string; blockId: string }
}
