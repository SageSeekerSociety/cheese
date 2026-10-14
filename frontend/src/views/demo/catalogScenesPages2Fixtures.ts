/**
 * 预览站里第二批拆出来的画面（`catalogScenesPages2.ts`）读的数据。
 *
 * 四件画面各有一份自己的形状：功能数据页认出 / 认不出的那个功能、一条反馈的全文、
 * 我的连接上的邮箱与飞书、技能列表。全是产品里真会出现的形状 —— 照着真接口的响应造
 * （`api/integrations.ts`、`cx_types.ts` 的反馈那几个别名），照着真页面里那几种状态写。
 *
 * 这里只出数据，不引任何运行时依赖：哪一份配哪个组件、每一格看什么，在
 * `catalogScenesPages2.ts`。
 */
import type { Integration, MailDraft } from '@/api'
import type { FeedbackComment, FeedbackDetail, FeedbackTimelineEntry } from '@/cx_types'
import type { FeatureView } from '@/views/admin/features/registry'

import { defineComponent, h, markRaw } from 'vue'

// ---- 功能数据页（AdminFeaturePageView）--------------------------------------

/** 功能页的「前端那一半」在注册表里是懒加载的（`defineAsyncComponent`），预览站不该
 *  为了看这一格去拉一个真的要取数的页面。这里给一个认得出来的替身，够证明「认出了
 *  这个 id，画的是它自己那一页」就行。 */
const StubFeaturePage = defineComponent({
  name: 'StubFeaturePage',
  setup: () => () => h('div', { class: 't-body' }, '这个功能自己那一页'),
})

export const FEATURE: FeatureView = {
  id: 'docs-assistant',
  titleKey: 'featureStats.features.docsAssistant.title',
  summaryKey: 'featureStats.features.docsAssistant.summary',
  // `markRaw`：这一份是当 prop 传的，Vue 会把 props 做成响应式，替身组件被代理一层
  // 就当场报一句「component made reactive」（注册表里那些是 `defineAsyncComponent`
  // 的返回，同样不该被代理）。
  view: markRaw(StubFeaturePage),
}

// ---- 我的连接（MyConnectionsViewView）--------------------------------------

/** 一条连接：`api/integrations.ts` 的 `Integration`。默认是一条好的邮箱连接。 */
export function integration(over: Partial<Integration> = {}): Integration {
  return {
    id: 'i-mail-1',
    provider: 'mail',
    label: 'course@example.com',
    owner_handle: 'wangchangxin',
    config: {},
    grants: [],
    status: 'ok',
    last_error: '',
    last_checked_at: '2026-10-12T09:00:00Z',
    user_authorized: true,
    shared_app: false,
    ...over,
  }
}

/** 两张卡上都有的两条：一条正常的邮箱、一条走平台应用还没授权的飞书。 */
export const INTEGRATIONS: Integration[] = [
  integration(),
  integration({
    id: 'i-feishu-1',
    provider: 'feishu',
    label: '课程小队的飞书',
    // 平台应用那一行：凭据是管理员的、账号是这个人的，授权回来之前它什么也做不了。
    shared_app: true,
    user_authorized: false,
  }),
]

/** 一封等着发出去的邮件：`api/integrations.ts` 的 `MailDraft`。 */
export function mailDraft(over: Partial<MailDraft> = {}): MailDraft {
  return {
    id: 'd-1',
    integration_id: 'i-mail-1',
    project_id: 'p-ml',
    topic_id: null,
    created_by: 'cheese',
    to: ['class@example.com'],
    cc: [],
    subject: '第一周的作业安排',
    body: '这周的实验交到平台上，周五之前。',
    attachments: [],
    status: 'drafted',
    error: '',
    sent_at: null,
    created_at: '2026-10-12T09:00:00Z',
    ...over,
  }
}

/** 待发送的那几封。 */
export const MAIL_DRAFTS: MailDraft[] = [
  mailDraft(),
  mailDraft({
    id: 'd-2',
    to: ['ta@example.com'],
    cc: ['wangchangxin@example.com'],
    subject: '实验环境的登录方式',
    body: '统一用学校邮箱登录，第一次进要改一次密码。',
    attachments: [{ path: 'docs/setup.pdf', name: 'setup.pdf', size: 20480 }],
  }),
]

/** 表格里那两张 `v-select` 的项目候选：和 `catalogScenesPagesFixtures.ts` 里小队项目
 *  同一份形状，这里另起两条名字好认，所以不复用 `TEAM_PROJECTS`。 */
export const CONNECTION_PROJECTS = [
  { id: 'p-ml', name: '机器学习入门', slug: 'ml', created_at: '2026-03-04T00:00:00Z' },
  { id: 'p-compiler', name: '编译器实验', slug: 'compiler', created_at: '2026-04-18T00:00:00Z' },
] satisfies Project[]

