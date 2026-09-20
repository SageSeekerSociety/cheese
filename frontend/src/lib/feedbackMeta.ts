/**
 * feedbackMeta.ts — 反馈界面的**呈现**词表：标签、颜色、图标。
 *
 * 它回答的只有「长什么样」，**不回答「有哪些取值」**。后者在服务端
 * （`GET /feedback/meta`，见 stores/feedback.ts 的 `meta`）：加一个状态是后端改
 * 一处的事，而前端的常量表要靠发版才能跟上。颜色则相反 —— 它是视觉决定，服务端
 * 不该知道 `--warn-wash` 这样的 token，所以它留在这里。
 *
 * 两边的接缝就是这几个 Record：服务端说「有这四个状态」，这里说「这四个各是什么
 * 颜色」。服务端多出一个这里没有的状态时，**`statusMeta` 会退回中性色而不是渲染空白**
 * —— 一行渲染不出来比颜色不对严重得多（见下）。
 *
 * 三条约束：
 *
 *   1. 颜色只写 token 名，不写 hex —— 写死的颜色在两个主题里必然错一个
 *      （docs/design-system.md），而 stylelint 会把 hex 判成新违规。`wash` 是底色、
 *      `ink` 是同一底色上的可读文字色，两者成对出现、不可混用。
 *   2. 顺序在这里只有一份（`STATUS_LADDER`），而且**只是兜底**：真正在用的那份
 *      从服务端来，页面通过 `store.statusLadder` 取。它留在这里是为了「meta 还没
 *      到」的那一帧不画出一条空线。
 *   3. 时间一律是「相对现在」算出来的（lib/relTime.ts），不从这一层出。
 */

import type { FeedbackKind, FeedbackPriority, FeedbackStatus } from '@/cx_types'

export interface StatusMeta {
  label: string
  wash: string
  ink: string
  dot: string
}

/** 中性色：服务端给了一个这里没见过的取值时用它。 */
const NEUTRAL: StatusMeta = { label: '未知', wash: 'var(--fill)', ink: 'var(--muted)', dot: 'var(--faint)' }

/**
 * 四级：收录 → 处理 → 修复 → 上线（服务端 `STATUS_LADDER` 是同一份的权威来源）。
 *
 * **最后两级共用绿色一家**，这是有意的，不是漏改：设计系统只有 ok / warn /
 * danger 三家状态色（docs/design-system.md §1.5），硬给第四级造一个新颜色，等于把
 * 一个语义拆成两个颜色去记。**但标签必须分开写**——「已修复」（代码改完了）和
 * 「已上线」（部署出去了）是两件不同的事，同名一族会让提交者以为「修复了就等于
 * 我这边能用了」。区分的责任在 label 和梯子上的位置（时间线按位置画），不在色相。
 *
 * `received` 用中性色而不是第三家：刚收下还没有任何进展，它不该看起来比「处理中」
 * 更值得注意，也不该看起来像出错。
 *
 * 「处理中」只有这一级，不往下再分：里面分了几步、谁在动哪一段，和服务端记录的是
 * 一回事，但**和提交者没关系** —— 他要知道的只是「有人在弄」。管理端要更细的口径时
 * 用指派和优先级两个字段，不往状态里塞。
 */
export const STATUS_META: Record<FeedbackStatus, StatusMeta> = {
  received: { label: '已收录', wash: 'var(--fill)', ink: 'var(--muted)', dot: 'var(--faint)' },
  in_progress: { label: '处理中', wash: 'var(--warn-wash)', ink: 'var(--warn-ink)', dot: 'var(--warn)' },
  resolved: { label: '已修复', wash: 'var(--ok-wash)', ink: 'var(--ok-ink)', dot: 'var(--ok)' },
  deployed: { label: '已上线', wash: 'var(--ok-wash)', ink: 'var(--ok-ink)', dot: 'var(--ok)' },
}

/** 状态梯子的**兜底**顺序，给 meta 还没到的那一帧用。 */
export const STATUS_LADDER: FeedbackStatus[] = ['received', 'in_progress', 'resolved', 'deployed']

/**
 * 「办完了」的那两级：修复和上线。
 *
 * 这是服务端 `CLOSED_STATUSES`（backend/app/domain/feedback/repositories.py）在前端的
 * 同一份。**别再写成 `status !== 'resolved'`**：上线这一级是后加的，列表卡片和详情页
 * 各写一遍 `!== 'resolved'` 的后果是已上线的条目按钮亮着、点下去服务端回 412，页面上
 * 只有一句「这条反馈已经办完了，不再接受支持」——按钮说能做、服务端说不能，人就不知道该
 * 信哪个。这个 bug 是端到端实跑抓到的，单测看不见（两处都各自「对」）。
 */
export const CLOSED_STATUSES: FeedbackStatus[] = ['resolved', 'deployed']

/** 这条还能不能支持/评论。列表卡片和详情页共用这一个判断。 */
export const isClosed = (status: FeedbackStatus | undefined): boolean => !!status && CLOSED_STATUSES.includes(status)

export const KIND_LABEL: Record<FeedbackKind, string> = {
  bug: 'Bug',
  suggestion: '建议',
  other: '其他',
}

export const PRIORITY_META: Record<FeedbackPriority, StatusMeta> = {
  low: { label: '低', wash: 'var(--fill)', ink: 'var(--muted)', dot: 'var(--faint)' },
  normal: { label: '普通', wash: 'var(--fill-2)', ink: 'var(--text)', dot: 'var(--muted)' },
  high: { label: '高', wash: 'var(--warn-wash)', ink: 'var(--warn-ink)', dot: 'var(--warn)' },
  urgent: { label: '紧急', wash: 'var(--danger-wash)', ink: 'var(--danger-ink)', dot: 'var(--danger)' },
}

/** 来源。**不是**一个字段 —— 后端给的是 `author_is_agent`，因为它和
 *  「谁按的发送」是两件事（提案卡：agent 写的、人发的）。 */
export const SOURCE_LABEL = {
  user: '用户提交',
  agent: 'Agent 发现',
} as const

/** 拿一个状态的呈现，取不到就回中性。见文件开头第 2 条理由。 */
export function statusMeta(status: FeedbackStatus | undefined): StatusMeta {
  return (status && STATUS_META[status]) || NEUTRAL
}

/** 拿一个优先级的呈现，取不到就回中性。 */
export function priorityMeta(priority: FeedbackPriority | undefined): StatusMeta {
  return (priority && PRIORITY_META[priority]) || NEUTRAL
}
