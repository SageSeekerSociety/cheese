// 现场 (the read-only transcript timeline) reads like a chat log, so it has to
// behave like one: newest at the bottom and visible on open, and a single
// enormous entry must not push everything else off the screen.
//
// The length rule lives here rather than in a CSS `line-clamp` alone because
// the template needs the same answer the style does — whether to render the
// 展开 affordance at all. Asking the DOM for a measured height would mean
// reading layout during render; asking the text is deterministic and testable.

import type { Block } from '../cx_types'

import { noticeText } from './noticeText'
import { TOOL_LABELS, toolLabel } from './toolLabels'

import { t } from '@/i18n'

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
 * Whether a rendered body overflows the 12-line clamp budget.
 *
 * The clamp is `max-height`, so it clips whatever is actually tall — including
 * a block-level `<pre>`, which `-webkit-line-clamp` silently let through. The
 * template needs the same answer to decide whether to offer 展开 at all, and a
 * character count cannot give it: 900 characters fit in twelve lines of a wide
 * panel and overflow a narrow one. Measuring the rendered element is the only
 * answer that agrees with what the style does, at every width.
 *
 * `lineHeight` is the body's computed line height in px; a non-positive value
 * (no layout, as in a unit test) means "cannot tell", and the caller falls back
 * to the content heuristic.
 */
export function overflowsClamp(scrollHeight: number, lineHeight: number, lines = SITE_CLAMP_LINES): boolean {
  const budget = lines * lineHeight
  return budget > 0 && scrollHeight > budget + 1
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
  /** 这一轮从开始到最后一条，秒。开始时间不知道时从头一条算；是 0 时组头不显示用时。 */
  seconds: number
}

interface TurnLike {
  id: string
  turn_id?: string | null
  created_at: string
  meta?: NarrationMeta | null
}

/**
 * `starts`：轮次 id → 这一轮开始的时刻（毫秒）。一轮的头一步落在准备和模型第一次
 * 回话之后，从头一步算，「思考中」等的那十几秒就不见了，组头会说这一轮只用了两秒。
 */
export function groupByTurn<T extends TurnLike>(blocks: T[], starts: Record<string, number> = {}): SiteTurn<T>[] {
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
    // 平台自己说的一句（重试、等机器、这一轮失败了）也不是它做的一步：带 `who`
    // 的是平台提示（后端 platform_notices.notice 拼的）。
    turn.steps = turn.entries.filter((b) => !isNarration(b.meta) && b.meta?.who === undefined).length
    // 平台替一轮写的开场那一行落在这一轮登记之前，所以取两者里早的那个。
    const first = Math.min(Date.parse(turn.entries[0].created_at), starts[turn.key] ?? Infinity)
    const last = Date.parse(turn.entries[turn.entries.length - 1].created_at)
    turn.seconds = Number.isFinite(first) && Number.isFinite(last) ? Math.max(0, Math.round((last - first) / 1000)) : 0
  }
  return turns
}

/** 一轮用了多久，写成组头上的那一小截。 */
export function formatSpan(seconds: number): string {
  if (seconds < 60) return t('work.room.site.span.seconds', { s: seconds })
  const minutes = Math.floor(seconds / 60)
  const rest = String(seconds % 60).padStart(2, '0')
  if (minutes < 60) return t('work.room.site.span.minutes', { m: minutes, s: rest })
  return t('work.room.site.span.hours', { h: Math.floor(minutes / 60), m: String(minutes % 60).padStart(2, '0') })
}

// 工具事件那一行：backend stores "verb\npreview"; legacy rows are "🔧 toolname".
// Split into the action verb and an optional argument preview. 现场和卡片详情都按
// 这一份翻译，同一步操作在两处写成同一个词。legacy 行的工具名也查同一张表。

