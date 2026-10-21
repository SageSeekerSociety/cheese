/**
 * 对话栏（`ChatPanel` 那一组）拆出来的那几件：那一格的主题、时间线摆的几行，以及
 * `ChatTimeline` 要吃的那一大把 props。
 *
 * 从 `../catalogFixtures.ts` 拆出来的一册：行还是剧本第 0 到第 1 步那一段，
 * 由 `./common` 算。条目在 `../catalogChat.ts`。
 */
import type { Block, ReplyPreview, Topic } from '@/cx_types'
import type { Outgoing } from '@/lib/composerDrafts'

import { ref } from 'vue'

import { DEMO_PROJECT, DEMO_TOPIC } from '../demoPanels'

import { AGENT_NAME, NAMES, ROOM_REFS, type RoomRow, rows, SCENE } from './common'

import { dayLabelsFor, runEdgeBetween } from '@/lib/chatGrouping'

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
  starterPrompts: [] as { label: string; hint: string; text: string }[],
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
  avatarOf: () => null as string | null,
  displayName: (b: Block | ReplyPreview) => NAMES[b.author] ?? b.author,
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
