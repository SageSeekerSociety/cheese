// 侧栏红灯：有人 @ 了 AI、或 PR 反馈 / 检查报错落地，等了这么久还没有 AI 出来
// 接，就算「卡住了」。
//
// 后端只给「从什么时候开始等」和「多半为什么还没回」（`Topic.awaiting_reply_since`
// / `reply_wait_reason`），多久算太久在这里判：列表是某一刻读出来的，而这盏灯要
// 跟着当下的钟亮起来，不能等下一次刷新。

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
  if (minutes < 60) return `${Math.max(1, minutes)} 分钟`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时`
  return `${Math.floor(hours / 24)} 天`
}

export interface StallInfo {
  reason?: string | null
  since?: string | null
  pr?: number | null
}

/** 红灯悬停那一句：哪件事、哪个 PR、已经等了多久、该谁去动。 */
export function stallReasonText(info: StallInfo, agent: string, now: number): string {
  const waited = waitedFor(info.since, now)
  const long = waited ? `已等 ${waited}` : '等了很久'
  const pr = info.pr ? `PR #${info.pr} ` : ''
  switch (info.reason) {
    case 'device_waiting':
      return `${agent}的机器够不着，${long}——多半要有人去把那台设备开机或连上网`
    case 'machine_provisioning':
      return `${agent}的机器还没创建好，${long}`
    case 'sandbox_rebuilt':
    case 'environment_repaired':
      return `${agent}的运行环境还没恢复，${long}`
    case 'check':
      return `${pr}检查没通过，${long}，没有${agent}在处理`
    case 'conflict':
      return `${pr}有合并冲突，${long}，没有${agent}在处理`
    case 'rejected':
      return `${pr}被退回了，${long}，没有${agent}去改`
    case 'gate':
      return `${pr}质量闸门没过，${long}，没有${agent}去改`
    default:
      return `有人 @ 了${agent}，${long}还没有回话`
  }
}
