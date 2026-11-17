// 一件任务的状态长什么样：八个状态词中的哪一个、哪个图标、哪种颜色、动不动，以及状态词
// 下面那一行说什么。频道里的任务卡、全部任务、待处理清单、任务页头和侧栏的任务行都从这一处
// 取，同一件任务在五处是同一个样子。
//
// 状态不在这里算：后端 `presentation.phrase` 已经说了它在哪一格（`room_task/presentation.py`），
// 这里只把那个码归到八个状态词之一（`STATUS`），再挑图标和颜色。
//
//   未开始 not_started      刚建，还没人动手（started）
//   进行中 in_progress      在做、在等结果、被退回后修改中（running / waiting / bounced）
//   待回答 awaiting_answer  芝士提了问题在等人答
//   待协助 needs_help       队友说没人帮忙走不下去（blocked）
//   待审阅 in_review        已提交，在审阅链路上：跑检查、修检查、解冲突、等人审阅
//   待部署 awaiting_deploy  最后一步已合并，在等主干 CI 与部署上线（deploying / deploy_blocked）；
//                          合并了不等于上线了，所以不归已完成（蔡松洋 2026-11-17 定的第八个词）
//   已完成 done             最后一步已采纳，或以结论结束
//   已关闭 closed           没做成就结束了
//
// 图标和颜色比状态词细一层（`kind`）：同是进行中，在跑的转、退回的是危险色；同是待部署，
// 主干 CI 红挡住部署时火箭换警示色。
// 状态词只说状态；谁、为什么、多久写在下面那一行，那一行不再重复状态词。

import type { BoardPhrase, RoomTask } from '@/cx_types'

import { phraseLabel } from './board'
import { relTime } from './relTime'
import { waitedFor } from './replyWait'

import { t } from '@/i18n'

export type TaskStateKind =
  | 'running'
  | 'waiting'
  | 'blocked'
  | 'asking'
  | 'review'
  | 'deploying'
  | 'bounced'
  | 'open'
  | 'closed'

/** 一件任务此刻的状态：全产品只用这八个词（`work.taskState.label.*`）。 */
export type TaskStatus =
  | 'not_started'
  | 'in_progress'
  | 'awaiting_answer'
  | 'needs_help'
  | 'in_review'
  | 'awaiting_deploy'
  | 'done'
  | 'closed'

/** 记号和字用哪一组 token（样式在 `TaskStateMark.vue`）。 */
export type TaskStateTone = 'ok' | 'okMark' | 'neutral' | 'warn' | 'ink' | 'danger' | 'faint'

export interface TaskState {
  kind: TaskStateKind
  /** 八个状态词中的哪一个。 */
  status: TaskStatus
  icon: string
  tone: TaskStateTone
  /** 状态词（`work.taskState.label.<status>`）。 */
  label: string
  /** 记号在转：只给「在做」且会话真的在跑的那一刻。 */
  live: boolean
  /** 第一次看到时要闪一下的那件事的钥匙；不闪是 null。见 `firstSight`。 */
  attention: string | null
  /** 标签下面那一行；没有就空串。 */
  line: string
  /** 悬停在记号上说的那一句；没有就空串。 */
  tip: string
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
  deploying: 'deploying',
  deploy_blocked: 'deploying',
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

/** 后端码归到哪种样子。这一版不认识的码（后端先发了新短语）当作未开始，不画成警示。 */
export function stateKind(phrase: BoardPhrase): TaskStateKind {
  return KIND[phrase] ?? 'open'
}

const STATUS: Record<BoardPhrase, TaskStatus> = {
  started: 'not_started',
  draft: 'not_started',
  running: 'in_progress',
  waiting: 'in_progress',
  bounced: 'in_progress',
  idle: 'in_progress',
  awaiting_answer: 'awaiting_answer',
  blocked: 'needs_help',
  gate_running: 'in_review',
  awaiting_checks: 'in_review',
  fixing_checks: 'in_review',
  resolving_conflict: 'in_review',
  updating_branch: 'in_review',
  deploying: 'awaiting_deploy',
  deploy_blocked: 'awaiting_deploy',
  checks_failed: 'in_review',
  awaiting_review: 'in_review',
  accepted: 'done',
  completed: 'done',
  closed: 'closed',
  archived: 'closed',
}

/** 后端码归到八个状态词中的哪一个。不认识的码当作未开始。 */
export function taskStatus(phrase: BoardPhrase): TaskStatus {
  return STATUS[phrase] ?? 'not_started'
}

/** 状态词，按当前语言现取。 */
export function statusLabel(status: TaskStatus): string {
  return t(`work.taskState.label.${status}`)
}

/** 全部任务按状态分组时的次序：要人出手的在前，结束的在后。 */
export const STATUS_ORDER: readonly TaskStatus[] = [
  'needs_help',
  'awaiting_answer',
  'in_review',
  'awaiting_deploy',
  'in_progress',
  'not_started',
  'done',
  'closed',
]

const LOOK: Record<TaskStateKind, { icon: string; tone: TaskStateTone }> = {
  running: { icon: 'mdi-timelapse', tone: 'ok' },
  waiting: { icon: 'mdi-timer-sand', tone: 'neutral' },
  blocked: { icon: 'mdi-alert-circle', tone: 'warn' },
  asking: { icon: 'mdi-help-circle', tone: 'warn' },
  review: { icon: 'mdi-file-eye-outline', tone: 'ink' },
  deploying: { icon: 'mdi-rocket-launch-outline', tone: 'neutral' },
  bounced: { icon: 'mdi-undo-variant', tone: 'danger' },
  open: { icon: 'mdi-circle-outline', tone: 'faint' },
  closed: { icon: 'mdi-minus-circle-outline', tone: 'faint' },
}

/** 「在做」但会话停着：同一种颜色，换一个不转的图标 —— 转与不转关掉动画之后也分得开。 */
const RUNNING_IDLE_ICON = 'mdi-progress-clock'
/** 已完成的画一个绿勾，没做成就关了的照旧淡色一道横。 */
const ACCEPTED_ICON = 'mdi-check-circle-outline'

/** 审阅链路上平台或队友正在做的那一步：写在「待审阅」下面那一行开头。等人审阅那一步
 *  不写（那一行已经说了等谁）。 */
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
      | 'owner_handle'
      | 'card'
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
  /** 看的人的 handle：等的是他就写「等你」。 */
  viewer: string
  /** 做这件任务的 AI 队友叫什么（项目里给它起的名字）：审阅链路那几步和待协助那一句点它的名。 */
  agentName: string
  /** handle → 名字。 */
  nameOf?: (handle: string) => string
  /** 现在几点（毫秒）；不给就是此刻。 */
  now?: number
}

