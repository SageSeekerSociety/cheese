/**
 * 状态 / 类型 / 来源这几个**枚举取值的名字**，走 i18n 的那一份。
 *
 * 为什么要多这一层：这三个词以前只有一份来源（`lib/feedbackMeta.ts` 的
 * `STATUS_META` / `KIND_LABEL` / `SOURCE_LABEL`），而那一份写的是中文。管理端整页都在
 * `t()` 上，反馈这几页却是「导航跟着语言走、页面里那几颗药丸永远是中文」—— 英文访客
 * 读到的是一个半中半英的界面（见 catalog.spec.ts 顶上那段，同一个坑 #929 已经吃过一次）。
 *
 * **颜色留在 `feedbackMeta`，名字搬到这里**：颜色是视觉决定、服务端不该知道 token 名，
 * 而名字是文案、必须跟着语言走。两张表按同一批键对齐，`feedback.status.*` 就是
 * `STATUS_META` 的键集。
 *
 * 取不到的取值**不渲染空白**，退回中性那一句（`feedbackMeta.statusMeta` 的兜底是同一
 * 条规矩：一行渲染不出来比名字不对严重得多）。
 */
import type { FeedbackKind, FeedbackStatus } from '@/cx_types'

import { t } from '@/i18n'

const STATUS_KEY: Partial<Record<FeedbackStatus, string>> = {
  received: 'feedback.status.received',
  in_progress: 'feedback.status.in_progress',
  resolved: 'feedback.status.resolved',
  deployed: 'feedback.status.deployed',
  declined: 'feedback.status.declined',
}

/** 时间线上**还没轮到**那一档的名字。中文去掉「已」（已修复 → 修复），英文换成动词
 *  原形（Resolved → Resolve）—— 过去分词本身就带着「已经」，还没发生的档位顶着它，
 *  和中文顶着「已修复」是同一个错。走到了的档位仍用 `STATUS_KEY`。 */
const PENDING_STATUS_KEY: Partial<Record<FeedbackStatus, string>> = {
  received: 'feedback.status.pending.received',
  in_progress: 'feedback.status.pending.in_progress',
  resolved: 'feedback.status.pending.resolved',
  deployed: 'feedback.status.pending.deployed',
}

/** 时间线上**已经走过去**那一档的名字。只有「处理中」到了这里要换一副面孔
 *  （处理中 → 已处理）：它说的是「有人正在弄」，可这一步已经过去了；其余三档
 *  「正在」和「走过」本来就是同一个名字。 */
const PASSED_STATUS_KEY: Partial<Record<FeedbackStatus, string>> = {
  received: 'feedback.status.passed.received',
  in_progress: 'feedback.status.passed.in_progress',
  resolved: 'feedback.status.passed.resolved',
  deployed: 'feedback.status.passed.deployed',
}

const KIND_KEY: Partial<Record<FeedbackKind, string>> = {
  bug: 'feedback.kind.bug',
  suggestion: 'feedback.kind.suggestion',
  other: 'feedback.kind.other',
}

/** 状态的名字。服务端多出一个这里没有的状态时回「未知」，不是空串。 */
export function statusLabel(status: FeedbackStatus | undefined): string {
  const key = status ? STATUS_KEY[status] : undefined
  return key ? t(key) : t('feedback.status.unknown')
}

/** 时间线上**还没轮到**的那一档。取不到时退回 `statusLabel`：宁可名字带着「已」，
 *  也不要一格空着。 */
export function pendingStatusLabel(status: FeedbackStatus | undefined): string {
  const key = status ? PENDING_STATUS_KEY[status] : undefined
  return key ? t(key) : statusLabel(status)
}

/** 时间线上**已经走过去**的那一档。取不到时同样退回 `statusLabel`。 */
export function passedStatusLabel(status: FeedbackStatus | undefined): string {
  const key = status ? PASSED_STATUS_KEY[status] : undefined
  return key ? t(key) : statusLabel(status)
}

/** 类型（Bug / 建议 / 其他）。服务端多出一个没见过的类型时**原样显示它自己的名字**：
 *  少一个类型比多一个生词严重 —— 少掉的那一个会在列表里看着像「这条没有类型」。 */
export function kindLabel(kind: FeedbackKind | undefined): string {
  const key = kind ? KIND_KEY[kind] : undefined
  return key ? t(key) : kind ?? ''
}

/** 来源。**不是**一个字段 —— 后端给的是 `author_is_agent`，因为它和「谁按的发送」
 *  是两件事（提案卡：agent 写的、人发的）。 */
export function sourceLabel(source: 'user' | 'agent'): string {
  return t(source === 'agent' ? 'feedback.source.agent' : 'feedback.source.user')
}
