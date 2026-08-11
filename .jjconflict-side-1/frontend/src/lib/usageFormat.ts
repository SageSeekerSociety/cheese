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
    ? `另有 ${tokens} token 走订阅计费，按月付费、无单价`
    : `${tokens} token 走订阅计费，按月付费、无单价——不是没花钱，是没有逐 token 的价`
}
