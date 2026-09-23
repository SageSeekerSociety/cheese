// 资源面板的数字怎么写 (P1-8).
//
// Two of the panel's numbers were not just badly formatted but untruthful: a
// ten-digit token count ran as one unreadable string, and subscription usage —
// which has no per-token price at all — printed as "$0.0000", i.e. "we spent
// nothing" where the truth is "we cannot price this". These helpers live here,
// apart from the component, because they are exactly the rules worth pinning
// with tests.

import type { UsageStats } from '../cx_types'

/** Group a token/turn count: 2532615017 → "2,532,615,017". */
export function fmtNum(n: number): string {
  return Number(n || 0).toLocaleString('en-US')
}

/**
 * A USD amount at a precision that suits its magnitude. A fixed 4 decimals was
 * wrong at both ends — it flattened a real fraction of a cent to $0.0000 and
 * buried the magnitude of four-figure spend in noise.
 */
export function fmtCost(n: number): string {
  const v = Number(n) || 0
  if (v === 0) return '$0'
  const abs = Math.abs(v)
  if (abs < 0.0001) return `${v < 0 ? '-' : ''}<$0.0001`
  if (abs < 1) return `$${v.toFixed(4)}`
  if (abs < 1000) return `$${v.toFixed(2)}`
  return `$${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
}

/**
 * 费用 as the books can honestly state it.
 *
 * Subscription-routed tokens carry no USD price (the plan is billed monthly),
 * so their rows record 0.0 meaning "no price" — never "free". Printing that as
 * $0.0000 over 2.28M tokens is 未知冒充零, which reads as "this cost nothing"
 * and is worse than showing nothing at all.
 */
export function costLabel(u: UsageStats): string {
  if (!u.unpriced_tokens) return fmtCost(u.cost_usd)
  return u.cost_usd > 0 ? `${fmtCost(u.cost_usd)}+` : '未知'
}

/** The sentence that explains an incomplete or unknown 费用 (empty when the
 * figure is complete). */
export function costNote(u: UsageStats): string {
  if (!u.unpriced_tokens) return ''
  const tokens = fmtNum(u.unpriced_tokens)
  return u.cost_usd > 0
    ? `另有 ${tokens} token 走订阅计费，按月付费，不逐 token 计价`
    : `${tokens} token 走订阅计费，按月付费，不逐 token 计价，因此这里不显示金额`
}

/**
 * SI abbreviation: `2.5M`, `1.2G`, `3.4T`. Below 1e3 the full grouped form.
 *
 * The old `shortTokens` helper stopped at M and printed `1e12` as `1000000.0M`.
 * The ladder is 1e3/1e6/1e9/1e12 with k/M/G/T — nothing larger is expressible
 * in the display width we have. NBSP before the prefix so the number and its
 * unit never split across a line break.
 *
 * ALWAYS pair an abbreviated value with `title={fmtNum(n)}`. The abbreviation is
 * a display affordance, not the fact — the fact is the grouped number.
 */
const NBSP = '\u00a0'
const SI_STEPS: Array<[number, string]> = [
  [1e12, 'T'],
  [1e9, 'G'],
  [1e6, 'M'],
  [1e3, 'k'],
]

export function fmtSI(n: number | null | undefined, unit = ''): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '\u2014'
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : ''
  for (const [step, suffix] of SI_STEPS) {
    if (abs >= step) {
      const scaled = abs / step
      // 2.5M / 20k / 250k — one decimal only under 10. Keeps the string under
      // ~6 display chars in every tier.
      const digits = scaled >= 10 ? 0 : 1
      return `${sign}${scaled.toFixed(digits)}${NBSP}${suffix}${unit}`
    }
  }
  return `${sign}${Math.round(abs).toLocaleString('en-US')}${unit ? NBSP + unit : ''}`
}

/**
 * Compact form for a KPI tile: SI from 1e6 up (`2.53M`), the grouped full form
 * below. Same caveat as `fmtSI` — put the full number in `title`.
 *
 * Both locales use the Latin SI suffixes (`k`/`M`/`G`/`T`) rather than 亿/万: an
 * abbreviated number sits in a fixed-width KPI track, and a locale that swaps
 * between `2.53G` and `25.3亿` reflows the tile mid-session. The full digits
 * live in the tooltip in every case, which is where exactness belongs.
 */
export function fmtCompact(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '\u2014'
  return Math.abs(n) >= 1e6 ? fmtSI(n) : fmtNum(n)
}

/**
 * A percentage that never lies about a tiny rate.
 *
 * `3.14159e-7` and `0.000001` both rounded to `0%` under a fixed 2-decimal
 * rule, which is indistinguishable from "nothing happened". Anything above zero
 * but below 0.1% prints `<0.1%`. `0` prints `0%` — a real zero is a different
 * statement from a non-zero we cannot render.
 */
export function fmtPercent(rate: number | null | undefined, digits = 1): string {
  if (rate === null || rate === undefined || !Number.isFinite(rate)) return '\u2014'
  const pct = rate <= 1 && rate >= 0 ? rate * 100 : rate
  if (pct === 0) return '0%'
  if (pct > 0 && pct < 0.1) return '<0.1%'
  if (pct >= 10) return `${Math.round(pct)}%`
  return `${pct.toFixed(digits)}%`
}

/**
 * Latency: `850\u00a0ms`, `1.2\u00a0s`, `2\u00a0min`. Sub-millisecond keeps two
 * significant figures — `0.35 ms` and `0.34 ms` are a real difference at this
 * scale and an integer `0 ms` would erase both.
 */
export function fmtMs(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '\u2014'
  const abs = Math.abs(v)
  if (abs === 0) return `0${NBSP}ms`
  if (abs < 1) return `${v.toPrecision(2)}${NBSP}ms`
  if (abs < 1000) return `${Math.round(v)}${NBSP}ms`
  if (abs < 60_000) return `${(v / 1000).toFixed(1)}${NBSP}s`
  return `${Math.round(v / 60_000)}${NBSP}min`
}

/**
 * The one minutes/hours/days ladder. Three copies of this arithmetic used to
 * live in the dashboard's panel helpers and they had already drifted apart.
 */
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return '\u2014'
  }
  const s = Math.max(0, Math.floor(seconds))
  if (s < 60) return `${s}${NBSP}s`
  if (s < 3600) return `${Math.floor(s / 60)}${NBSP}min`
  if (s < 86400) {
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    return m ? `${h}${NBSP}h${NBSP}${m}${NBSP}min` : `${h}${NBSP}h`
  }
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  return h ? `${d}${NBSP}d${NBSP}${h}${NBSP}h` : `${d}${NBSP}d`
}

/**
 * KPI 环比：`+12%` / `-3.4%` / `±0%` —— 当前窗口对上一等长窗口的百分比差。
 *
 * `prev` 为 0 或缺数时给**空串**（卡片据此不画 delta）：除以零没有答案，
 * 画一个「+∞%」是把「上一周期没有这个数」说成一个鬼故事；`cur` 缺数同理 ——
 * 「没读到」不画，绝不画成「较上期 —%」。`prev=0` 也不画：上一周期是零时
 * 任何百分比都没有意义，那张卡只报当前值。
 *
 * 分档照 `fmtPercent` 的精神：10% 以下留一位小数（3.4 和 3 是真差别），
 * 以上取整（12.34% 的小数位是噪音）。
 */
export function fmtDelta(cur: number | null | undefined, prev: number | null | undefined): string {
  if (cur === null || cur === undefined || prev === null || prev === undefined) return ''
  if (!Number.isFinite(cur) || !Number.isFinite(prev) || prev === 0) return ''
  const pct = ((cur - prev) / Math.abs(prev)) * 100
  if (pct === 0) return '±0%'
  const abs = Math.abs(pct)
  const text = abs >= 10 ? String(Math.round(abs)) : abs.toFixed(1)
  return `${pct > 0 ? '+' : '-'}${text}%`
}
