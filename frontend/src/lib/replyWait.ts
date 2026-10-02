// 侧栏上一位成员的红标：房间在等它——它那一轮报错了，或者有人 @ 了它、PR 反馈 /
// 检查报错落地，等了这么久它还没出来接，就算「卡住了」。等的永远是一位成员，不是房间。
//
// 后端只给「在等谁、从什么时候开始、多半为什么还没回」（`Topic.waits`），多久算太久
// 在这里判：列表是某一刻读出来的，而这个标要跟着当下的钟亮起来，不能等下一次刷新。

import type { MemberWait } from '@/lib/memberActivity'

import { t } from '@/i18n'

/** 它那一轮报错了：不用等，立刻算卡住。 */
export const FAILED = 'failed'

/** 等多久算太久。 */
export const REPLY_STALL_MS = 5 * 60_000
/** 机器在创建、环境在重建时平台正在处理，本来就要一阵子，放宽到这么久。 */
export const REPLY_STALL_PREPARING_MS = 15 * 60_000

const PREPARING = new Set(['machine_provisioning', 'sandbox_rebuilt', 'environment_repaired'])

/** 这个原因下等多久算太久。 */
export function stallThreshold(reason: string | null | undefined): number {
  return reason && PREPARING.has(reason) ? REPLY_STALL_PREPARING_MS : REPLY_STALL_MS
}

/** 从 `since` 等到 `now`，是不是已经等太久了。没有在等（null）就不算。 */
export function replyStalled(
  since: string | null | undefined,
  now: number,
  reason: string | null | undefined = null
): boolean {
  if (!since) return false
  const at = Date.parse(since)
  if (Number.isNaN(at)) return false
  return now - at >= stallThreshold(reason)
}

/** 等了多久，说成人读的一截：「3 分钟」「4 小时」「2 天」。 */
export function waitedFor(since: string | null | undefined, now: number): string {
  const at = since ? Date.parse(since) : NaN
  if (Number.isNaN(at)) return ''
  const minutes = Math.max(0, Math.floor((now - at) / 60_000))
  if (minutes < 60) return t('work.sidebar.wait.minutes', { count: Math.max(1, minutes) })
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return t('work.sidebar.wait.hours', { count: hours })
  return t('work.sidebar.wait.days', { count: Math.floor(hours / 24) })
}

export interface StallInfo {
  reason?: string | null
  since?: string | null
  pr?: number | null
}

/** 红灯悬停那一句：哪件事、哪个 PR、已经等了多久、该谁去动。 */
export function stallReasonText(info: StallInfo, agent: string, now: number): string {
  const waited = waitedFor(info.since, now)
  const long = waited ? t('work.sidebar.stall.waited', { waited }) : t('work.sidebar.stall.waitedLong')
  const pr = info.pr ? t('work.sidebar.stall.pr', { pr: info.pr }) : ''
  switch (info.reason) {
    case 'device_waiting':
      return t('work.sidebar.stall.device', { agent, long })
    case 'machine_provisioning':
      return t('work.sidebar.stall.provisioning', { agent, long })
    case 'sandbox_rebuilt':
    case 'environment_repaired':
      return t('work.sidebar.stall.environment', { agent, long })
    case 'check':
      return t('work.sidebar.stall.check', { pr, agent, long })
    case 'conflict':
      return t('work.sidebar.stall.conflict', { pr, agent, long })
    case 'rejected':
      return t('work.sidebar.stall.rejected', { pr, agent, long })
    case 'gate':
      return t('work.sidebar.stall.gate', { pr, agent, long })
    default:
      return t('work.sidebar.stall.mention', { agent, long })
  }
}

/** 这一条等待此刻算不算「卡住了」。 */
export function waitStalled(wait: MemberWait, now: number): boolean {
  return wait.reason === FAILED || replyStalled(wait.since, now, wait.reason)
}

/** 悬停在那位成员的红标上说的那一句。`name` 是被等的那位。 */
export function waitText(wait: MemberWait, name: string, now: number): string {
  if (wait.reason === FAILED) return t('work.sidebar.turnFailed', { agent: name })
  return stallReasonText(wait, name, now)
}
