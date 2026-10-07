// 现场每一轮顶上那根细条：这一轮的时间花在了哪几段。排队（前面的轮次占着名额）、
// 等环境、AI 服务出错后重试，剩下的是在干活。一轮慢的时候，一眼看得出慢在哪一段。
//
// 只读这一轮自己的几条运行记录和它的起止时刻，不猜：排队从排队那条记录到这一轮开始；
// 等环境从等机器那条到它说连上了（`meta.at`），没说就到下一步；重试从重试那条到
// 下一步出现。

import type { Block } from '../cx_types'

export type Phase = 'queue' | 'environment' | 'retry' | 'work'

export interface PhaseSpan {
  phase: Phase
  seconds: number
}

/** 条上从左到右的顺序：一轮的时间大体就是这么走的。 */
const ORDER: Phase[] = ['queue', 'environment', 'retry', 'work']

function eventType(b: Block): string {
  return String(b.meta?.event_type ?? '')
}

function at(iso: unknown): number {
  return typeof iso === 'string' ? Date.parse(iso) : Number.NaN
}

/** 一条记录之后、第一件不是平台记下的事发生的时刻。 */
function nextStep(entries: Block[], after: number): number {
  for (const b of entries) {
    const when = at(b.created_at)
    if (when > after && !b.meta?.event_type) return when
  }
  return Number.NaN
}

/**
 * @param entries   这一轮的条目（步骤和运行记录），按时间先后
 * @param startedAt 这一轮开始的时刻（毫秒）；不知道就是 undefined
 */
export function turnPhases(entries: Block[], startedAt?: number): PhaseSpan[] {
  if (!entries.length) return []
  const times = entries.map((b) => at(b.created_at)).filter(Number.isFinite)
  const first = Math.min(...times, startedAt ?? Infinity)
  const last = Math.max(...times)
  const total = Math.max(0, (last - first) / 1000)
  if (!total) return []

  const spent: Record<Phase, number> = { queue: 0, environment: 0, retry: 0, work: 0 }
  for (const b of entries) {
    const from = at(b.created_at)
    let to = Number.NaN
    let phase: Phase | null = null
    switch (eventType(b)) {
      case 'turn_queued':
        phase = 'queue'
        to = startedAt ?? nextStep(entries, from)
        break
      case 'device_waiting':
        phase = 'environment'
        to = b.meta?.state === 'over' ? at(b.meta?.at) : nextStep(entries, from)
        break
      case 'api_retry':
        phase = 'retry'
        to = nextStep(entries, from)
        break
    }
    if (!phase) continue
    if (!Number.isFinite(to)) to = last
    spent[phase] += Math.max(0, (Math.min(to, last) - from) / 1000)
  }
  const waited = spent.queue + spent.environment + spent.retry
  spent.work = Math.max(0, total - waited)
  return ORDER.filter((phase) => spent[phase] >= 1).map((phase) => ({ phase, seconds: Math.round(spent[phase]) }))
}
