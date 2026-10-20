// 芝士写一格的样子：参数是一大块一大块到的（模型快的时候一整格一下子就到了），表单要是
// 到多少填多少，人看到的是一格格瞬间换掉，看不出是谁在写、写到了哪。所以到了的参数先攒
// 着，按节奏放出去。
//
// 节奏按格子定，不按总字数：参数是一段 JSON，这里边读边认出眼下在写哪个字段的字。
// - 一格的字按每秒 CHARS_PER_S 个放；字多就加快，短格子最多 SHORT_S 秒、详情这类长格子
//   最多 LONG_S 秒写完——描述再长也不拖，比人自己填快得多。
// - 换到下一格之前停 HOLD_MS：指针滑过去，原来有字的格子先被选中一下，看得出是覆盖；
//   改详情里的一段时，原文先被选中一下，再换上新的。
//   停的时候连这一格的引号都不放，表单上还是原来的样子。
// - 只用来定位的字段（草稿 id、要替换的原文）和 JSON 的括号、键名一下子放完。
// 模型比这慢时就跟着模型走，不为凑节奏等。
//
// 这次调用结束、草稿写进去了，都要等放完再收尾：不然表单会在打到一半时跳成最终的样子。
// 系统要求减少动态效果时不攒，到多少放多少。

const TICK_MS = 40
export const CHARS_PER_S = 60
const SHORT_S = 1
const LONG_S = 2
const HOLD_MS = 220

/** 写起来长的字段：详情、改详情时换上的那段、评分标准。 */
const LONG = new Set(['description', 'replace', 'criteria'])
/** 只用来定位、不往表单上写的字段：一下子放完，也不停。 */
const INSTANT = new Set(['find', 'draft', 'task', 'space', 'version', 'source', 'template'])

export interface Typist {
  /** 一次调用的参数到了这么多（整段，不是增量）。 */
  feed: (call: string, raw: string) => void
  /** 等眼下这些都放完。 */
  idle: () => Promise<void>
  stop: () => void
}

/** 读到一半的 JSON 的状态：在哪一层、在不在字符串里、这个字符串是不是值、属于哪个键。 */
interface Scan {
  stack: ('{' | '[')[]
  /** 对象里下一个字符串是键。 */
  wantKey: boolean
  /** 每一层最近的键（数组那层沿用外层的）。 */
  keys: string[]
  inString: boolean
  escaped: boolean
  isValue: boolean
  key: string
  /** 正在读的键名。 */
  keyText: string
}

function freshScan(): Scan {
  return { stack: [], wantKey: false, keys: [], inString: false, escaped: false, isValue: false, key: '', keyText: '' }
}

/** 从 `from`（字符串里）往后，字符串在哪结束（右引号的位置）；还没到就是 -1。 */
function stringEnd(raw: string, from: number, escapedAtStart: boolean): number {
  let escaped = escapedAtStart
  for (let i = from; i < raw.length; i++) {
    const c = raw[i]
    if (escaped) escaped = false
    else if (c === '\\') escaped = true
    else if (c === '"') return i
  }
  return -1
}

/** `held`：停在哪个字段前面（它的字还没开始放），没停就是 null。 */
export function createTypist(
  show: (call: string, raw: string, held: string | null) => void,
  instant = reducedMotion()
): Typist {
  let call: string | null = null
  let raw = ''
  let shown = 0
  let scan = freshScan()
  let rate = CHARS_PER_S
  let acc = 0
  let holdUntil = 0
  let held: string | null = null
  /** 这一格的字从哪开始（定节奏用）。 */
  let valueStart = 0
  let timer: ReturnType<typeof setInterval> | null = null
  let waiting: (() => void)[] = []

  function settle() {
    if (timer) clearInterval(timer)
    timer = null
    const done = waiting
    waiting = []
    done.forEach((resolve) => resolve())
  }

  /** 读过一个字符（结构里的，或字符串里的），更新状态。 */
  function step(c: string) {
    const s = scan
    if (s.inString) {
      if (s.escaped) s.escaped = false
      else if (c === '\\') s.escaped = true
      else if (c === '"') {
        s.inString = false
        if (!s.isValue) {
          s.keys[s.stack.length - 1] = s.keyText
          s.wantKey = false
        }
      } else if (!s.isValue) s.keyText += c
      return
    }
    switch (c) {
      case '{':
        s.stack.push('{')
        s.wantKey = true
        s.keys[s.stack.length - 1] = s.keys[s.stack.length - 2] ?? ''
        break
      case '[':
        s.stack.push('[')
        s.keys[s.stack.length - 1] = s.keys[s.stack.length - 2] ?? ''
        break
      case '}':
      case ']':
        s.stack.pop()
        s.wantKey = false
        break
      case ',':
        s.wantKey = s.stack[s.stack.length - 1] === '{'
        break
      case '"':
        s.inString = true
        s.escaped = false
        s.isValue = !s.wantKey
        if (s.wantKey) s.keyText = ''
        s.key = s.keys[s.stack.length - 1] ?? ''
        break
    }
  }

  /** 放到下一个要按节奏写的字为止（结构、键名、定位字段一下子放完）；碰到一格的开头就停一下。 */
  function skipStructure(now: number): boolean {
    while (shown < raw.length) {
      const c = raw[shown]
      if (scan.inString) {
        if (scan.isValue && !INSTANT.has(scan.key)) return true
        step(c)
        shown++
        continue
      }
      if (c === '"' && !scan.wantKey && !INSTANT.has(scan.keys[scan.stack.length - 1] ?? '')) {
        // 一格的字要开始了：先停一下，连引号都不放。
        if (holdUntil === 0) {
          const key = scan.keys[scan.stack.length - 1] ?? ''
          // 顶层的一格，或改详情时换上的那段（先选中原文）。
          const hold = scan.stack.length <= 1 || key === 'replace'
          holdUntil = now + (hold ? HOLD_MS : 0)
          if (holdUntil > now) {
            held = key
            return false
          }
        }
        if (now < holdUntil) return false
        holdUntil = 0
        held = null
        step(c)
        shown++
        valueStart = shown
        const end = stringEnd(raw, shown, false)
        const length = (end < 0 ? raw.length : end) - valueStart
        rate = Math.max(CHARS_PER_S, length / (LONG.has(scan.key) ? LONG_S : SHORT_S))
        acc = 0
        return true
      }
      step(c)
      shown++
    }
    return false
  }

  function tick() {
    if (call === null) return settle()
    const now = Date.now()
    if (shown >= raw.length) return settle()
    if (!skipStructure(now)) {
      show(call, raw.slice(0, shown), held)
      if (shown >= raw.length) settle()
      return
    }
    // 在一格的字里：按这一格的速度放。字还在流时，按已经到的长度重新定速。
    const end = stringEnd(raw, shown, scan.escaped)
    const length = (end < 0 ? raw.length : end) - valueStart
    rate = Math.max(rate, length / (LONG.has(scan.key) ? LONG_S : SHORT_S))
    acc += (rate * TICK_MS) / 1000
    let n = Math.floor(acc)
    acc -= n
    while (n > 0 && shown < raw.length && scan.inString) {
      step(raw[shown])
      shown++
      n--
    }
    show(call, raw.slice(0, shown), null)
  }

  function flush() {
    if (call !== null && shown < raw.length) {
      shown = raw.length
      show(call, raw, null)
    }
  }

  return {
    feed(next, text) {
      if (next !== call) {
        flush()
        call = next
        raw = ''
        shown = 0
        scan = freshScan()
        holdUntil = 0
        held = null
      }
      raw = text
      if (instant) {
        shown = raw.length
        show(call, raw, null)
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
