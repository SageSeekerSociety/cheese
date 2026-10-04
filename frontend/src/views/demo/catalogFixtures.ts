/**
 * 预览站喂给组件的数据。
 *
 * 全部是产品里真会出现的形状，没有「随便来一条」：
 *
 *   - 房间那几条（消息、通知、验收卡）从演示剧本算出来，和 `DemoRoom` 走同一条
 *     管线（`frameAt` + `collapseNotices`）—— 预览站上演的正是文档里的那些话；
 *   - 看板那几张卡抄自看板页真正的调用方（`AdminDashboardPage`），行的形状、
 *     口径句、时间戳的写法都一样；
 *   - 剩下几条是产品里会遇到、剧本里没演到的那几格（长正文、没送出去的消息、
 *     空列表、首次加载），照各组件的 props 造。
 *
 * 这里只出数据（外加两个把行翻成 props 的小函数）。哪一份配哪个组件、每个组件
 * 看哪几格，在 `catalog.ts`。
 */
import type { RouteLocationRaw } from 'vue-router'
import type { MenuCommand } from '@/commands'
import type { DocConnection, DocPeer, DocSession } from '@/composables/useDocCollab'
import type { OpenFileTab } from '@/composables/useTopicMemory'
import type {
  AcceptCard,
  Block,
  FeedbackCard,
  FeedbackStatus,
  FileContent,
  FileSource,
  GitCommit,
  PrChecks,
  ProjectMemberRow,
  RoomTask,
  Topic,
  WorkspaceFile,
} from '@/cx_types'
import type { Outgoing } from '@/lib/composerDrafts'
import type { FileDiff } from '@/lib/diff'
import type { DocThreadActions, DocThreadState } from '@/lib/docThreadTypes'
import type { RailMemberMark } from '@/lib/memberActivity'
import type { VisibleRow } from '@/lib/topicTree'
import type { SpaceLearningExcerpt } from '@/network/api/spaces/types'
import type { ChangesScene, ChatLine, Frame } from './demoScene'

import { ref } from 'vue'

import { answer } from './demoBackend'
import { DEMO_PROJECT, DEMO_TOPIC, diffOf, filesOf, roomTask } from './demoPanels'
import { frameAt } from './demoScene'
import { SCENES } from './scenes'

import { dayLabelsFor, runEdgeBetween } from '@/lib/chatGrouping'
import { parseDiffLines, splitDiffByFile } from '@/lib/diff'
import { localDocSession } from '@/lib/docLocalSession'
import { DOCUMENT_TYPES } from '@/lib/fileKind'
import { mergeBadgeOf, visibleReasons } from '@/lib/mergeState'
import { collapseNotices, type PlatformNotice } from '@/lib/platformNotice'

const SCENE = SCENES.quickstart

/** 剧本放到第 step 步末尾的那一帧：`elapsed` 给一个够大的数，这一步的事都演完了。 */
function frame(step: number): Frame {
  return frameAt(SCENE, step, Number.MAX_SAFE_INTEGER)
}

const NAMES: Record<string, string> = Object.fromEntries(
  Object.entries(SCENE.people).map(([handle, person]) => [handle, person.name])
)

/** 正文里的 token 靠它渲染成可点的 chip（和 `DemoRoom` 给的是同一份）。 */
export const ROOM_REFS = { mentionNames: NAMES, topicTitles: {} }

/** 房间当前那位 AI 队友的名字（剧本里第一个座位，`DemoRoom` 也是这么算的）。 */
export const AGENT_NAME = NAMES[SCENE.seats?.[0] ?? ''] ?? '芝士'

export interface RoomRow {
  line: ChatLine
  block: Block
  notice: PlatformNotice | null
  run: Block[]
}

/** 时间线上的行。和 `DemoRoom.rows` 同一条管线，只是不需要它那两种不是块的行
 *  （`mark` 分隔说明、`split` 已派出标记）——预览站没有整条时间线要摆。 */
function rows(step: number): RoomRow[] {
  const chat = frame(step).chat
  const byId = new Map(chat.filter((l) => l.block).map((l) => [l.block!.id, l]))
  return collapseNotices(chat.filter((l) => l.block).map((l) => l.block!)).map((row) => ({
    line: byId.get(row.block.id)!,
    block: row.block,
    notice: row.notice,
    run: row.run,
  }))
}

/** 第 step 步里某一位说的那几条消息。 */
function messagesOf(step: number, who: string): RoomRow[] {
  return rows(step).filter((r) => !r.notice && r.block.kind === 'message' && r.block.author === who)
}

/** 第 step 步里的通知（折过之后还是通知的那些行）。 */
function noticesOf(step: number): RoomRow[] {
  return rows(step).filter((r) => r.notice)
}

/** 某一步里最后出现的那一条：`frameAt` 是从第一步重放到这一步的，所以第 N 步的
 *  「那一条」总是它的最后一条，不是整个房间的第一条。 */
function latest<T>(list: T[]): T {
  return list[list.length - 1]
}

/** 王长鑫的两句话：一句只留在话题里的留言，一句点了名交给芝士。 */
export const WANG_LINES = messagesOf(0, 'wang')
/** 芝士复述理解的那一句，和它随后写下的步骤清单。 */
export const CHEESE_LINES = messagesOf(1, 'cheese')
/** 房间收尾那一句。 */
export const WANG_CLOSING = latest(messagesOf(5, 'wang'))
/** 这一轮结束时折出来的那几条：本轮摘要、待审阅、已采纳。 */
export const TURN_SUMMARY = latest(noticesOf(3))
export const CARD_FILED = latest(noticesOf(4))
export const ACCEPT_DONE = latest(noticesOf(5))
/** 验收卡那一格：卡和它那个 PR 的检查（剧本第四步出现）。 */
export const ACCEPT_CARD = frame(4).card
export const ACCEPT_CHECKS = frame(4).checks

