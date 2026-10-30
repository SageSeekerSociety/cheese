/**
 * 平台在这段对话里运行时记下的事（运行记录）：排队、环境准备与释放、AI 服务重试、
 * 整理上下文、等机器。它们不进对话，只走 `run_record` 帧：现场把它们排在步骤之间，
 * 输入框上方那一行从这里读队友此刻在等什么。
 *
 * 一轮没完成、平台排了自动重试：等的是后端那一行待重试（turn_resume）。打开、重连
 * 时 `room_state` 帧给出此刻还欠着的全部（`resumesNow`），之后它的每一步都是一条运行
 * 记录：排上（`turn_resume_wait`，带重试时刻 `meta.due_at`）、开始重试
 * （`turn_resume_started`）、取消（`turn_resume_stopped`）。
 *
 * 帧里的记录长得和一条事件块一样，改写过的（重试到第几次、整理完没有）用同一个 id
 * 再来一次，这里按 id 换掉旧的那一份。
 */

import type { Block } from '../../../cx_types'
import type { MemberActivityLine } from '../../../lib/memberActivity'
import type { ResumeWait } from '../../../types/roomSocket'

import { computed, ref } from 'vue'

import { t } from '@/i18n'
import { isAgentBlock, isPersonBlock } from '@/lib/authorship'
import { clockTime } from '@/lib/dateText'

/** 手上最多留多少条：够算出每位队友此刻的状态，不会越攒越多。 */
const KEPT = 200

/** 还没开始的那一轮在等的几种事。 */
const BEFORE_START = new Set(['turn_queued'])

function ahead(record: Block): number | null {
  const params = (record.meta?.i18n as { content?: { params?: { ahead?: unknown } } } | undefined)?.content?.params
  return typeof params?.ahead === 'number' ? params.ahead : null
}

/** `run_record` 帧带的那条记录；别的帧是 null。 */
export function runRecordOf(frame: unknown): Block | null {
  const f = frame as { type?: unknown; record?: unknown } | null
  return f?.type === 'run_record' && f.record ? (f.record as Block) : null
}

/** 支线里的一条运行记录，频道主线收到的那一份（`thread_status` 帧）；别的帧是 null。 */
export function threadStatusOf(frame: unknown): { threadId: string; record: Block } | null {
  const f = frame as { type?: unknown; thread_id?: unknown; record?: unknown } | null
  if (f?.type !== 'thread_status' || typeof f.thread_id !== 'string' || !f.record) return null
  return { threadId: f.thread_id, record: f.record as Block }
}

/** 排上了的那条运行记录说的那一次自动重试；别的记录、没写全的是 null。 */
function scheduled(record: Block): ResumeWait | null {
  const m = record.meta
  if (m?.event_type !== 'turn_resume_wait') return null
  if (typeof m.seat !== 'string' || typeof m.resume_id !== 'string' || typeof m.due_at !== 'string') return null
  return {
    conversation_id: record.conversation_id,
    seat: m.seat,
    resume_id: m.resume_id,
    due_at: m.due_at,
    since: record.created_at,
  }
}

/** 一帧在后端进程发出的帧里排第几（`stream` 是这套数的名字）。 */
export interface StreamAt {
  stream: string
  at: number
}

/** 运行记录那一帧带的位置；没带的是 null。 */
export function streamAt(frame: unknown): StreamAt | null {
  const f = frame as { stream?: unknown; at?: unknown } | null
  return typeof f?.stream === 'string' && typeof f.at === 'number' ? { stream: f.stream, at: f.at } : null
}

/** `a` 排在 `b` 之后：同一套数里号更大。不同进程的号没法比，算不上。 */
function after(a: StreamAt | null, b: StreamAt | null): boolean {
  return a !== null && b !== null && a.stream === b.stream && a.at > b.at
}

/** 结束了的那一次自动重试（开始了或取消了）的 id；别的记录是 null。 */
function settled(record: Block): string | null {
  const m = record.meta
  if (m?.event_type !== 'turn_resume_started' && m?.event_type !== 'turn_resume_stopped') return null
  return typeof m.resume_id === 'string' ? m.resume_id : null
}

/**
 * 这一次自动重试之后，对话里有人开口，或者这位队友自己又说了话：轮到重试时平台会
 * 取消它（后端 `_no_longer_applies`），这一刻起就不再说它在等。
 */
function takenBack(wait: ResumeWait, blocks: Block[]): boolean {
  const since = Date.parse(wait.since)
  return blocks.some(
    (b) =>
      b.conversation_id === wait.conversation_id &&
      Date.parse(b.created_at) > since &&
      (isPersonBlock(b) || (isAgentBlock(b) && b.author === wait.seat))
  )
}

