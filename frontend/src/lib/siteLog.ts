// 现场 (the read-only transcript timeline) reads like a chat log, so it has to
// behave like one: newest at the bottom and visible on open, and a single
// enormous entry must not push everything else off the screen.
//
// The length rule lives here rather than in a CSS `line-clamp` alone because
// the template needs the same answer the style does — whether to render the
// 展开 affordance at all. Asking the DOM for a measured height would mean
// reading layout during render; asking the text is deterministic and testable.

/** Collapsed height for a long entry, in lines of the 现场 monospace type. */
export const SITE_CLAMP_LINES = 12

/** Beyond this, an entry is long even if it never wraps a newline. */
const SITE_CLAMP_CHARS = 900

/**
 * Whether this transcript entry should start collapsed.
 *
 * Two ways to be long, and both matter: 芝士 emits both wide prose (few
 * newlines, thousands of characters) and tall command output (many short
 * lines). Testing only one of them lets the other through.
 */
export function isLongSiteEntry(content: string): boolean {
  if (!content) return false
  if (content.length > SITE_CLAMP_CHARS) return true
  return countLines(content) > SITE_CLAMP_LINES
}

/** Lines in the entry, for the "展开全部（N 行）" label. */
export function countLines(content: string): number {
  if (!content) return 0
  return content.split('\n').length
}

/**
 * How many frames the tail-pin may keep re-pinning while the panel is still
 * growing. ~30 frames ≈ 500ms at 60fps, and it stops the moment the height
 * holds steady for one frame — measured, the panel settles in about 120ms.
 */
export const SITE_TAIL_PIN_FRAMES = 30

/**
 * Whether to pin the view to the bottom for another frame.
 *
 * A one-shot `scrollTop = scrollHeight` is not enough, and #327 shipped exactly
 * that. The panel renders its spinner FIRST, so at the moment the scroll runs
 * the container is one viewport tall with nothing to scroll — the assignment
 * clamps to 0 — and the real timeline then lays out underneath, leaving the
 * reader on the oldest entry, which is the bug #327 set out to fix. Measured on
 * the deployed page afterwards: scrollHeight 500 at +40ms, 2066 at +120ms,
 * scrollTop 0 the whole way.
 *
 * So re-pin while the height is still moving, and stop as soon as it isn't.
 * Both bounds matter: without the height check this would fight the reader's
 * own scrolling forever, and without the frame cap a panel that never settles
 * (a live-streaming turn) would pin for the life of the page.
 */
export function shouldKeepPinning(
  height: number,
  lastHeight: number,
  frames: number,
  maxFrames = SITE_TAIL_PIN_FRAMES
): boolean {
  return height !== lastHeight && frames < maxFrames
}

// ---- 按轮分组 ----
//
// 现场是一条平铺的时间线，而干活本来是一轮一轮发生的：一次调用二十个工具，铺
// 成二十条等权的行，读的人看不出哪些是同一件事的经过。每个块都带 `turn_id`
// （人写的块为空），按它把相邻的块收成一组，组头才有地方写这一轮几步、多久。
//
// 相邻才收：一个 `turn_id` 在时间线上本来就是连续的，按 id 建表反而会把中间
// 隔着别的轮次的两段拼到一起，显示出一段从未发生过的连续工作。

interface NarrationMeta {
  [key: string]: unknown
  tool?: string
  progress?: unknown
}

/** 这一行是工具调用，还是芝士自己说的话。 */
export function isNarration(meta?: NarrationMeta | null): boolean {
  if (!meta) return false
  return meta.tool === undefined && meta.progress === true
}

export interface SiteTurn<T> {
  /** 分组键：轮次 id，没有 id 的那些用它们头一条的 id。 */
  key: string
  entries: T[]
  /** 这一组头一条的时间，组头显示它。 */
  startedAt: string
  /** 工具调用的条数 —— 芝士说的话不是「一步」。 */
  steps: number
  /** 首末之差，秒。只有一条时是 0，组头就不显示用时。 */
  seconds: number
}

interface TurnLike {
  id: string
  turn_id?: string | null
  created_at: string
  meta?: NarrationMeta | null
}

export function groupByTurn<T extends TurnLike>(blocks: T[]): SiteTurn<T>[] {
  const turns: SiteTurn<T>[] = []
  for (const block of blocks) {
    const last = turns[turns.length - 1]
    const id = block.turn_id ?? null
    // 没有轮次 id 的块（旧数据、人写的）各自成组：把它们收进上一组，等于声称
    // 它们属于那一轮，而那正是我们不知道的事。
    const sameTurn = last !== undefined && id !== null && last.key === id
    if (sameTurn) {
      last.entries.push(block)
    } else {
      turns.push({ key: id ?? block.id, entries: [block], startedAt: block.created_at, steps: 0, seconds: 0 })
    }
  }
  for (const turn of turns) {
    turn.steps = turn.entries.filter((b) => !isNarration(b.meta)).length
    const first = Date.parse(turn.entries[0].created_at)
    const last = Date.parse(turn.entries[turn.entries.length - 1].created_at)
    turn.seconds = Number.isFinite(first) && Number.isFinite(last) ? Math.max(0, Math.round((last - first) / 1000)) : 0
  }
  return turns
}

/** 一轮用了多久，写成组头上的那一小截。 */
export function formatSpan(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  if (minutes < 60) return `${minutes} 分 ${String(rest).padStart(2, '0')} 秒`
  return `${Math.floor(minutes / 60)} 小时 ${String(minutes % 60).padStart(2, '0')} 分`
}
