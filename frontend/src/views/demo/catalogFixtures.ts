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
import type { OpenFileTab } from '@/composables/useTopicMemory'
import type { Block, FeedbackStatus } from '@/cx_types'
import type { SpaceLearningExcerpt } from '@/network/api/spaces/types'
import type { ChatLine, Frame } from './demoScene'

import { answer } from './demoBackend'
import { frameAt } from './demoScene'
import { SCENES } from './scenes'

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
 * 预览站里会自己取数的那几件（验收卡读卡和读检查）该读到什么。装到演示后端上
 * （`demoBackend`），和 `DemoRoom.installPanelAnswers` 是同一件事。
 */
export function installCatalogAnswers(): void {
  answer('/topics/demo/accept-card', () => ({ data: ACCEPT_CARD ? [ACCEPT_CARD] : [], total: ACCEPT_CARD ? 1 : 0 }))
  answer('/topics/demo/pr-checks', () => ACCEPT_CHECKS ?? { available: false })
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
    name: row.block.author === 'system' ? null : NAMES[row.block.author] ?? null,
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