// Meta-first rendering: an event block with structured meta ({tool, arg}) is
// translated at DISPLAY time via the full toolLabels table — so a verb missing
// from the table at write time is never frozen untranslated. Rows without meta
// (pre-meta data) fall back to the baked content text.
// 前端报错不是一次工具调用：正文是一整句「前端报错（页面地址）」，没有「动词\n参数」
// 那道换行。原样当动词，整句就被塞进定宽、不折行的动词列，圆点单独占一行、字从右边
// 溢出去。动词就是「前端报错」，参数是报错本身，和别的步骤同一个形状。
function frontendError(b: Block): boolean {
  return b.meta?.event_type === 'frontend_error'
}

export function eventVerb(b: Block): string {
  if (frontendError(b)) return t('work.room.site.frontendError')
  // as_tool 优先：一次 Bash 调用如果后端认出它其实在读文件，就按「读取文件」显示。
  // tool 仍然如实记着真正跑的是哪个工具。
  if (b.meta?.tool) return toolLabel(b.meta.as_tool ?? b.meta.tool)
  const first = (noticeText(b).split('\n')[0] || '').replace(/^🔧\s*/, '')
  return Object.hasOwn(TOOL_LABELS, first) ? toolLabel(first) : first
}

export function eventArg(b: Block): string {
  if (frontendError(b)) {
    const stack = typeof b.meta?.stack === 'string' ? b.meta.stack : ''
    const page = typeof b.meta?.page === 'string' ? b.meta.page : ''
    return stack.split('\n')[0].trim() || page
  }
  if (b.meta?.tool) return b.meta.arg ?? ''
  const text = noticeText(b)
  const nl = text.indexOf('\n')
  return nl >= 0 ? text.slice(nl + 1).trim() : ''
}

// 这一步挂了没有。后端只在挂了的时候写这个字段，所以「没有」就是「没挂」。
export function eventFailed(b: Block): boolean {
  return b.meta?.failed === true
}

// ---- 参数那一列的省略 ----

/**
 * Beyond this many characters a non-prose argument (a path or a command) is
 * shown with its middle elided. The panel is a narrow side column; at 1280px
 * its argument column fits roughly this many monospace characters. It is a
 * guess about width, which is why the full text stays on `title` and 展开
 * still shows every character.
 */
export const SITE_ARG_MID_CHARS = 36

/**
 * Keep the head and the tail, drop the middle.
 *
 * A path's two ends are what identify it — `backend/app/…/callback.py` — and a
 * command's are the program and its target. An end-ellipsis throws away exactly
 * the half that says *which* file, which is the whole point of the column. The
 * budget is split the way the label asks: 40% head, 40% tail, an ellipsis
 * between them; short enough to stay on one line, long enough to keep both
 * identifying ends.
 *
 * Counted in code points, not UTF-16 units: `slice` cuts between the two halves
 * of a surrogate pair, so a path with an emoji or a rare CJK glyph (𠮷) came
 * back with a lone half that renders as a replacement box.
 */
export function middleTruncate(text: string, max = SITE_ARG_MID_CHARS): string {
  const points = Array.from(text)
  if (points.length <= max) return text
  const keep = Math.max(1, Math.floor(max * 0.4))
  return `${points.slice(0, keep).join('')}…${points.slice(points.length - keep).join('')}`
}

/** 参数里有中文的（文档标题、验收卡标题、一句说明）不走等宽：中文没有等宽字形，
 * 落在等宽字体上会掉到别的字体、字距被拉开。路径和命令照旧等宽。 */
const CJK = /[㐀-鿿豈-﫿]/

/** Whether this step's argument reads as prose (a title, a sentence) rather than
 * a path or a command. The 现场 argument column drops the monospace font for
 * prose (see the template), and elides it from the end rather than the middle. */
export function isProseArg(b: Block): boolean {
  return CJK.test(eventArg(b))
}

/** The collapsed argument column's text. A path or command loses its middle —
 * its two ends are what identify it — while prose is left whole for the CSS
 * end-ellipsis, which cuts a sentence cleanly where a middle ellipsis would cut
 * it in half. 摊开 goes through its own reader, not this. */
export function argDisplay(b: Block): string {
  const arg = eventArg(b)
  return isProseArg(b) ? arg : middleTruncate(arg)
}
