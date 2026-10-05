// 「这块内容没读出来」有两种，说的话不一样。
//
// 读失败可以再试一次；401/403 再试多少次都是同一个回答。`BaseLoadError` 画的是
// 第二种时不摆重试按钮，所以调用方得先把两者分开，再把结果传给 `:forbidden`。
//
// 到页面上的错误有两条来路，都带状态码，但字段名不同：
//   - `ApiError`（`@/api`，`.status` 是数字，`.code` 是字符串）—— 新的那批客户端；
//   - `BusinessError`（`@/network/types/error`，`.code` 是数字）—— axios 的拦截器
//     把 403 翻成它（见 `network/Interceptors/responseInterceptorErr.ts`）。
// 401 会被拿去刷 token 而不是抛出来，所以实际撞上的是 403。
export function isForbidden(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false
  const { status, code } = error as { status?: unknown; code?: unknown }
  return status === 401 || status === 403 || code === 401 || code === 403
}

/** 一个错误给人看的那句话：有 `message` 就用它，否则 `null`（调用方只显示标题）。 */
export function loadFailureReason(error: unknown): string | null {
  if (!error || typeof error !== 'object') return null
  const message = (error as { message?: unknown }).message
  return typeof message === 'string' && message.trim() ? message : null
}
