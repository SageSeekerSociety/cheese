// Run a background fetch when the browser is idle, not on the critical path.
//
// 有些请求只是为了画一个几秒后才有人看的角标（工作面板的 summary、名册里的工作电脑），
// 但它们一进房间就发，和「把内容画出来」抢同一条网络和同一个主线程。推到首屏之后、
// 浏览器空下来的时候再发，内容先出来，角标随后补上。
//
// `requestIdleCallback` 在主流浏览器里首屏画完之后很快会回调一次；没有它的环境
// （Safari）退回一个 `setTimeout(…, 0)`，行为一样是「让出这一帧」。
type IdleWindow = Window &
  typeof globalThis & {
    requestIdleCallback?: (cb: () => void, options?: { timeout: number }) => number
    cancelIdleCallback?: (handle: number) => void
  }

/** Schedule `task` for the first idle moment; returns a cancel function. */
export function whenIdle(task: () => void, timeout = 1500): () => void {
  const w = window as IdleWindow
  if (typeof w.requestIdleCallback === 'function') {
    const handle = w.requestIdleCallback(() => task(), { timeout })
    return () => w.cancelIdleCallback?.(handle)
  }
  const handle = window.setTimeout(task, 0)
  return () => window.clearTimeout(handle)
}
