// 一件任务的状态长什么样：哪种状态、哪个图标、哪种颜色、动不动，以及标签下面那一行
// 说什么。频道里的任务卡、全部任务、待处理清单、任务页头和侧栏的任务行都从这一处取，
// 同一件任务在五处是同一个样子。
//
// 状态不在这里算：后端 `presentation.phrase` 已经说了它在哪一格（`room_task/presentation.py`），
// 这里只把那个码归到界面上的几种状态，再挑图标和颜色。
//
//   在做   running            绿；会话真的在跑时转动，停着时换成静止的图标
//   在等   waiting            中性
//   卡住   blocked            警示；第一次看到时闪一下
//   待回答 awaiting_answer    警示，问号；第一次看到时闪一下
//   待审阅 卡在审阅链路上      强调
//   退回   bounced            危险
//   已采纳                    绿勾，字是中性的
//   开启 / 已关闭             淡
//
// 标签只说状态，两三个字；谁、为什么、多久写在后面那一行，那一行不再重复标签。

import type { BoardPhrase, RoomTask } from '@/cx_types'

import { phraseLabel } from './board'
import { relTime } from './relTime'
import { waitedFor } from './replyWait'

import { t } from '@/i18n'

export type TaskStateKind = 'running' | 'waiting' | 'blocked' | 'asking' | 'review' | 'bounced' | 'open' | 'closed'

/** 记号和字用哪一组 token（样式在 `TaskStateMark.vue`）。 */
export type TaskStateTone = 'ok' | 'okMark' | 'neutral' | 'warn' | 'ink' | 'danger' | 'faint'

export interface TaskState {
  kind: TaskStateKind
  icon: string
  tone: TaskStateTone
  /** 标签：状态本身，两三个字（`work.taskState.label.*`）。 */
  label: string
  /** 记号在转：只给「在做」且会话真的在跑的那一刻。 */
  live: boolean
  /** 第一次看到时要闪一下的那件事的钥匙；不闪是 null。见 `firstSight`。 */
  attention: string | null
  /** 标签下面那一行；没有就空串。 */
  line: string
}

const KIND: Record<BoardPhrase, TaskStateKind> = {
  running: 'running',
  waiting: 'waiting',
  blocked: 'blocked',
  awaiting_answer: 'asking',
  gate_running: 'review',
  awaiting_checks: 'review',
  fixing_checks: 'review',
  resolving_conflict: 'review',
  updating_branch: 'review',
  awaiting_review: 'review',
  checks_failed: 'review',
  bounced: 'bounced',
  started: 'open',
  idle: 'open',
  draft: 'open',
  accepted: 'closed',
  completed: 'closed',
  closed: 'closed',
  archived: 'closed',
}

/** 后端码归到哪种状态。这一版不认识的码（后端先发了新短语）当作开启，不画成警示。 */
export function stateKind(phrase: BoardPhrase): TaskStateKind {
  return KIND[phrase] ?? 'open'
}

const LOOK: Record<TaskStateKind, { icon: string; tone: TaskStateTone }> = {
  running: { icon: 'mdi-timelapse', tone: 'ok' },
  waiting: { icon: 'mdi-timer-sand', tone: 'neutral' },
  blocked: { icon: 'mdi-alert-circle', tone: 'warn' },
  asking: { icon: 'mdi-help-circle', tone: 'warn' },
  review: { icon: 'mdi-file-eye-outline', tone: 'ink' },
  bounced: { icon: 'mdi-undo-variant', tone: 'danger' },
  open: { icon: 'mdi-circle-outline', tone: 'faint' },
  closed: { icon: 'mdi-minus-circle-outline', tone: 'faint' },
}

/** 「在做」但会话停着：同一种颜色，换一个不转的图标 —— 转与不转关掉动画之后也分得开。 */
const RUNNING_IDLE_ICON = 'mdi-progress-clock'
/** 关闭里最后一步被采纳的那种画一个绿勾，没交付就关了的照旧淡色一道横。 */
const ACCEPTED_ICON = 'mdi-check-circle-outline'

/** 审阅链路上平台或队友正在做的那一步：写在「待审阅」后面那一行开头。等人审阅那一步
 *  不写（后面已经说了等谁）。 */
const REVIEW_STEP: Partial<Record<BoardPhrase, true>> = {
  gate_running: true,
  awaiting_checks: true,
  fixing_checks: true,
  resolving_conflict: true,
  updating_branch: true,
  checks_failed: true,
}

type StateRow = Pick<RoomTask, 'id' | 'presentation'> &
  Partial<
    Pick<
      RoomTask,
      | 'progress_state'
      | 'progress_note'
      | 'progress_set_at'
      | 'session_running'
      | 'moving'
      | 'waiting_on'
      | 'awaits_me'
      | 'needs_my_action'
      | 'card'
      | 'status'
    >
  >

