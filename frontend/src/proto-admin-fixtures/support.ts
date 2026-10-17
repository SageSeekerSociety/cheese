/**
 * proto-admin-fixtures/support.ts — 三份夹具共用的原语：相对时间，以及上游用量数字
 * 的形状。
 *
 * `ago` / `day` 用**读的时候**算，不用固定时刻：预览里「2 小时前」这类字样得跟着现在
 * 走，钉死的话第二天打开就变成「昨天」了。`DAY` 也在这里，两处都用它换算。
 */
import type { GatewayUsageNumbers } from '@/api'

export const DAY = 86400_000

export const ago = (ms: number) => new Date(Date.now() - ms).toISOString()

export const day = (offset: number) => new Date(Date.now() - offset * DAY).toISOString().slice(0, 10)

/** 七个数按上游那几个 token 桶的比例摊出来：prompt 七成、completion 两成、
 *  cache read 一成，`total` 是给的整数。比例是编的，但**必须自洽** —— 三个分项加起
 *  来要是超过 total，页面上那个占比条就会画出界。 */
export function usage(spend: number, requests: number, tokens: number, failed = 0): GatewayUsageNumbers {
  return {
    spend_usd: spend,
    requests,
    failed_requests: failed,
    prompt_tokens: Math.round(tokens * 0.7),
    completion_tokens: Math.round(tokens * 0.2),
    cache_read_tokens: Math.round(tokens * 0.1),
    total_tokens: tokens,
  }
}