// ---- 反馈详情（FeedbackDetailPageView）-------------------------------------

/** 一条评论：`cx_types.ts` 的 `CommentOut`。只填详情页读得到的那几项。 */
export function comment(over: Partial<FeedbackComment> = {}): FeedbackComment {
  return {
    id: 'c-1',
    parent_id: null,
    author_handle: 'andy',
    author_is_agent: false,
    author_avatar_id: null,
    reply_to_handle: null,
    body: '我这边也遇到过，刷新一下就好了。',
    liked: false,
    likes: 2,
    can_delete: false,
    created_at: '2026-10-12T02:00:00Z',
    ...over,
  } as FeedbackComment
}

/** 一栋楼：一条顶层评论，下面挂着两条回复（第二条回的是第一条）。 */
export const FEEDBACK_THREAD: FeedbackComment[] = [
  comment(),
  comment({
    id: 'c-2',
    parent_id: 'c-1',
    author_handle: 'cheese',
    author_is_agent: true,
    body: '是同一个原因吗？如果是，我把它并到这条里。',
    likes: 0,
    created_at: '2026-10-12T02:10:00Z',
  }),
  comment({
    id: 'c-3',
    parent_id: 'c-1',
    author_handle: 'wangchangxin',
    author_avatar_id: 5,
    reply_to_handle: 'cheese',
    body: '应该不是，我这边是点了两下才出来的。',
    liked: true,
    likes: 1,
    can_delete: true,
    created_at: '2026-10-12T02:20:00Z',
  }),
]

/** 一条走过的进展：`cx_types.ts` 的 `TimelineOut`。 */
export const FEEDBACK_TIMELINE: FeedbackTimelineEntry[] = [
  { status: 'in_progress', by_handle: 'cheese', at: '2026-10-12T03:00:00Z', note: null },
  { status: 'received', by_handle: 'andy', at: '2026-10-11T22:00:00Z', note: null },
] as FeedbackTimelineEntry[]

/** 一条反馈的全文：`cx_types.ts` 的 `FeedbackDetail`。只填详情页会读到的那几项。 */
export function feedback(over: Partial<FeedbackDetail> = {}): FeedbackDetail {
  return {
    id: 'f-1',
    display_id: 'FB-42',
    kind: 'bug',
    title: '看板刷新之后列标题会闪一下',
    summary: '每次刷新看板，列标题先空一下再填上。',
    status: 'in_progress',
    priority: 'normal',
    visibility: 'public',
    security: false,
    author_handle: 'wangchangxin',
    author_is_agent: false,
    author_avatar_id: 5,
    submitted_by_handle: null,
    assignee_handle: 'cheese',
    tags: ['看板'],
    supports: 7,
    comments: 3,
    supported: false,
    last_activity_at: '2026-10-12T03:00:00Z',
    created_at: '2026-10-11T22:00:00Z',
    problem: '在看板上按一下刷新，四列的标题会先变成空的，过一会儿才填回来。',
    why: '找任务的时候会以为这一列真的没有东西。',
    expectation: '刷新时标题一直在，只有卡片区在换。',
    what_happened: '标题那一条整块消失，大约半秒之后才回来。',
    repro: '1. 打开一个小队的看板\n2. 按一下右上角的刷新\n3. 盯着列标题看',
    evidence: '录屏里能看到标题空了大约半秒。',
    logs: '',
    session_id: null,
    environment: 'Chrome 140 · macOS 15',
    topic_id: null,
    project_id: 'p-ml',
    timeline: FEEDBACK_TIMELINE,
    thread: FEEDBACK_THREAD,
    thread_next_cursor: null,
    notes: [],
    can_delete: true,
    can_claim: true,
    can_release: false,
    ...over,
  } as FeedbackDetail
}

/** 一条私密反馈：没有支持按钮，标签也换成中性的那一套。 */
export const PRIVATE_FEEDBACK = feedback({
  id: 'f-2',
  display_id: 'FB-43',
  title: '导出的表格里少了一列',
  summary: '导出的成员表里没有「加入时间」这一列。',
  status: 'received',
  visibility: 'private',
  author_handle: 'cheese',
  author_is_agent: true,
  author_avatar_id: null,
  assignee_handle: null,
  tags: [],
  supports: 0,
  comments: 0,
  problem: '导出的表格里没有「加入时间」这一列，但页面上是有的。',
  why: '',
  expectation: '',
  what_happened: '',
  repro: '',
  evidence: '',
  environment: '',
  timeline: [],
  thread: [],
  can_delete: false,
  can_claim: false,
})
