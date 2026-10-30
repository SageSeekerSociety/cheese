/**
 * 平台在这段对话里运行时记下的事（运行记录）：排队、环境准备与释放、AI 服务重试、
 * 整理上下文、等机器。它们不进对话，只走 `run_record` 帧：现场把它们排在步骤之间，
 * 输入框上方那一行从这里读队友此刻在等什么。
 *
 * 只有一种等待不在运行记录里：一轮没完成、平台排了自动重试。那一句在对话里
 * （`turn_resume_wait`，带着重试时刻 `meta.due_at`），刷新、重连之后照样在，输入框上方
 * 那一行从对话读它（`resumeWaits`）。
 *
 * 帧里的记录长得和一条事件块一样，改写过的（重试到第几次、整理完没有）用同一个 id
 * 再来一次，这里按 id 换掉旧的那一份。
 */

import type { Block } from '../../../cx_types'
import type { MemberActivityLine } from '../../../lib/memberActivity'

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

/** 平台排好的自动重试在什么时刻（毫秒）；这一行没写就是 null。 */
function resumeAt(block: Block): number | null {
  const due = block.meta?.due_at
  const at = typeof due === 'string' ? Date.parse(due) : Number.NaN
  return Number.isFinite(at) ? at : null
}

/**
 * 对话里还在等自动重试的队友，各自最近的那一句（按座位）。之后同一段对话里有人
 * 开口（平台就不再自动重试）、这位队友又说了话，或者平台又为它说了一句，就不算在等。
 */
export function resumeWaits(blocks: Block[]): Map<string, Block> {
  const waits = new Map<string, Block>()
  for (const b of blocks) {
    const seat = typeof b.meta?.seat === 'string' ? b.meta.seat : null
    for (const [handle, wait] of waits) {
      if (wait.conversation_id !== b.conversation_id) continue
      if (isPersonBlock(b) || (isAgentBlock(b) ? b.author : seat) === handle) waits.delete(handle)
    }
    if (seat && b.meta?.event_type === 'turn_resume_wait' && resumeAt(b) !== null) waits.set(seat, b)
  }
  return waits
}

export function useRunRecords() {
  const records = ref<Block[]>([])
  // 已经开始或结束了的轮次：它们的排队记录不再说「排队中」。
  const begun = ref<Set<string>>(new Set())

  function receive(record: Block) {
    const at = records.value.findIndex((r) => r.id === record.id)
    const next = records.value.slice()
    if (at >= 0) next[at] = record
    else next.push(record)
    records.value = next.length > KEPT ? next.slice(next.length - KEPT) : next
  }

  function turnBegan(id: string) {
    if (begun.value.has(id)) return
    begun.value = new Set([...begun.value, id])
  }

  function reset() {
    records.value = []
    begun.value = new Set()
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
   * `blocks` 是屏幕上的对话：等自动重试的那几位从它读。
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
    for (const [handle, b] of resumeWaits(blocks)) {
      if (waiting.value.has(handle)) continue
      lines.push({
        handle,
        name: nameOf(handle),
        kind: 'working',
        since: Date.parse(b.created_at) / 1000,
        detail: t('work.room.site.status.resumeAt', { time: clockTime(resumeAt(b) ?? Number.NaN) }),
      })
    }
    return lines
  }

  return { records, receive, turnBegan, reset, forgetWaiting, waitingLines }
}
