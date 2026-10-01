// AI 队友头像此刻是什么表情（docs/brand.md「AI 队友头像」）。
//
// 一轮在跑时，表情跟着现场顶上那一行走（lib/siteStatus）：在想、在干活、等机器一直
// 持续；重试是卡住了。一轮刚结束的那一下是一次性的：做完了，或者以失败收场就是卡住
// 了，播完头像回到静止。
//
// 按队友算，不按房间算：一间房几个队友并行在干，各有各的表情。同一个队友并行两轮
// 时看最近开始的那一轮。

import type { Block } from '../cx_types'
import type { SiteState, SiteStatus } from './siteStatus'

import { siteStatus, STOPPED } from './siteStatus'

export type FaceState = 'think' | 'work' | 'wait' | 'stuck' | 'done'

export interface AgentFace {
  state: FaceState
  /** 这一轮还在跑：现场顶上那一行此刻说的。刚结束的那一下是 null。 */
  status: SiteStatus | null
}

const FACE: Record<Exclude<SiteState, 'stopped' | 'idle'>, FaceState> = {
  thinking: 'think',
  compacting: 'think',
  acting: 'work',
  waiting: 'wait',
  retrying: 'stuck',
}

/** 一轮刚结束之后，「做完了 / 卡住了」那一下留多久（毫秒），要比动画长。 */
export const FACE_SETTLE_MS = 2400

/**
 * @param blocks  时间线上的块（工具事件和平台提示也在里面，只是不露面）
 * @param starts  在跑的轮次 id → 开始时间（毫秒）
 * @param owners  轮次 id → 那一轮队友的 handle
 * @param ended   队友 handle → 它刚结束的那一轮
 * @returns 队友 handle → 它此刻的表情；没在干活、也没刚干完的队友不在里面
 */
export function agentFaces(
  blocks: Block[],
  starts: Record<string, number>,
  owners: Record<string, string>,
  ended: Record<string, string> = {}
): Record<string, AgentFace> {
  const latest: Record<string, string> = {}
  for (const [id, at] of Object.entries(starts)) {
    const handle = owners[id]
    if (handle && (!(handle in latest) || at > starts[latest[handle]])) latest[handle] = id
  }
  const faces: Record<string, AgentFace> = {}
  for (const [handle, turn] of Object.entries(ended)) {
    const failed = blocks.some((b) => b.turn_id === turn && STOPPED.has(String(b.meta?.event_type ?? '')))
    faces[handle] = { state: failed ? 'stuck' : 'done', status: null }
  }
  for (const [handle, id] of Object.entries(latest)) {
    const status = siteStatus(blocks, true, { [id]: starts[id] })
    if (status.state === 'stopped' || status.state === 'idle') continue
    faces[handle] = { state: FACE[status.state], status }
  }
  return faces
}
