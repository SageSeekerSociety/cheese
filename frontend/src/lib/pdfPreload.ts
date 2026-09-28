/**
 * 进房间后趁空把 pdf.js 取下来。
 *
 * 第一次打开 PDF、Word、PPT 预览要先下载 pdf.js 主体和 worker，压缩后约 550 KB
 * （dev 实测 2026-09-27），网络慢时这一段就是人等的那一段。资源是带哈希的长缓存
 * 文件，提前取一次，之后点预览直接从浏览器缓存拿。只在浏览器空闲时取、只取一次，
 * 不和房间自己的加载抢。
 */
let started = false

type IdleWindow = Window & {
  requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number
}

export function preloadPdfViewer(): void {
  if (started || typeof window === 'undefined') return
  started = true
  const run = () => {
    void Promise.all([
      import('pdfjs-dist/legacy/build/pdf.mjs'),
      import('pdfjs-dist/legacy/build/pdf.worker.min.mjs?url').then((m) =>
        // worker 是按地址加载的，import 只拿到地址；取一次让它进 HTTP 缓存。
        fetch(m.default, { credentials: 'same-origin' }).then((r) => r.blob())
      ),
    ]).catch(() => {
      // 预取失败不要紧：真正打开预览时会照常再取，并在那里报错。
      started = false
    })
  }
  const w = window as IdleWindow
  if (w.requestIdleCallback) w.requestIdleCallback(run, { timeout: 5000 })
  else window.setTimeout(run, 2000)
}

/** 测试用：回到还没预取过的状态。 */
export function resetPdfPreload(): void {
  started = false
}
