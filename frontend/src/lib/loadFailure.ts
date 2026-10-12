// 「这块内容没读出来」有两种，说的话不一样。
//
// 读失败可以再试一次；401/403 再试多少次都是同一个回答。`BaseLoadError` 画的是
// 第二种时不摆重试按钮，所以调用方得先把两者分开，再把结果传给 `:forbidden`。
//
// 到页面上的错误都来自 `@/api` 的 `ApiError`：`.status` 是数字 HTTP 状态码，
// `.code` 是错误名（字符串），所以判状态码只看 `status`。401 会被拿去刷 token
// 而不是抛出来，所以实际撞上的是 403。
export function isForbidden(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false
  const { status } = error as { status?: unknown }
  return status === 401 || status === 403
}

/** 一个错误给人看的那句话：有 `message` 就用它，否则 `null`（调用方只显示标题）。 */
export function loadFailureReason(error: unknown): string | null {
  if (!error || typeof error !== 'object') return null
  const message = (error as { message?: unknown }).message
  return typeof message === 'string' && message.trim() ? message : null
}