/**
 * `cheese_ask` 的一条真问题 —— 产品**现状**那一版，不是这次要做的新版。
 *
 * 画它的是 `RoomMessage.vue` 本身，所以这两格就是「现在长什么样」的对照图：
 * `meta.options` 躺在那儿，房间画出一排一键按钮；有人答过之后 `meta.answer_log`
 * 的末条落在同一条上，房间画成「谁选了什么」的回执。
 *
 * 字段形状照后端写：`meta.asked` 是**在等谁答**那个 handle 或 null
 * （`topics_messages.py` 建问题那处写 `{"options": …, "asked": …}`），不是布尔。
 * 答完也**不清它** —— `answer_options` 只往 `answer_log` 追加，末条是生效的那一版。
 */
const ASK_TEXT = '这次的作业按哪种方式收？'
const ASK_OPTIONS = [{ text: '课程平台收文件' }, { text: '发到课程邮箱' }, { text: '课上交纸质' }]
/** 这道题在等谁答：发起那一轮的人，谁答谁就是他。 */
const ASK_WAITING_FOR = 'wang'

function askRow(answered: { option: string; by: string } | null): RoomRow {
  const meta: Block['meta'] = { asked: ASK_WAITING_FOR, options: ASK_OPTIONS }
  if (answered) {
    meta.answer_log = [
      {
        v: 1,
        kind: 'option',
        option: answered.option,
        note: null,
        by: answered.by,
        at: '2026-09-30T10:24:00Z',
        client_op_id: 'demo',
      },
    ]
  }
  const block: Block = {
    id: answered ? 'ask-demo-answered' : 'ask-demo-open',
    topic_id: 'demo',
    kind: 'message',
    author_type: 'participant',
    author: 'cheese',
    content: ASK_TEXT,
    created_at: '2026-09-30T10:24:00Z',
    meta,
  }
  return {
    line: { kind: 'message', id: block.id, author: block.author, text: ASK_TEXT, time: '10:24', block },
    block,
    notice: null,
    run: [block],
  }
}

/** 还没答：一排一键选项，点一下就交上去。 */
export const ASK_OPEN = askRow(null)
/** 已经有人答了：整排收成一句回执「某某 选了「什么」」。 */
export const ASK_ANSWERED = askRow({ option: ASK_OPTIONS[0].text, by: 'wang' })

/**
 * 预览站里会自己取数的那几件（验收卡读卡和读检查）该读到什么。装到演示后端上
 * （`demoBackend`），和 `DemoRoom.installPanelAnswers` 是同一件事。
 */
export function installCatalogAnswers(): void {
  answer('/topics/demo/accept-card', () => ({ data: ACCEPT_CARD ? [ACCEPT_CARD] : [], total: ACCEPT_CARD ? 1 : 0 }))
  answer('/topics/demo/pr-checks', () => ACCEPT_CHECKS ?? { available: false })
}

// ---- 验收卡（TopicAcceptCard）拆出来的那几件 ----------------------------------
//
// 拆开之后 `components/accept/` 里这几件都是「只吃 props、只往上发事件」的那种
// （`frontend_grade.py` 的 A 级）：三件只管画的零件（AcceptPrChecks / AcceptNoteLine
// / AcceptDockBar）和五张脸（闸门未过 / 闸门没跑成 / 待采纳 / 已采纳等合并 / 已采
// 纳）。于是每一件都能单独摆进预览站，条目在 `catalogAccept.ts`。数据就着剧本第四
// 步那张卡改几格（`ACCEPT_CARD` / `ACCEPT_CHECKS`）——它们本来就是同一张卡上的几段。

/** 那张卡（`AcceptPrChecks` / 几张脸都要）。剧本第四步一定有它（`installCatalogAnswers`
 *  也是照着它答的），所以这里就是那一张。 */
export const ACCEPT_ONE = ACCEPT_CARD as AcceptCard

/** 一份 PR 检查。起点是剧本里那份（`pr_number` / `state` / `mergeable` 都在），只换
 *  这一格要看的 `checks` 那一列：`status` / `conclusion` 就是 GitHub 那两个字段，
 *  `AcceptPrChecks` 照它们挑图标、写「进行中」。 */
export function acceptChecks(checks: NonNullable<PrChecks['checks']>, over: Partial<PrChecks> = {}): PrChecks {
  return { ...(ACCEPT_CHECKS ?? { available: true }), checks, ...over }
}

/** 卡上那条 note（`AcceptNoteLine`）的两种口气。句子是后端写的原话
 *  （`backend/app/domain/review/services.py`），前端只照 `note_level` 挑颜色和图标，
 *  所以这里抄的是那两句本身，不是一个「差不多」的文案。 */
export const ACCEPT_NOTE_ERROR = '这个项目的代码托管凭据不可用'
export const ACCEPT_NOTE_INFO = 'PR #1 有新提交，已有的采纳批准被作废'

/** 闸门未过（`gate_failed`）的历史卡：检查跑了、代码红了。输出抄的是检查命令真会
 *  写的那一行 —— `TopicAcceptCardGate.test.ts` 里也是这一行。 */