export function useRunRecords() {
  const records = ref<Block[]>([])
  // 已经开始或结束了的轮次：它们的排队记录不再说「排队中」。
  const begun = ref<Set<string>>(new Set())
  // 每位队友还欠着的那一次自动重试（按座位），连同说它的那条运行记录的位置
  // （`room_state` 给的没有，记 null）。
  const resumes = ref<Map<string, { wait: ResumeWait; at: StreamAt | null }>>(new Map())
  // 已经开始或取消了的自动重试：晚到的那份 `room_state`、补发的排上记录不再把它说回来。
  let ended = new Set<string>()
  // 最近那份 `room_state` 读库之前的位置：排在它前面的运行记录，那份现场已经算进去了。
  let read: StreamAt | null = null

  /** 一条运行记录，`position` 是它那一帧的位置（`streamAt`）。 */
  function receive(record: Block, position: StreamAt | null = null) {
    const at = records.value.findIndex((r) => r.id === record.id)
    const next = records.value.slice()
    if (at >= 0) next[at] = record
    else next.push(record)
    records.value = next.length > KEPT ? next.slice(next.length - KEPT) : next
    const wait = scheduled(record)
    const over = settled(record)
    // 重连时补发的旧记录排在最近那份现场之前：它说的那一次，现场已经算过了。
    const stale = position !== null && read !== null && position.stream === read.stream && position.at <= read.at
    if (wait && !ended.has(wait.resume_id) && !stale)
      resumes.value = new Map(resumes.value).set(wait.seat, { wait, at: position })
    if (over) {
      ended = new Set(ended).add(over)
      resumes.value = new Map([...resumes.value].filter(([, w]) => w.wait.resume_id !== over))
    }
  }

  /**
   * `room_state` 说的此刻还欠着的全部自动重试（`at` 是它读库之前的位置），换掉手上那
   * 一份 —— 排在它之后的运行记录说的不换：服务端读库在先、发这一帧在后，中间排上的
   * 那一次先到了，这份现场里还没有它。
   */
  function resumesNow(owed: ResumeWait[], at: StreamAt) {
    const next = new Map<string, { wait: ResumeWait; at: StreamAt | null }>()
    for (const wait of owed) if (!ended.has(wait.resume_id)) next.set(wait.seat, { wait, at: null })
    for (const [seat, w] of resumes.value) if (after(w.at, at)) next.set(seat, w)
    resumes.value = next
    read = at
  }

  function turnBegan(id: string) {
    if (begun.value.has(id)) return
    begun.value = new Set([...begun.value, id])
  }

  function reset() {
    records.value = []
    begun.value = new Set()
    resumes.value = new Map()
    ended = new Set()
    read = null
  }

  /**
   * 重连后：排队中的那几轮核对不了（`room_state` 只说在跑的，说不出断线期间是不是已经
   * 跑完了），不再说它们在排队。还在排的，开工时照常来 `turn_started`。
   */
  function forgetWaiting() {
    for (const record of waiting.value.values()) if (record.turn_id) turnBegan(record.turn_id)
  }

  /** 每位还没开工、在排队的队友那一行。同一位只算最近的那一轮。 */
  const waiting = computed(() => {
    const latest = new Map<string, Block>()
    for (const r of records.value) {
      const seat = r.meta?.seat
      if (typeof seat !== 'string' || !r.turn_id || begun.value.has(r.turn_id)) continue
      if (!BEFORE_START.has(String(r.meta?.event_type ?? ''))) continue
      latest.set(seat, r)
    }
    return latest
  })

  /**
   * 输入框上方那一行里，排队的队友、等自动重试的队友各一行（名字由房间给）。
   * `blocks` 是屏幕上的对话：之后有人开口、队友自己说了话的那次自动重试不再算在等。
   */
  function waitingLines(nameOf: (handle: string) => string, blocks: Block[] = []): MemberActivityLine[] {
    const lines: MemberActivityLine[] = [...waiting.value.entries()].map(([handle, r]) => {
      const n = ahead(r)
      return {
        handle,
        name: nameOf(handle),
        kind: 'working',
        since: Date.parse(r.created_at) / 1000,
        detail: n ? t('work.room.site.status.queuedBehind', { ahead: n }) : t('work.room.site.status.queued'),
      }
    })
    for (const [handle, { wait }] of resumes.value) {
      if (waiting.value.has(handle) || takenBack(wait, blocks)) continue
      lines.push({
        handle,
        name: nameOf(handle),
        kind: 'working',
        since: Date.parse(wait.since) / 1000,
        detail: t('work.room.site.status.resumeAt', { time: clockTime(Date.parse(wait.due_at)) }),
      })
    }
    return lines
  }

  return { records, receive, resumesNow, turnBegan, reset, forgetWaiting, waitingLines }
}
