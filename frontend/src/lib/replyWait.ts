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

/** 红灯悬停那一句：为什么亮、该谁去动。 */
export function stallReasonText(reason: string | null | undefined, agent: string): string {
  switch (reason) {
    case 'device_waiting':
      return `${agent}的机器够不着，平台在等它回来——多半要有人去把那台设备开机或连上网`
    case 'machine_provisioning':
      return `${agent}的机器创建超过 15 分钟还没好`
    case 'sandbox_rebuilt':
    case 'environment_repaired':
      return `${agent}的运行环境重建超过 15 分钟还没恢复`
    case 'check':
      return `PR 反馈或检查报错超过 5 分钟没有${agent}去处理`
    default:
      return `有人 @ 了${agent}，超过 5 分钟没有回话`
  }
}