export const ACCEPT_GATE_FAILED: AcceptCard = {
  ...ACCEPT_ONE,
  status: 'gate_failed',
  gate_output: 'ruff: E501 line too long',
}
/** 闸门没跑成（`gate_blocked`）：检查本身没起来，对代码没有结论。这是「要人看一眼」
 *  的状态，和上面那张刻意分开。 */
export const ACCEPT_GATE_BLOCKED: AcceptCard = {
  ...ACCEPT_ONE,
  status: 'gate_blocked',
  gate_output: 'docker: not found',
}
/** 已采纳等合并（`pr_open`，#718 退役的兜底脸）：采纳过、合并没走完，只读。 */
export const ACCEPT_DELIVERING_ONE: AcceptCard = { ...ACCEPT_ONE, status: 'pr_open', decided_by: 'wang' }
/** 归档话题上那张已采纳的卡：它存在的理由就是「撤回采纳」那个入口。 */
export const ACCEPT_ACCEPTED_ONE: AcceptCard = { ...ACCEPT_ONE, status: 'accepted', decided_by: 'wang' }
/** 主分支保护：这次改动要两个人批准，李甘已经批了。`approvals` 记的是 handle
 *  （剧本里 `li` 就是李甘）。 */
export const ACCEPT_APPROVALS: AcceptCard = { ...ACCEPT_ONE, approvals: ['li'], approvals_required: 2 }
/** 检查还没绿：后端此刻会拒掉采纳，所以按钮灰着、`acceptBlockedTitle` 有话要说，
 *  旁边那颗「检查通过后自动合并」也只在 `blocked` / `behind` 这一档才出现。
 *  依据抄的是验收剧本里那条（`kind: ci_running` 那一格）。 */
export const ACCEPT_BLOCKED: AcceptCard = {
  ...ACCEPT_ONE,
  merge_state: {
    ...ACCEPT_ONE.merge_state,
    state: 'blocked',
    who: 'ci',
    reasons: [{ kind: 'ci_running', checks: ['CI required'], detail: '检查进行中' }],
  },
  auto_merge: { allowed: true, armed_by: null, armed_at: null },
}
/** 与主分支冲突（`conflict`）：芝士正在处理，这一支连状态词那一行都不画。 */
export const ACCEPT_CONFLICTED: AcceptCard = { ...ACCEPT_ONE, status: 'conflict', note: '' }

/** 「改由谁审阅」那一份名单：名册上还在岗的人（`useAcceptCard.ts` 就是拿 store 里的
 *  `members` 直接给菜单的）。 */
export const ACCEPT_REVIEWERS: ProjectMemberRow[] = [
  { user_handle: 'wang', name: '王长鑫', role: 'owner', source: 'owner' },
  { user_handle: 'li', name: '李甘', role: 'member', source: 'team' },
]

/**
 * `AcceptPendingFace` 要吃的那一大把 props。默认是「可以采纳」那一档（剧本第四步
 * 那张卡），要看别的档就换一两个键就行。
 */
export function acceptPendingProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  const card = (over.card as AcceptCard | undefined) ?? ACCEPT_ONE
  return {
    card,
    // 冲突卡不画状态词（标题已经说了「芝士处理中」），别的卡按合并态翻译。
    badge: card.status === 'conflict' ? null : mergeBadgeOf(card.merge_state, AGENT_NAME),
    reasons: visibleReasons(card.merge_state),
    forgeDeclaration: card.forge.declaration,
    note: null,
    reviewerChoices: ACCEPT_REVIEWERS,
    busy: false,
    blockedTitle: null,
    needsPr: false,
    autoMergeVisible: false,
    autoMergeArmedBy: null,
    prChecks: ACCEPT_CHECKS,
    docked: false,
    myHandle: 'wang',
    agentName: AGENT_NAME,
    agentHandle: 'cheese',
    deliverableBusy: false,
    deliverableError: '',
    // 六个 `defineModel` 在这张脸上都是 required（它们的初值属于调用方的状态，
    // 重新读卡要能收起来）。预览站给的就是「三个小表单都收着」。
    showRejectInput: false,
    rejectNote: '',
    showVoidInput: false,
    voidNote: '',
    showForceMergeInput: false,
    forceMergeReason: '',
    ...over,
  }
}

/** `RoomMessage` 要吃的那一大把 props，和 `DemoRoom` 传给它的逐字一样。 */
export function roomMessageProps(row: RoomRow, over: Record<string, unknown> = {}): Record<string, unknown> {
  const who = row.block.author
  return {
    block: row.block,
    parent: null,
    parentName: null,
    runStart: true,
    mine: false,
    topicId: null,
    authorName: NAMES[who] ?? who,
    avatar: null,
    isAgent: SCENE.people[who]?.agent === true,
    time: row.line.time,
    refs: ROOM_REFS,
    viewer: '',
    askBusy: false,
    ...over,
  }
}

/** `RoomNotice` 要吃的那一大把 props（同上）。这一行必须真的是一条通知。 */
export function roomNoticeProps(row: RoomRow, over: Record<string, unknown> = {}): Record<string, unknown> {
  const notice = row.notice
  if (!notice) throw new Error('这一行没有通知，roomNoticeProps 用错了')
  return {
    block: row.block,
    notice,
    run: row.run,
    agent:
      row.block.author === 'system' || !NAMES[row.block.author]
        ? null
        : { name: NAMES[row.block.author]!, handle: row.block.author },
    time: row.line.time,
    agentName: AGENT_NAME,
    refs: ROOM_REFS,
    ...over,
  }
}

