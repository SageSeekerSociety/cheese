// 侧栏红灯：有人 @ 了 AI、或 PR 反馈 / 检查报错落地，等了这么久还没有 AI 出来
// 接，就算「卡住了」。
//
// 后端只给「从什么时候开始等」（`Topic.awaiting_reply_since`），多久算太久在这里
// 判：列表是某一刻读出来的，而这盏灯要跟着当下的钟亮起来，不能等下一次刷新。

/** 等多久算太久。 */
export const REPLY_STALL_MS = 5 * 60_000

/** 从 `since` 等到 `now`，是不是已经等太久了。没有在等（null）就不算。 */
export function replyStalled(since: string | null | undefined, now: number): boolean {
  if (!since) return false
  const at = Date.parse(since)
  if (Number.isNaN(at)) return false
  return now - at >= REPLY_STALL_MS
}
