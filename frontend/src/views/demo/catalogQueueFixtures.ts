/**
 * 队列那一串在预览站里吃的数据。
 *
 * 形状**不是编的**：每一份就是那一件自己声明的 props（`AdminQueueHeader` 两样、
 * `AdminQueueToolbar` 七样、`AdminQueueEmpty` 五样、`AdminAssigneeSelect` 六样、
 * `AdminQueueDetail` 九样、`AdminQueuePageView` 那一整套），少一个键多一个键都在
 * `vue-tsc` 那里当场红。值照抄调用方真实的传法 —— 四个栏位和四档状态页签的文案分别
 * 来自 `feedback.queue.lane.*` 和 `statusMeta()`（zh-CN 那份）、窗口 chip 的文案来自
 * `feedback.queue.window.*`，只把整句 i18n 之外的东西（`t` 的结果）落成字面量，
 * 因为这里给的是一份现成的 props，不装 i18n 的运行时。
 *
 * 一条反馈的全文不在这儿另写一份：`catalogScenesPages2Fixtures.ts` 的 `feedback()`
 * 就是那个形状的工厂，队列这几件读的字段它都有（`FeedbackCard` 是 `FeedbackDetail`
 * 的子集，队列行只画得少，不是另一种数据）。候选人是名册页那两位。
 *
 * 为什么单独一份文件：`catalogFixtures.ts` 六百多行、`catalog.ts` 已经八百多行，
 * 这一串的数据塞进去会顶到 `frontend/src` 那一千行的上限；和 `catalogRail.ts` 同一个
 * 理由。条目本身在 `catalogQueue.ts`，那边的 `CatalogEntry` 是 type-only 引用。
 *
 * 为什么这几件能在预览站里单独画：它们都是「只吃 props、只往上发事件」的那种组件
 * （`frontend_grade.py` 的 A 级），取数全在 `composables/useAdminQueue.ts` 里。
 */
import type { AdminCandidate } from '@/api'
import type { QueueView, QueueWindowKey } from '@/composables/useAdminQueue'
import type { FeedbackCard, FeedbackDetail, FeedbackStatus } from '@/cx_types'
import type { AdminTab } from '@/stores/feedback'

import { MEMBERS_CANDIDATES } from './catalogScenesMisc2Fixtures'
import { feedback } from './catalogScenesPages2Fixtures'

import { STATUS_LADDER } from '@/lib/feedbackMeta'

/** 四个栏位（服务端的 `tab`）。文案与 `feedback.queue.lane.*` 逐字一致。 */
export const QUEUE_LANES: { value: AdminTab; label: string }[] = [
  { value: 'public', label: '公开' },
  { value: 'private', label: '私密' },
  { value: 'agent', label: 'AI 队友提的' },
  { value: 'security', label: '安全' },
]

/** 状态页签那一排：`all` + 状态阶梯的四档。文案与 `tabOptions` 一致
 *  （`all` 走 `feedback.queue.tab.all`，其余走 `statusMeta().label`）。 */
export const QUEUE_STATUS_TABS: { value: FeedbackStatus | 'all'; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'received', label: '已收录' },
  { value: 'in_progress', label: '处理中' },
  { value: 'resolved', label: '已修复' },
  { value: 'deployed', label: '已上线' },
]

/** 一颗窗口 chip：可见那段字，和那颗 `×` 的读屏名字（`clearAria` 里带着同一段字）。 */
export function queueWindowChip(
  key: QueueWindowKey,
  text: string
): {
  key: QueueWindowKey
  text: string
  clearAria: string
} {
  return { key, text, clearAria: `清除这个日期筛选：${text}` }
}

/** 页头那两样：未读条数、当前视图。 */
export function queueHeaderProps(over: { unread?: number; view?: QueueView } = {}) {
  return { unread: 12, view: 'list' as QueueView, ...over }
}

/** 工具行那七样。`lanes` / `statusOptions` 不由调用方逐格传：它们就是上面那两张表。 */
export function queueToolbarProps(
  over: {
    lane?: AdminTab
    query?: string
    chips?: { key: QueueWindowKey; text: string; clearAria: string }[]
    status?: FeedbackStatus | 'all'
    showScopeNote?: boolean
  } = {}
) {
  return {
    lane: 'public' as AdminTab,
    lanes: QUEUE_LANES,
    query: '',
    chips: [] as { key: QueueWindowKey; text: string; clearAria: string }[],
    status: 'all' as FeedbackStatus | 'all',
    statusOptions: QUEUE_STATUS_TABS,
    showScopeNote: false,
    ...over,
  }
}

/** 四态块那五样。 */
export function queueEmptyProps(
  over: { title?: string; desc?: string; action?: string; tone?: 'neutral' | 'error'; raw?: string | null } = {}
) {
  return {
    title: '暂无反馈',
    desc: '还没有人提交反馈。反馈提交后会出现在这条队列里。',
    action: '',
    tone: 'neutral' as 'neutral' | 'error',
    raw: null as string | null,
    ...over,
  }
}

// ---- 拆出来之后新变成 A 级的那三件 -----------------------------------------