/** 一整段交代写进一条消息里：产品里真有人这么发（也是长正文那一格要看的）。 */
const LONG_TEXT = `我把这周的进度理了一遍，发在这里，谁有空看一眼：

1. 课件前三讲的排版都过了一遍，习题答案还差第三讲；
2. 作业提交的口子换了新的表单，旧的那个还留着，等这周过了再关；
3. 上周说的那个「交上来文件名是乱的」问题，是因为两次上传同名文件，已经改成加时间戳了。

下周打算先把第四讲的课件排出来，再把作业那套流程整个跑一遍，有问题在下面回我。`

const LONG_BLOCK: Block = {
  id: 'catalog-long',
  topic_id: 'demo',
  kind: 'message',
  author_type: 'participant',
  author: 'wang',
  content: LONG_TEXT,
  created_at: new Date('2026-09-28T14:20:00+08:00').toISOString(),
}

/** 一条长消息的行（不是剧本里的，其余几条都是）。 */
export const LONG_ROW: RoomRow = {
  line: { kind: 'message', id: LONG_BLOCK.id, author: 'wang', text: LONG_TEXT, time: '14:20' },
  block: LONG_BLOCK,
  notice: null,
  run: [LONG_BLOCK],
}

// ---- 看板那几张卡 ----------------------------------------------------------
// 抄自看板页真正的调用方：一个数字串（格式化好的）、一个去处、一行环比。

export const ADMIN_QUEUE: RouteLocationRaw = { path: '/admin/feedback' }

export const KPI_STATES = {
  linked: {
    label: '待分诊',
    value: '12,048',
    unit: '条',
    to: ADMIN_QUEUE,
    delta: '+12.3%',
    deltaTitle: '上一周期（再前 7 天）：10,727',
    spark: [3, 5, 4, 8, 6, null, 9],
  },
  plain: {
    label: '本周新增反馈',
    value: '316',
    unit: '条',
    note: '按反馈创建时间算，上一周期是 281 条。',
    spark: [12, 19, 14, 22, 31, 28, 26],
  },
  empty: { label: '使用中的设备', value: '', unit: '台', note: '读不到时不画 0：0 是「确实是零」。' },
}

export const BAR_ROWS = [
  { id: 'spaces', label: '空间协作', value: 412 },
  { id: 'questions', label: '题库与判题', value: 268 },
  { id: 'docs', label: '文档站', value: 155 },
  { id: 'infra', label: '平台自身', value: 93 },
]

export const BAR_ROWS_LONG = [
  { id: 'very-long', label: '一个名字长到会走省略号的项目（空间协作与题库与判题与文档站）', value: 412 },
  ...BAR_ROWS.slice(1),
]

export const NUMBER_ROWS: { id: string; no: number; title: string; status: FeedbackStatus; updatedAt: string }[] = [
  {
    id: 'fb-1024',
    no: 1024,
    title: '导出一个月的数据要等四十秒',
    status: 'received',
    updatedAt: '2026-09-28T09:12:00Z',
  },
  {
    id: 'fb-1019',
    no: 1019,
    title: '判题结果偶尔少一个测试点',
    status: 'in_progress',
    updatedAt: '2026-09-27T18:40:00Z',
  },
  { id: 'fb-1011', no: 1011, title: '手机上上传课件会掉一半', status: 'resolved', updatedAt: '2026-09-26T11:05:00Z' },
]

export const ACTION_ROWS = [
  {
    id: 'task:2100',
    title: '合并 #2100（前端边界闸）',
    subtitle: '任务 · 施工中',
    tone: 'warn' as const,
    statusLabel: '待评审',
    age: '3 小时前',
    to: { path: '/topics/t-1' } as RouteLocationRaw,
  },
  {
    id: 'task:2098',
    title: '机器 cm-2b81 连不上',
    subtitle: '设备',
    tone: 'danger' as const,
    statusLabel: '已离线',
    age: '1 天前',
  },
  {
    id: 'topic:77',
    title: '第 3 题：为什么天空是蓝的',
    subtitle: '话题',
    statusLabel: '等你验收',
    age: '2 小时前',
    to: { path: '/topics/t-77' } as RouteLocationRaw,
  },
]

// ---- 底部动作面板、底栏、看板页签、学习摘录 --------------------------------

export const SHEET_ACTIONS = [
  { key: 'rename', label: '重命名', icon: 'mdi-pencil', to: { path: '/topics/t-1/rename' } as RouteLocationRaw },
  { key: 'mute', label: '静音 1 天', icon: 'mdi-bell-off-outline', badge: '3' },
  { key: 'pin', label: '置顶', icon: 'mdi-pin-outline', loading: true },
  { key: 'archive', label: '归档', icon: 'mdi-archive-outline', disabled: true },
  { key: 'delete', label: '删除话题', icon: 'mdi-trash-can-outline', danger: true },
]

export const NAV_ITEMS = [
  { key: 'spaces', type: 'item' as const, title: '空间', icon: 'mdi-view-grid-outline', to: '/spaces' },
  { key: 'inbox', type: 'item' as const, title: '待办', icon: 'mdi-inbox-outline', to: '/inbox', badge: 3 },
  { key: 'me', type: 'item' as const, title: '我的', icon: 'mdi-account-outline', to: '/me' },
]

