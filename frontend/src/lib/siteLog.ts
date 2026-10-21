// 现场 (the read-only transcript timeline) reads like a chat log, so it has to
// behave like one: newest at the bottom and visible on open, and a single
// enormous entry must not push everything else off the screen.
//
import type { Block } from '../cx_types'

import { noticeText } from './noticeText'
import { toolLabel } from './toolLabels'

import { t } from '@/i18n'

/**
 * How many frames the tail-pin may keep re-pinning while the panel is still
 * growing. ~30 frames ≈ 500ms at 60fps, and it stops the moment the height
 * holds steady for one frame — measured, the panel settles in about 120ms.
 */
export const SITE_TAIL_PIN_FRAMES = 30

/**
 * Whether to pin the view to the bottom for another frame.
 *
 * A one-shot `scrollTop = scrollHeight` is not enough, and #327 shipped exactly
 * that. The panel renders its spinner FIRST, so at the moment the scroll runs
 * the container is one viewport tall with nothing to scroll — the assignment
 * clamps to 0 — and the real timeline then lays out underneath, leaving the
 * reader on the oldest entry, which is the bug #327 set out to fix. Measured on
 * the deployed page afterwards: scrollHeight 500 at +40ms, 2066 at +120ms,
 * scrollTop 0 the whole way.
 *
 * So re-pin while the height is still moving, and stop as soon as it isn't.
 * Both bounds matter: without the height check this would fight the reader's
 * own scrolling forever, and without the frame cap a panel that never settles
 * (a live-streaming turn) would pin for the life of the page.
 */
export function shouldKeepPinning(
  height: number,
  lastHeight: number,
  frames: number,
  maxFrames = SITE_TAIL_PIN_FRAMES
): boolean {
  return height !== lastHeight && frames < maxFrames
}

// ---- 按轮分组 ----
//
// 现场是一条平铺的时间线，而干活本来是一轮一轮发生的：一次调用二十个工具，铺
// 成二十条等权的行，读的人看不出哪些是同一件事的经过。每个块都带 `turn_id`
// （人写的块为空），按它把相邻的块收成一组，组头才有地方写这一轮几步、多久。
//
// 相邻才收：一个 `turn_id` 在时间线上本来就是连续的，按 id 建表反而会把中间
// 隔着别的轮次的两段拼到一起，显示出一段从未发生过的连续工作。

interface NarrationMeta {
  [key: string]: unknown
  tool?: string
  record?: unknown
}

/** 这一行是工具调用，还是芝士自己说的话。 */
export function isNarration(meta?: NarrationMeta | null): boolean {
  if (!meta) return false
  return meta.tool === undefined && meta.record !== undefined
}

export interface SiteTurn<T> {
  /** 分组键：轮次 id，没有 id 的那些用它们头一条的 id。 */
  key: string
  /** 不属于任何一轮的一条平台记录（环境空闲释放了、某人改了文档）：只占一行，不成组。 */
  loose: boolean
  entries: T[]
  /** 这一组头一条的时间，组头显示它。 */
  startedAt: string
  /** 工具调用的条数 —— 芝士说的话不是「一步」。 */
  steps: number
  /** 这一轮从开始到最后一条，秒。开始时间不知道时从头一条算；是 0 时组头不显示用时。 */
  seconds: number
}

interface TurnLike {
  id: string
  turn_id?: string | null
  created_at: string
  meta?: NarrationMeta | null
}

/**
 * `starts`：轮次 id → 这一轮开始的时刻（毫秒）。一轮的头一步落在准备和模型第一次
 * 回话之后，从头一步算，「思考中」等的那十几秒就不见了，组头会说这一轮只用了两秒。
 */
