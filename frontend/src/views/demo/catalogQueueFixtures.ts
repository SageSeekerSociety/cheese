/**
 * 队列那三件（`components/admin/queue/*.vue`）在预览站里吃的数据。
 *
 * 形状**不是编的**：就是那三件自己声明的 props（`AdminQueueHeader` 两样、
 * `AdminQueueToolbar` 七样、`AdminQueueEmpty` 五样），少一个键多一个键都在 `vue-tsc`
 * 那里当场红。值照抄页面那一层真实的传法 —— 四个栏位和四档状态页签的文案分别来自
 * `feedback.queue.lane.*` 和 `statusMeta()`（zh-CN 那份）、窗口 chip 的文案来自
 * `feedback.queue.window.*`，只把整句 i18n 之外的东西（`t` 的结果）落成字面量，
 * 因为这里给的是一份现成的 props，不装 i18n 的运行时。
 *
 * 为什么单独一份文件：`catalogFixtures.ts` 六百多行、`catalog.ts` 已经八百多行，
 * 三件的数据塞进去会顶到 `frontend/src` 那一千行的上限；和 `catalogRail.ts` 同一个
 * 理由。条目本身在 `catalogQueue.ts`，那边的 `CatalogEntry` 是 type-only 引用。
 *
 * 为什么这三件能在预览站里单独画：它们都是「只吃 props、只往上发事件」的那种组件
 * （`frontend_grade.py` 的 A 级），取数全在 `composables/useAdminQueue.ts` 里。
 */
import type { QueueView, QueueWindowKey } from '@/composables/useAdminQueue'
import type { FeedbackStatus } from '@/cx_types'
import type { AdminTab } from '@/stores/feedback'

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