export const OPEN_FILES: OpenFileTab[] = [
  { path: 'README.md', pinned: false },
  { path: '.cheese/notes/plan.md', pinned: true },
]

// ---- 反馈列表里的一行 ------------------------------------------------------
// 一行吃到的就是后端 `schemas.FeedbackCard` 本身（页面不再转第二种形状），所以这里
// 照那个形状造，只有 `created_at` 是「昨天」，好让底行那句相对时间读起来正常。

/** 一行反馈。默认这一条是公开、刚收录、还没人支持的那一种；其余几格只改差的那几项。 */
function feedbackRow(over: Partial<FeedbackCard> = {}): FeedbackCard {
  return {
    id: 'fb-1024',
    display_id: 'FB-1024',
    kind: 'bug',
    title: '导出一个月的数据要等四十秒',
    summary: '每次导出都要重跑一遍全量聚合，数据一多就卡在那儿转。',
    status: 'received',
    priority: 'normal',
    visibility: 'public',
    security: false,
    author_handle: 'alice',
    author_is_agent: false,
    author_avatar_id: null,
    submitted_by_handle: null,
    assignee_handle: null,
    tags: [],
    supports: 0,
    comments: 0,
    supported: false,
    last_activity_at: null,
    created_at: '2026-09-28T09:12:00Z',
    ...over,
  }
}

export const FEEDBACK_ROWS: Record<string, FeedbackCard> = {
  plain: feedbackRow(),
  /** 支持过了：图标实心、数字变色、底色起来（三个信号一起变，不只换颜色）。 */
  supported: feedbackRow({ supported: true, supports: 12, comments: 4, tags: ['导出', '性能'] }),
  /** 摘要和标题是同一句话：这一行不画摘要（组件文件头那段）。 */
  sameLine: feedbackRow({ summary: '  导出一个月的数据要等四十秒  ' }),
  /** 长标题 + 长摘要：标题一行就截，摘要两行封顶。 */
  long: feedbackRow({
    title: '导出一个月的数据要等四十秒，而且导出到一半切到别的页面就全没了',
    summary:
      '每次导出都要重跑一遍全量聚合，数据一多就卡在那儿转；退出去再回来得从头开始，中间那一半文件也没有落下来。试过换浏览器，一样。',
  }),
  /** 不能公开的条目：没有支持按钮 —— 它不该让人知道它存在（行政标的也一样）。 */
  private: feedbackRow({ visibility: 'private', title: '后台有个接口会把手机号回显出来' }),
  /** 芝士提的：来源那一颗写「AI 队友」，提交人是别人。 */
  agent: feedbackRow({
    kind: 'suggestion',
    author_handle: 'cheese',
    author_is_agent: true,
    submitted_by_handle: 'alice',
    title: '反馈列表里那一行可以再挤进一条标签',
  }),
  /** 办完了（已上线）：支持按钮变灰不可点，提示语换成「这条已经处理完了」。 */
  closed: feedbackRow({ status: 'deployed', supported: true, supports: 12, comments: 4 }),
}

export const EXCERPTS: SpaceLearningExcerpt[] = [
  {
    blockId: 'b-1',
    topicId: 't-1',
    projectId: 'p1',
    student: 'alice',
    studentName: '爱丽丝',
    topicTitle: '第 3 题：为什么天空是蓝的',
    createdAt: Date.parse('2026-09-28T09:12:00Z'),
    quote: '因为瑞利散射，短波长的光被散射得更多。',
    knowledgePoint: '瑞利散射',
  },
  {
    blockId: 'b-2',
    topicId: 't-9',
    projectId: 'p1',
    student: 'bob',
    studentName: '鲍勃',
    topicTitle: '第 5 题：这道题我错在哪',
    createdAt: Date.parse('2026-09-28T10:31:00Z'),
    quote:
      '我把第三问当成了求最大值，其实题目问的是「至少需要多少」——重新读了一遍才发现是下界，不是极值，所以整段推导都用错了方向，后面几问跟着一起错。',
    knowledgePoint: null,
  },
]

// ---- 项目侧栏：一行话题、顶上的置顶入口、项目头、组头、已归档 ----------------
//
// 侧栏拆成几个只管画的组件之后，这几件都能单独立着。它们吃的数据不少（一行的
// `VisibleRow` 有十几个字段），所以这里造的是**产品里真会出现的那几格**：在跑的、
// 等你拍板的、收起来的、出了故障的、在等合并的——而不是「随便来一条」。

/** 一行话题的完整形状（`lib/topicTree.ts` 的 `VisibleRow`）：话题 + 缩进 + 折叠
 *  开关要的那几个数（收起来了几个、里面有几条未读、里面有没有动静）。 */
function railRow(
  topic: Partial<Topic> & { id: string; title: string },
  visible: Partial<VisibleRow<Topic>> = {}
): VisibleRow<Topic> {
  return {
    topic: {
      project_id: 'p1',
      parent_id: null,
      kind: 'topic',
      status: 'active',
      created_at: '2026-09-24T09:00:00Z',
      ...topic,
    },
    depth: 0,
    hasChildren: false,
    collapsed: false,
    hiddenCount: 0,
    hiddenUnread: 0,
    unreadTotal: 0,
    hiddenWorking: false,
    hiddenAwaits: false,
    hiddenStalled: false,
    ...visible,
  }
}