/** 下面那一行的几段连成一句：中文用逗号，不用「·」。 */
export function joined(parts: (string | null | undefined | false)[]): string {
  return parts.filter((part): part is string => !!part).join(t('work.taskState.separator'))
}

/** 卡被退回时写谁退回的：「林晓已退回」。不是退回（闸门判红、没跑成）写检查未通过。 */
function returnedBy(card: StateRow['card'], nameOf: (handle: string) => string): string | null {
  if (card?.status === 'rejected')
    return card.decided_by
      ? t('work.taskState.returnedBy', { name: nameOf(card.decided_by) })
      : t('work.taskState.returned')
  if (card?.status === 'gate_failed' || card?.status === 'gate_blocked') return t('work.taskState.checksFailed')
  return null
}

/** 一件任务此刻的状态：图标、颜色、动不动、标签和下面那一行。 */
export function taskState(row: StateRow, ctx: StateContext): TaskState {
  const { phrase } = row.presentation
  const kind = stateKind(phrase)
  const status = taskStatus(phrase)
  const look = LOOK[kind]
  const now = ctx.now ?? Date.now()
  const nameOf = ctx.nameOf ?? ((handle: string) => handle)
  const note = (row.progress_note ?? '').trim()
  const live = kind === 'running' && sessionRunning(row)
  const waitsOn = (handle: string | null | undefined, mine: boolean): string | null =>
    mine ? t('work.taskState.waitsOnYou') : handle ? t('work.taskState.waitsOn', { name: nameOf(handle) }) : null
  let icon = look.icon
  let tone = look.tone
  const label = statusLabel(status)
  let line = ''
  let tip = ''
  switch (kind) {
    case 'running': {
      if (!live) icon = RUNNING_IDLE_ICON
      // 卡被退回，平台把任务放回进行中、队友还没说自己在改什么：写谁退回的，修改中。
      if (!note && row.card?.status === 'rejected') {
        line = joined([returnedBy(row.card, nameOf), t('work.taskState.revising')])
        break
      }
      // 那句话和那个时刻只在它报的就是在做时属于这一格：它报了在等、需要协助，会话又跑
      // 起来了（后端照会话判成进行中），那句说的是在等什么、卡在哪，写在下面就是假话。
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
      // 检查没过、芝士清不掉：下一步在负责人手上，写等谁。
      const step =
        phrase === 'checks_failed'
          ? joined([t('work.taskState.checksFailed'), waitsOn(row.owner_handle, row.needs_my_action === true)])
          : REVIEW_STEP[phrase]
            ? phraseLabel(phrase, ctx.agentName)
            : waitsOn(reviewer, mine)
      line = joined([
        step,
        card?.subject,
        card?.version ? t('work.taskState.version', { n: card.version }) : null,
        since && t('work.taskState.submitted', { time: since }),
      ])
      break
    }
    case 'deploying':
      // 合并了不等于上线了：主干 CI 跑完、部署上 dev 之后才写总结关闭。主干红了挡住
      // 部署时换成警示色，那一句说卡在哪；红的那次 run 的链接在任务页头。
      if (phrase === 'deploy_blocked') tone = 'warn'
      tip = phraseLabel(phrase, ctx.agentName)
      line = tip
      break
    case 'bounced':
      // 退回或检查没过之后没人在改：只写发生了什么，不写修改中。
      line = returnedBy(row.card, nameOf) ?? ''
      break
    case 'closed':
      if (status === 'done') {
        icon = ACCEPTED_ICON
        tone = 'okMark'
        const decider = row.card?.status === 'accepted' ? row.card.decided_by : null
        if (phrase === 'accepted' && decider) line = t('work.taskState.acceptedBy', { name: nameOf(decider) })
      }
      break
    default:
      break
  }
  return {
    kind,
    status,
    icon,
    tone,
    label,
    live,
    attention: kind === 'blocked' || kind === 'asking' ? `${row.id}:${phrase}:${row.progress_set_at ?? ''}` : null,
    line,
    tip,
  }
}