export function groupByTurn<T extends TurnLike>(blocks: T[], starts: Record<string, number> = {}): SiteTurn<T>[] {
  const turns: SiteTurn<T>[] = []
  for (const block of blocks) {
    const last = turns[turns.length - 1]
    const id = block.turn_id ?? null
    // 没有轮次 id 的块（旧数据、人写的）各自成组：把它们收进上一组，等于声称
    // 它们属于那一轮，而那正是我们不知道的事。
    const sameTurn = last !== undefined && id !== null && last.key === id
    if (sameTurn) {
      last.entries.push(block)
    } else {
      // 平台提示、平台动作（某人改了文档）、运行记录：不是哪一轮的步骤。
      const meta = block.meta ?? {}
      const loose = id === null && (meta.who !== undefined || meta.platform === true || meta.action !== undefined)
      turns.push({ key: id ?? block.id, loose, entries: [block], startedAt: block.created_at, steps: 0, seconds: 0 })
    }
  }
  for (const turn of turns) {
    // 平台自己说的一句（重试、等机器、这一轮失败了）也不是它做的一步：带 `who`
    // 的是平台提示（后端 platform_notices.notice 拼的）。
    turn.steps = turn.entries.filter((b) => Boolean(b.meta?.tool)).length
    // 平台替一轮写的开场那一行落在这一轮登记之前，所以取两者里早的那个。
    const first = Math.min(Date.parse(turn.entries[0].created_at), starts[turn.key] ?? Infinity)
    const last = Date.parse(turn.entries[turn.entries.length - 1].created_at)
    turn.seconds = Number.isFinite(first) && Number.isFinite(last) ? Math.max(0, Math.round((last - first) / 1000)) : 0
  }
  return turns
}

/** 一轮用了多久，写成组头上的那一小截。 */
export function formatSpan(seconds: number): string {
  if (seconds < 60) return t('work.room.site.span.seconds', { s: seconds })
  const minutes = Math.floor(seconds / 60)
  const rest = String(seconds % 60).padStart(2, '0')
  if (minutes < 60) return t('work.room.site.span.minutes', { m: minutes, s: rest })
  return t('work.room.site.span.hours', { h: Math.floor(minutes / 60), m: String(minutes % 60).padStart(2, '0') })
}

// 运行记录（平台运行中记下的事，只在现场）的动词列写它是哪一类事，正文是那一句话。
// 键名写全，不拼：拼出来的键谁也搜不到。
const RECORD_VERB: Record<string, string> = {
  turn_queued: 'work.room.site.record.queue',
  delivery_checking: 'work.room.site.record.delivery',
  delivery_fallback: 'work.room.site.record.delivery',
  tools_recovered: 'work.room.site.record.delivery',
  prompt_replayed: 'work.room.site.record.delivery',
  timed_delivery: 'work.room.site.record.delivery',
  api_retry: 'work.room.site.record.retry',
  turn_resume_wait: 'work.room.site.record.recovery',
  turn_resume_started: 'work.room.site.record.recovery',
  turn_resume_stopped: 'work.room.site.record.recovery',
  context_compact: 'work.room.site.record.compact',
  device_waiting: 'work.room.site.record.environment',
  cloud_startup: 'work.room.site.record.environment',
  cloud_provisioning: 'work.room.site.record.environment',
  sandbox_asleep: 'work.room.site.record.environment',
  memory_changed: 'work.room.site.record.memory',
}

/** 这一行是运行记录：平台在运行中记下的事，不是芝士做的一步。 */
export function isRunRecord(b: Block): boolean {
  return String(b.meta?.event_type ?? '') in RECORD_VERB
}

function recordVerb(b: Block): string | null {
  const key = RECORD_VERB[String(b.meta?.event_type ?? '')]
  return key ? t(key) : null
}

export function eventVerb(b: Block): string {
  const recorded = recordVerb(b)
  if (recorded) return recorded
  return b.meta?.tool ? toolLabel(b.meta.tool) : noticeText(b).split('\n')[0]
}

export function eventFailed(b: Block): boolean {
  const parts = b.meta?.parts as { failed?: boolean }[] | undefined
  return parts?.some((part) => part.failed) ?? false
}