export const RAIL_ROWS = {
  /** 一位队友正在这个话题里干活：右边它的小头像带一颗绿点。 */
  working: railRow({
    id: 't-1',
    title: '写第 4 章的教案',
    activity: [{ member: 'cheese-a1', kind: 'working', since: 1790845200 }],
  }),
  /** 有事等你拍板：琥珀点（未读的 @ 不点这颗灯，所以未读和它是两回事）。 */
  awaits: railRow({ id: 't-2', title: '决定这学期用哪本教材', awaits_me: true, i_participate: true }),
  /** 收起来的父话题：开关自己带聚合色（里面有话题在等人），右边是聚上来的未读。 */
  collapsed: railRow(
    { id: 't-3', title: '期末复习' },
    { hasChildren: true, collapsed: true, hiddenCount: 12, hiddenUnread: 4, unreadTotal: 4, hiddenAwaits: true }
  ),
  /** 子话题：缩进一级，左边一条竖向引导线。 */
  sub: railRow({ id: 't-4', title: '第 3 题：为什么天空是蓝的', parent_id: 't-3' }, { depth: 1, unreadTotal: 2 }),
  /** 房间在等的一位队友：它最近一轮报错了。不靠数据变——钟走到哪儿它都卡着。 */
  stalled: railRow({
    id: 't-5',
    title: '把成绩单导出成 CSV',
    waits: [{ member: 'cheese-a1', reason: 'failed', since: '2026-09-29T08:41:00Z' }],
  }),
  /** 归档行：标题压暗一档，行尾是「取消归档」（`TopicRailArchivedGroup` 那一组）。 */
  archived: { id: 't-7', title: '第 1 题：写一段自我介绍', kind: 'topic' } as Topic,
}

/** 侧栏一行右边那几位成员（`useTopicRail.memberMarks` 在真环境里给的就是这个形状）。 */
export const RAIL_MARKS = {
  working: [
    { handle: 'cheese-a1', name: AGENT_NAME, agent: true, state: 'working', title: `${AGENT_NAME}正在这里工作` },
  ],
  stalled: [
    { handle: 'cheese-a1', name: AGENT_NAME, agent: true, state: 'stalled', title: `${AGENT_NAME}最近一轮报错了` },
  ],
} satisfies Record<string, RailMemberMark[]>

/** 一行的 ⋯ 里那几项（`commands/topicActions.ts` 在真环境里给的就是这个形状）。 */
export const RAIL_ACTIONS: MenuCommand[] = [
  { id: 'topic.rename', title: '重命名', icon: 'mdi-pencil', run: () => {} },
  { id: 'topic.copyLink', title: '复制链接', icon: 'mdi-link-variant', run: () => {} },
  { id: 'topic.archive', title: '归档', icon: 'mdi-archive-outline', run: () => {} },
]

/** 项目本体（全局房间）：置顶那一行，也是项目名的落点。 */
export const RAIL_ROOT_TOPIC: Topic = {
  id: 't-root',
  project_id: 'p1',
  parent_id: null,
  title: '课程项目 · 项目总览',
  kind: 'root',
  status: 'active',
  created_at: '2026-09-20T08:00:00Z',
}

/** 这个项目的壳摆出来的那几页（顺序就是壳说的顺序，见 `lib/shell.ts`）。 */
export const RAIL_PAGES = [
  { key: 'project-library', label: 'navigation.project.library', icon: 'mdi-folder-outline' },
  { key: 'project-members', label: 'navigation.project.members', icon: 'mdi-account-group-outline' },
  { key: 'project-routines', label: 'navigation.project.routines', icon: 'mdi-timer-cog-outline' },
]

/** 壳换了词之后的项目词汇表（「{project}文档」靠它渲染）。 */
export const RAIL_TERMS = { project: '项目', topic: '话题' }
// ---- 工作面板那三格（#2143 拆出来的 View） ----------------------------------
// 这三件是「props 进、事件出」的纯渲染组件（取数在 `composables/usePanel*` 里），
// 所以下面造的全是数据 —— 挂起来不需要后端，也不需要登录。改动那一格的原料从剧本
// 里那几份真东西出发（`demoPanels` 的 `diffOf` / `filesOf` / `roomTask`），只有剧本
// 没演到的那几处（保存冲突、空、读不到）才补一小截。

/** 改动那一格看的这一支活：一份新文件、一份改过的、一份删掉的。 */
const CHANGES_SCENE: ChangesScene = {
  files: [
    {
      path: 'README.md',
      status: 'added',
      diff: ['+# 课程资料', '+', '+这个项目放本课程的课件和作业。', '+', '+有不清楚的地方，在话题里问。'],
    },
    {
      path: 'docs/week-1.md',
      status: 'modified',
      diff: [
        ' 第一周的课件放在这里。',
        '-习题答案：第 3、5、7 题',
        '+习题答案：第 3、5、7、9 题',
        '+',
        '+最后一题的提示写在下面。',
      ],
    },
    {
      path: 'docs/old-plan.md',
      status: 'removed',
      diff: ['-# 旧的大纲', '-', '-这一版已经不用了。'],
    },
  ],
}

const CHANGES_FILE_DIFFS = splitDiffByFile(diffOf(CHANGES_SCENE))
/** 打开的那一份（新文件，字都在正文里）。 */
const CHANGES_OPEN = CHANGES_FILE_DIFFS[0]
const CHANGES_BY_PATH = new Map(CHANGES_FILE_DIFFS.map((d) => [d.path, d]))
const CHANGES_TREE: WorkspaceFile[] = filesOf(CHANGES_SCENE)

