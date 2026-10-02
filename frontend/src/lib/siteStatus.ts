// 一位队友在跑的那一轮，此刻在干什么：它的头像跟着变脸（lib/agentFace），输入框下面
// 「Cedar 正在工作 · 思考中」那一句也说它。
//
// 一轮在跑的时候，现场可以好几分钟一行都不长——模型在想、请求在重
// 试、机器断了，从外面看都是「没动静」。这里把它们分开说。
//
// 能从时间线本身读出来的都从时间线读：一步刚开始（工具行）、平台说它在重试、在压缩
// 上下文或在等机器（三种提示行，原地更新）。读不出来的那一种——请求发出去了、还没有任何东西回
// 来——就是「思考中」。

import type { Block } from '../cx_types'

import { eventFailed, eventVerb, isNarration } from './siteLog'

export type SiteState = 'thinking' | 'acting' | 'retrying' | 'compacting' | 'waiting'

export interface SiteStatus {
  state: SiteState
  /** acting：这一步的动词，和时间线上那一行同一个词。 */
  verb?: string
  /** retrying：第几次；平台没说就是 null。 */
  attempt?: number | null
  /** 最早那个在跑的轮次从什么时候开始（毫秒）。不知道就是 null。 */
  startedAt: number | null
}

/** 这一轮以失败收场的提示：房间停下来不是因为做完了。 */
export const STOPPED = new Set(['turn_failed', 'turn_timeout'])
/** 还没开工、在等工作电脑起来的那几种提示。 */
const PROVISIONING = new Set(['cloud_provisioning', 'cloud_startup', 'machine_provisioning'])

function eventType(b: Block): string {
  return typeof b.meta?.event_type === 'string' ? b.meta.event_type : ''
}

/** 一行最近一次变的时刻。原地改过的提示（重试次数涨了、机器回来了）带着 `meta.at`。 */
function touchedAt(b: Block): number {
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
 * @param blocks  时间线上的块
 * @param turns   在跑的轮次 id → 开始时间（毫秒）
 */
export function siteStatus(blocks: Block[], turns: Record<string, number>): SiteStatus {
  const starts = Object.values(turns)
  const startedAt = starts.length ? Math.min(...starts) : null
  const running = blocks.filter((b) => b.turn_id && b.turn_id in turns)
  const last = latest(running)
  const base = { startedAt }

  if (!last) {
    // 还没有这一轮的任何一行。工作电脑还在起来的话，它在等的是机器，不是在想。
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
  // 在压缩上下文：几分钟不说话、也不回新消息，但它没挂。整理完那一行会改成已结束。
  if (type === 'context_compact' && last.meta?.state !== 'over') return { state: 'compacting', ...base }
  if (type === 'device_waiting' && last.meta?.state !== 'over') return { state: 'waiting', ...base }
  // 一步开始了、还没听说它结束：它就是此刻在做的事。挂了的、交回了输出的，都已
  // 经结束了。
  if (last.meta?.tool && !isNarration(last.meta) && !eventFailed(last) && !last.meta.output_bytes) {
    return { state: 'acting', verb: eventVerb(last), ...base }
  }
  return { state: 'thinking', ...base }
}