/** 「指派给谁」那六样。候选与那串词本来在组件自己的 composable 里，现在由调用方给。 */
export function queueAssigneeProps(
  over: {
    modelValue?: string | null
    search?: string
    candidates?: AdminCandidate[]
    searching?: boolean
    dense?: boolean
  } = {}
) {
  return {
    modelValue: 'cheese' as string | null,
    label: '指派给',
    dense: true,
    search: '',
    candidates: MEMBERS_CANDIDATES,
    searching: false,
    ...over,
  }
}

/** 摆在详情里的那一条（宽屏那一档和抽屉那一档共用同一条）。 */
export const QUEUE_DETAIL_ITEM: FeedbackDetail = feedback()

/** 一条还没指派过的：分诊面板收起时那一行说「未指派」，不是空白。 */
export const UNASSIGNED_FEEDBACK: FeedbackDetail = feedback({
  id: 'f-5',
  display_id: 'FB-39',
  title: '通知里点进来会回到首页',
  summary: '点通知里的链接，落在首页而不是那一条。',
  status: 'received',
  assignee_handle: null,
  supports: 1,
  created_at: '2026-10-09T08:00:00Z',
})

/** 详情那九样。`item: null` 加上 `loading` 是加载态，`error` 是打不开那一态。 */
export function queueDetailProps(
  over: {
    item?: FeedbackDetail | null
    loading?: boolean
    error?: string | null
    triageOpen?: boolean
    statuses?: FeedbackStatus[]
    writeError?: string
    candidates?: AdminCandidate[]
    search?: string
    searching?: boolean
  } = {}
) {
  return {
    item: feedback(),
    loading: false,
    error: null as string | null,
    triageOpen: true,
    statuses: STATUS_LADDER,
    writeError: '',
    candidates: MEMBERS_CANDIDATES,
    search: '',
    searching: false,
    ...over,
  }
}

/** 队列上那几行。`FeedbackCard` 是 `FeedbackDetail` 的子集（列表行只画卡片上那几格），
 *  所以这一份就是 `feedback()` 改几个字段 —— 不是另一种数据，不另造一份形状。 */
export const QUEUE_CARDS: FeedbackCard[] = [
  feedback(),
  feedback({
    id: 'f-3',
    display_id: 'FB-41',
    title: '导出的成员表里没有「加入时间」',
    summary: '导出之后少了一列。',
    status: 'received',
    visibility: 'private',
    priority: 'high',
    assignee_handle: null,
    supports: 2,
    comments: 0,
    created_at: '2026-10-12T06:00:00Z',
    last_activity_at: '2026-10-12T06:00:00Z',
  }),
  feedback({
    id: 'f-4',
    display_id: 'FB-40',
    kind: 'suggestion',
    title: '看板上的卡片想按优先级排',
    summary: '现在只能按时间排。',
    status: 'resolved',
    security: true,
    author_handle: 'cheese',
    author_is_agent: true,
    author_avatar_id: null,
    assignee_handle: 'bob',
    supports: 11,
    comments: 5,
    created_at: '2026-10-10T09:00:00Z',
  }),
]

/** 整页那一件（`AdminQueuePageView`）收的那一整套。 */
export function queuePageProps(
  over: {
    isWide?: boolean
    detailOpen?: boolean
    detail?: FeedbackDetail | null
    detailLoading?: boolean
    detailError?: string | null
    triageOpen?: boolean
    view?: QueueView
    lane?: AdminTab
    query?: string
    chips?: { key: QueueWindowKey; text: string; clearAria: string }[]
    status?: FeedbackStatus | 'all'
    showScopeNote?: boolean
    items?: FeedbackCard[]
    cursorId?: string | null
    loading?: boolean
    emptyTitle?: string
    emptyDesc?: string
    emptyAction?: string
    emptyTone?: 'error' | 'neutral'
    undo?: { message: string } | null
  } = {}
) {
  const items = over.items ?? QUEUE_CARDS
  return {
    isWide: true,
    detailOpen: false,
    detail: null as FeedbackDetail | null,
    detailLoading: false,
    detailError: null as string | null,
    detailWriteError: '',
    triageOpen: true,
    statuses: STATUS_LADDER,
    candidates: MEMBERS_CANDIDATES,
    search: '',
    searching: false,
    unread: 12,
    view: 'list' as QueueView,
    lane: 'public' as AdminTab,
    lanes: QUEUE_LANES,
    query: '',
    chips: [] as { key: QueueWindowKey; text: string; clearAria: string }[],
    status: 'all' as FeedbackStatus | 'all',
    statusOptions: QUEUE_STATUS_TABS,
    showScopeNote: false,
    items,
    cursorId: 'f-1' as string | null,
    cursorIndex: 0,
    loading: false,
    showStatusWord: true,
    emptyTitle: '暂无反馈',
    emptyDesc: '还没有人提交反馈。反馈提交后会出现在这条队列里。',
    emptyAction: '',
    emptyTone: 'neutral' as 'error' | 'neutral',
    scope: false,
    rows: items.length,
    hasPrev: false,
    hasNext: false,
    undo: null as { message: string } | null,
    ...over,
  }
}