/** 改动那一支活自己（面板顶上那条「来源」读的就是它）。 */
const CHANGES_TASK: RoomTask = roomTask(
  { id: 'demo-task-1', title: '整理第一周的课件', column: 'needs_you', phrase: 'awaiting_review' },
  0
)

/** 没打开文件时那一半画的是提交记录。 */
const CHANGES_COMMITS: GitCommit[] = [
  { hash: '1f2a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c', author: '芝士', message: '整理第一周的课件' },
  { hash: '4c3b2a1908f7e6d5c4b3a2918070605040302010', author: '芝士', message: '删掉旧的大纲' },
]

const CHANGES_BASE = {
  topicId: DEMO_TOPIC,
  readOnly: false,
  overview: false,
  taskOptions: [CHANGES_TASK],
  taskLoadError: null,
  tasksLoaded: true,
  selectedTask: CHANGES_TASK.id,
  currentTask: CHANGES_TASK,
  sourceTitle: '整理第一周的课件',
  sourceStatus: '等你验收',
  sourceUnavailable: false,
  requestedPath: null,
  overviewDiffs: {},
  overviewErrors: {},
  expandedTasks: new Set<string>(),
  showAll: false,
  fileSource: 'committed' as FileSource,
  fileToolReady: true,
  loading: false,
  refreshing: false,
  errorMsg: null,
  noRepo: false,
  missing: null,
  gitCommits: CHANGES_COMMITS,
  fileDiffs: CHANGES_FILE_DIFFS,
  diffByPath: CHANGES_BY_PATH,
  treeFiles: CHANGES_TREE,
  openPath: CHANGES_OPEN.path,
  fileDraft: '# 课程资料\n\n这个项目放本课程的课件和作业。',
  fileSaving: false,
  fileDirty: false,
  fileVersion: 'cd1f2a3',
  fileBinary: false,
  fileTooLarge: false,
  fileBytes: 168,
  fileReadOnly: false,
  fileConflict: false,
  openDiff: CHANGES_OPEN,
  openDiffLines: parseDiffLines(CHANGES_OPEN.body),
  effectiveView: 'diff' as const,
  fileView: 'diff' as const,
  openIsImage: false,
  openIsDocument: false,
  openDocumentType: null,
  revisionPath: null,
  openRawUrl: '/api/projects/demo/file/raw?path=README.md',
  expandedDirs: new Set<string>(['docs']),
  revealTick: 0,
  draftCount: 0,
  docBytes: null,
  docLoading: false,
  docError: '',
  docRendererMissing: false,
}

/** 改动那一格的整串 props（六十来样 —— 这就是它的全部环境）。 */
export function changesPanelProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  return { ...CHANGES_BASE, ...over }
}

/** 空的那一格：这一支活什么都没改，提交记录也没有。 */
export const CHANGES_EMPTY = changesPanelProps({
  fileDiffs: [],
  diffByPath: new Map<string, FileDiff>(),
  treeFiles: [],
  gitCommits: [],
  openPath: null,
  openDiff: null,
  openDiffLines: [],
  fileToolReady: false,
})

/** 预览那一格看的这一份：一篇 markdown，正文直接画出来。 */
const PREVIEW_FILE: FileContent = {
  path: 'docs/week-1.md',
  content: '# 第一周\n\n课件和作业都在这里。\n\n- 课件：前三讲已经排好\n- 作业：第 3、5、7、9 题\n',
  version: '9f8e7d6',
  bytes: 132,
  binary: false,
  too_large: false,
  source: 'live',
}

const PREVIEW_BASE = {
  topicId: DEMO_TOPIC,
  projectId: DEMO_PROJECT,
  frameName: 'cheese-preview-demo',
  loading: false,
  refreshing: false,
  previewFile: PREVIEW_FILE,
  previewMime: 'text/markdown',
  previewNamed: true,
  previewUrl: null,
  previewAppNote: '',
  previewTunnelUp: true,
  previewNamedPath: PREVIEW_FILE.path,
  previewError: null,
  previewReadError: null,
  documentSuffix: 'md',
  documentType: DOCUMENT_TYPES.md,
  documentName: PREVIEW_FILE.path,
  isImageArtifact: false,
  downloadError: '',
  docBytes: null,
  docLoading: false,
  docError: '',
  docRendererMissing: false,
}

/** 预览那一格的整串 props。 */
export function previewPanelProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  return { ...PREVIEW_BASE, ...over }
}

/** 没有东西可看的那一格（房间里还没摆出过任何东西）。 */
export const PREVIEW_EMPTY = previewPanelProps({
  previewFile: null,
  documentType: null,
  documentName: '',
  previewMime: '',
  previewNamed: false,
  previewNamedPath: '',
})

/** 文档那一格看的这一篇。 */
export const DOC_TOPIC: Topic = {
  id: DEMO_TOPIC,
  project_id: DEMO_PROJECT,
  parent_id: null,
  title: '课程资料',
  kind: 'topic',
  status: 'open',
  created_at: '2026-09-29T09:00:00Z',
}

/** 这一格接住的动作：真产品里它们落回 `usePanelDoc` 的 ref，预览站里什么都不做。 */
const noop = () => {}
const noopAsync = async () => {}