/** 做它的 AI 队友此刻是不是真的在跑。频道的任务列表和单个任务带 `session_running`；
 *  项目级列表和侧栏行只带 `moving`（会话在跑，或卡走在交付链路上）—— 「在做」的时候
 *  没有卡在链路上，所以那边 `moving` 就是会话在跑。 */
export function sessionRunning(row: Pick<StateRow, 'session_running' | 'moving'>): boolean {
  return row.session_running ?? row.moving === true
}

// 这一页里已经闪过的那几件：任务 + 它进这个状态的那一刻。列表刷新、组件重画、同一件
// 在别的表面又出现一次都不再闪；它再卡住一次（报的时刻变了）才再闪。
const seen = new Set<string>()

/** 这件事是不是这一页第一次看到：是就记下并回 true，之后都回 false。 */
export function firstSight(key: string): boolean {
  if (seen.has(key)) return false
  seen.add(key)
  return true
}

export interface StateContext {
  /** 看的人的 handle：等的是他就写「待你审阅」「待你回答」。 */
  viewer: string
  /** 做这件任务的 AI 队友叫什么（项目里给它起的名字）：标签和卡住那一句都点它的名。 */
  agentName: string
  /** handle → 名字。 */
  nameOf?: (handle: string) => string
  /** 现在几点（毫秒）；不给就是此刻。 */
  now?: number
}

function joined(parts: (string | null | undefined | false)[]): string {
  return parts.filter((part): part is string => !!part).join(' · ')
}

/** 一件任务此刻的状态：图标、颜色、动不动、标签和下面那一行。 */
export function taskState(row: StateRow, ctx: StateContext): TaskState {
  const { phrase } = row.presentation
  // 最后一步采纳了、任务还开着（队友在写总结）的那一会儿：还是在做，不另起一个说法。
  const wrappingUp = phrase === 'accepted' && !!row.status && row.status !== 'closed'
  const kind = wrappingUp ? 'running' : stateKind(phrase)
  const look = LOOK[kind]
  const now = ctx.now ?? Date.now()
  const nameOf = ctx.nameOf ?? ((handle: string) => handle)
  const note = (row.progress_note ?? '').trim()
  const live = kind === 'running' && sessionRunning(row)
  const waitsOn = (handle: string | null | undefined, mine: boolean): string | null =>
    mine ? t('work.taskState.waitsOnYou') : handle ? t('work.taskState.waitsOn', { name: nameOf(handle) }) : null
  let icon = look.icon
  let tone = look.tone
  let label = t(`work.taskState.label.${kind}`)
  let line = ''
  switch (kind) {
    case 'running': {
      if (!live) icon = RUNNING_IDLE_ICON
      // 那句话和那个时刻只在它报的就是「在做」时属于这一格：它报了在等、卡住，会话又跑
      // 起来了（后端照会话判成在做），那句说的是在等什么、卡在哪，写在「在做」后面就是假话。
      if (row.progress_state !== 'working') break
      const time = waitedFor(row.progress_set_at, now)
      line = joined([note, time && t('work.taskState.workedFor', { time })])
      break
    }
    case 'waiting': {
      const time = waitedFor(row.progress_set_at, now)
      line = joined([note, time && t('work.taskState.waitedFor', { time })])
      break
    }
    case 'blocked':
      line = note || t('work.taskState.blockedFallback', { name: ctx.agentName })
      break
    case 'asking': {
      const mine = row.needs_my_action === true
      line = waitsOn(row.waiting_on, mine) ?? ''
      break
    }
    case 'review': {
      // 只有卡真在等人审阅（`awaiting_review`）时才写在等谁，名字取卡上的审阅人：任务
      // 建时定的那位可能已经改由别人审阅。跑检查、修检查、解冲突时写的是那一步，下一步
      // 在平台和队友手上，检查没过时在负责人手上，都不是在等谁审阅。
      const card = row.card
      const reviewer = phrase === 'awaiting_review' ? row.waiting_on ?? card?.reviewer ?? null : null
      const since = card?.since ? relTime(card.since) : ''
      const mine = phrase === 'awaiting_review' && row.needs_my_action === true
      line = joined([
        REVIEW_STEP[phrase] ? phraseLabel(phrase, ctx.agentName) : waitsOn(reviewer, mine),
        card?.subject,
        card?.version ? t('work.taskState.version', { n: card.version }) : null,
        since && t('work.taskState.submitted', { time: since }),
      ])
      break
    }
    case 'bounced':
      line = t('work.taskState.bouncedLine', { name: ctx.agentName })
      break
    case 'closed':
      if (phrase === 'accepted') {
        icon = ACCEPTED_ICON
        tone = 'okMark'
        label = t('work.taskState.label.accepted')
      }
      break
    default:
      break
  }
  return {
    kind,
    icon,
    tone,
    label,
    live,
    attention: kind === 'blocked' || kind === 'asking' ? `${row.id}:${phrase}:${row.progress_set_at ?? ''}` : null,
    line,
  }
}
