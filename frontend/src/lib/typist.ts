// 芝士写一格的样子：参数是一大块一大块到的（模型快的时候一整格一下子就到了），表单要是
// 到多少填多少，人看到的是一格格瞬间换掉，看不出是谁在写、写到了哪。所以到了的参数先攒
// 着，按打字的节奏放出去：平时每秒 CHARS_PER_S 个字；攒得多了就加快，每秒放掉攒下的
// 1 / CATCH_UP_S，一段三千字的详情几秒内放完。
//
// 这次调用结束、草稿写进去了，都要等放完再收尾：不然表单会在打到一半时跳成最终的样子。
// 系统要求减少动态效果时不攒，到多少放多少。

const TICK_MS = 40
const CHARS_PER_S = 90
const CATCH_UP_S = 1.5

export interface Typist {
  /** 一次调用的参数到了这么多（整段，不是增量）。 */
  feed: (call: string, raw: string) => void
  /** 等眼下这些都放完。 */
  idle: () => Promise<void>
  stop: () => void
}

export function createTypist(show: (call: string, raw: string) => void, instant = reducedMotion()): Typist {
  let call: string | null = null
  let raw = ''
  let shown = 0
  let timer: ReturnType<typeof setInterval> | null = null
  let waiting: (() => void)[] = []

  function settle() {
    if (timer) clearInterval(timer)
    timer = null
    const done = waiting
    waiting = []
    done.forEach((resolve) => resolve())
  }

  function tick() {
    if (call === null) return settle()
    const behind = raw.length - shown
    if (behind <= 0) return settle()
    const rate = Math.max(CHARS_PER_S, behind / CATCH_UP_S)
    shown = Math.min(raw.length, shown + Math.max(1, Math.round((rate * TICK_MS) / 1000)))
    show(call, raw.slice(0, shown))
    if (shown >= raw.length) settle()
  }

  function flush() {
    if (call !== null && shown < raw.length) {
      shown = raw.length
      show(call, raw)
    }
  }

  return {
    feed(next, text) {
      if (next !== call) {
        flush()
        call = next
        raw = ''
        shown = 0
      }
      raw = text
      if (instant) {
        shown = raw.length
        show(call, raw)
        return
      }
      if (!timer && shown < raw.length) timer = setInterval(tick, TICK_MS)
    },
    idle() {
      if (!timer) return Promise.resolve()
      return new Promise((resolve) => waiting.push(resolve))
    },
    stop() {
      call = null
      settle()
    },
  }
}

function reducedMotion(): boolean {
  try {
    return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  } catch {
    return false
  }
}