const DOC_BASE = {
  topic: DOC_TOPIC,
  activityTick: 0,
  agentName: AGENT_NAME,
  topicList: [DOC_TOPIC],
  session: null as DocSession | null,
  editable: true,
  readOnly: false,
  loading: false,
  connection: 'connected' as DocConnection,
  peers: [] as DocPeer[],
  errorMsg: null,
  threadState: { threads: [], activity: {}, errors: {}, busy: false, unknown: null } as DocThreadState,
  threadActions: {
    reply: noopAsync,
    resolve: noopAsync,
    reopen: noopAsync,
    recover: async () => undefined,
    stopAgent: noopAsync,
  } as DocThreadActions,
  fetchDocNodes: async () => [],
  imageSrc: (src: string) => src,
  toggleEditable: noop,
  setError: noop,
}

/** 一篇只活在这一页里的协同文档：预览站上正文是它，不连任何服务。 */
export function docSession(markdown: string): DocSession {
  return localDocSession(markdown, DEMO_TOPIC)
}

/** 文档那一格的整串 props。 */
export function docPanelProps(): typeof DOC_BASE
export function docPanelProps<T extends Record<string, unknown>>(over: T): Omit<typeof DOC_BASE, keyof T> & T
export function docPanelProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  return { ...DOC_BASE, ...over }
}

// ---- 对话栏拆出来的那几件（ChatPanel 那一组，见 `catalogChat.ts`）--------------

/** 对话栏那一格的主题：项目本体之外的普通话题。 */
export const CHAT_TOPIC: Topic = {
  id: DEMO_TOPIC,
  project_id: DEMO_PROJECT,
  parent_id: null,
  title: '把预览站补上对话栏拆出来的那几件',
  kind: 'topic',
  status: 'open',
  created_at: '2026-09-29T09:00:00Z',
}

/** 时间线那一格摆的几行：剧本第 0 步到第 1 步的整段对话，和 `DemoRoom` 同一份块
 *  —— 预览站上演的还是文档里的那些话，不是临时编的。 */
export const CHAT_ROWS: RoomRow[] = [...rows(0), ...rows(1)]

const CHAT_BASE = {
  topic: CHAT_TOPIC,
  unreadAnchorId: null,
  splitMarkers: { before: new Map(), tail: [] },
  arrived: new Set<string>(),
  older: new Set<string>(),
  delivered: new Set<string>(),
  sentNow: new Set<string>(),
  flashId: null,
  timeShownId: null,
  bar: { id: null, shown: false, top: 0, jump: false },
  barBlock: null,
  barEditable: false,
  reactionPickerFor: null,
  loadingHistory: false,
  hasMore: false,
  loadingOlder: false,
  retryIndex: -1,
  retryBusy: false,
  working: false,
  showStarters: false,
  starterPrompts: [] as { label: string; text: string }[],
  agentSeat: undefined as { handle?: string } | undefined,
  agentName: AGENT_NAME,
  refs: ROOM_REFS,
  outbox: [] as Outgoing[],
  typing: [],
  editingId: null,
  editSaving: false,
  askBusy: null,
  scrollRef: ref<HTMLElement | null>(null),
  contentRef: ref<HTMLElement | null>(null),
  // 下面这几件在生产里由 `useChatPanel` 回答（谁在说、这条是不是我说的、时间怎么
  // 写）。预览站按同一口径给一个最小实现，够这一格画对就行。
  isAgentBlock: (b: Block) => SCENE.people[b.author]?.agent === true,
  isMine: () => false,
  isExternal: () => false,
  avatarSrc: () => null as string | null,
  displayName: (b: Block) => NAMES[b.author] ?? b.author,
  noticeAgent: (b: Block) => (NAMES[b.author] ? { name: NAMES[b.author]!, handle: b.author } : null),
  parentOf: () => undefined,
  showReplyCue: () => false,
  fmtTime: (iso: string) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
  pendingBlock: (item: Outgoing) =>
    ({ id: item.clientId, author: '', content: item.content, kind: 'message' }) as Block,
  outgoingState: () => '',
  outboxEdge: (index: number) => (index > 0 ? 'cont' : 'start'),
  myName: NAMES.wang ?? '王长鑫',
  viewer: 'wang',
}

/**
 * `ChatTimeline` 要吃的那一大把 props。日期线和每一行的分组关系不写死：拿真的
 * `lib/chatGrouping.ts` 算一遍，预览站上看到的断行和日期线就是产品里会断的地方。
 */
export function chatTimelineProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  const list = (over.rows as RoomRow[] | undefined) ?? CHAT_ROWS
  return {
    ...CHAT_BASE,
    rows: list,
    dayLabels: dayLabelsFor(list),
    runEdges: list.map((r, i) => runEdgeBetween(list[i - 1]?.block, r.block, { broken: false })),
    ...over,
  }
}

/** 工作电脑表单：两台自有设备（一台离线），以及云端此刻的供应（示例数字，取自 2026-09-30 的 dev）。 */
export const COMPUTE_DEVICES = [
  { device_id: 'lab', name: '实验室工作站', online: true },
  { device_id: 'home', name: '家里那台', online: false },
]
export const CLOUD_SUPPLY = {
  available: true as const,
  offering: 'standard-lxc',
  selectable: {
    cores: { min: 1, max: 32 },
    memory_mb: { min: 512, max: 131072 },
    disk_gb: { min: 2, max: 128 },
  },
  provider: {
    cores: { min: 1, max: 32 },
    memory_mb: { min: 128, max: 131072 },
    disk_gb: { min: 2, max: 128 },
  },
  capacity_known: false,
}
export const CLOUD_SUPPLY_UNKNOWN = { available: false as const, reason: 'MicroCloud unreachable' }
