// 现场顶上那一行：此刻它在干什么。
//
// 对话栏说「正在处理…」的时候，现场可以好几分钟一行都不长——模型在想、请求在重
// 试、机器断了，从外面看都是「没动静」。这一行把它们分开说，而且只要一轮在跑就
// 不留白。
//
// 能从时间线本身读出来的都从时间线读：一步刚开始（工具行）、平台说它在重试或在等
// 机器（两种提示行，原地更新）。读不出来的那一种——请求发出去了、还没有任何东西回
// 来——就是「思考中」。

import type { Block } from '../cx_types'

import { eventFailed, eventVerb, isNarration } from './siteLog'

export type SiteState = 'thinking' | 'acting' | 'retrying' | 'waiting' | 'stopped' | 'idle'

export interface SiteStatus {
  state: SiteState
  /** acting：这一步的动词，和时间线上那一行同一个词。 */
  verb?: string
  /** retrying：第几次；平台没说就是 null。 */
  attempt?: number | null
  /** 最早那个在跑的轮次从什么时候开始（毫秒）。不知道就是 null。 */
  startedAt: number | null
  /** 最近一次有动静（毫秒）。一行都没有就是 null。 */
  lastAt: number | null
}

/** 这一轮以失败收场的提示：房间停下来不是因为做完了。 */
const STOPPED = new Set(['turn_failed', 'turn_timeout'])
/** 还没开工、在等运行环境起来的那几种提示。 */
const PROVISIONING = new Set(['cloud_provisioning', 'cloud_startup', 'machine_provisioning'])

function eventType(b: Block): string {
  return typeof b.meta?.event_type === 'string' ? b.meta.event_type : ''
}

/** 一行最近一次变的时刻。原地改过的提示（重试次数涨了、机器回来了）带着 `meta.at`。 */
export function touchedAt(b: Block): number {
  const created = Date.parse(b.created_at)
  const at = typeof b.meta?.at === 'string' ? Date.parse(b.meta.at) : Number.NaN
  return Number.isFinite(at) ? Math.max(at, created) : created
}

function latest(blocks: Block[]): Block | undefined {
  let last: Block | undefined
  for (const b of blocks) if (!last || touchedAt(b) >= touchedAt(last)) last = b
  return last
}

/**
 * @param blocks  现场时间线（这一栏正在显示的那些行）
 * @param working 房间里有没有活在跑（对话栏说了算）
 * @param turns   在跑的轮次 id → 开始时间（毫秒）
 */
export function siteStatus(blocks: Block[], working: boolean, turns: Record<string, number>): SiteStatus {
  if (!working) {
    const last = latest(blocks)
    const lastTurn = last?.turn_id ?? null
    const stopped =
      last !== undefined &&
      (lastTurn ? blocks.filter((b) => b.turn_id === lastTurn) : [last]).some((b) => STOPPED.has(eventType(b)))
    return { state: stopped ? 'stopped' : 'idle', startedAt: null, lastAt: last ? touchedAt(last) : null }
  }

  const starts = Object.values(turns)
  const startedAt = starts.length ? Math.min(...starts) : null
  const running = blocks.filter((b) => b.turn_id && b.turn_id in turns)
  const last = latest(running)
  const base = { startedAt, lastAt: last ? touchedAt(last) : startedAt }

  if (!last) {
    // 还没有这一轮的任何一行。运行环境还在起来的话，它在等的是机器，不是在想。
    const before = latest(blocks)
    const provisioning =
      before !== undefined &&
      PROVISIONING.has(eventType(before)) &&
      !['ready', 'failed'].includes(String(before.meta?.state))
    return { state: provisioning ? 'waiting' : 'thinking', ...base }
  }
  const type = eventType(last)
  if (type === 'api_retry') {
    const attempt = typeof last.meta?.attempt === 'number' ? last.meta.attempt : null
    return { state: 'retrying', attempt, ...base }
  }
  if (type === 'device_waiting' && last.meta?.state !== 'over') return { state: 'waiting', ...base }
  // 一步开始了、还没听说它结束：它就是此刻在做的事。挂了的那一步已经结束了。
  if (last.meta?.tool && !isNarration(last.meta) && !eventFailed(last)) {
    return { state: 'acting', verb: eventVerb(last), ...base }
  }
  return { state: 'thinking', ...base }
}
