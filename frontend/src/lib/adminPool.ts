// 用量那块看板上「Claude 账号池」一段怎么读。形状在 `api.ts`（`StatsClaudePool`），
// 判据在后端 `platform_stats/claude_pool.py`，这里只做**呈现**：一行账号画成哪句话、
// 底下那句总述怎么写。留在 lib 里而不是组件里，是因为这几条规则值得钉住 —— 尤其
// 「什么时候说最早恢复」「看不见时说什么」。
//
// 这一块的**全部**读数来自代理写在账本旁边的一份快照，所以三种状态要分得开：
//   有行           —— 画行，旧了就注明它有多旧（旧不等于没数据：行上的时刻是绝对的）；
//   没有行 + 代号  —— 画一句「为什么看不见」，这不是错误，开发环境就是如此；
//   没有这一块     —— 旧后端，`claude_accounts` 整个不在（调用方给 null）。
import type { StatsClaudePool } from '@/api'
import type { Trans } from './adminStats'

/** 一行：账号名 + 状态那句话（「冷却中，14:05 恢复 · 连续失败 2 次」）。 */
export interface PoolRow {
  name: string
  state: string
}

export interface PoolView {
  rows: PoolRow[]
  /** 底下那一行：有账号时是总述（「4 张账号：1 张冷却中…，最早 14:05 恢复」），
   *  没有账号时是「为什么看不见」。恒非空 —— 这一段永远不会是一片空白。 */
  summary: string
  /** 这份快照有多旧，只在不新鲜时有值（行照常画，但必须让人知道它是什么时间的）。 */
  stale: string
  /** 一行账号都没有：这一段只画 summary 那一句。 */
  empty: boolean
}

/** 状态 → 文案键。认不出的状态按 `unknown` 画（后端也会替代理先发布的值收一次）。 */
const STATE_KEY: Record<string, string> = {
  available: 'feedback.dashboard.pool.state.available',
  cooling: 'feedback.dashboard.pool.state.cooling',
  disabled: 'feedback.dashboard.pool.state.disabled',
  unknown: 'feedback.dashboard.pool.state.unknown',
}

/** 「为什么看不见」的代号 → 文案。**认不出的代号也给一句话**：后端加了第五种原因时，
 *  界面该说「读不出来」，而不是把代号原样印给人看。 */
const REASON_KEY: Record<string, string> = {
  'not-configured': 'feedback.dashboard.pool.reason.notConfigured',
  missing: 'feedback.dashboard.pool.reason.missing',
  unreadable: 'feedback.dashboard.pool.reason.unreadable',
  malformed: 'feedback.dashboard.pool.reason.malformed',
}

/** `14:05`；隔了天的写 `10/8 14:05`。今天之内还写日期的话，红格子似的一串数字读不出
 *  「还有二十分钟」这层意思；而隔了天的只写时刻会让人以为是今天。 */
function untilText(until: number, locale: string, now: number): string {
  const at = new Date(until * 1000)
  const here = new Date(now)
  const sameDay =
    at.getFullYear() === here.getFullYear() && at.getMonth() === here.getMonth() && at.getDate() === here.getDate()
  const date: Intl.DateTimeFormatOptions = sameDay ? {} : { month: 'numeric', day: 'numeric' }
  return new Intl.DateTimeFormat(locale, { ...date, hour: '2-digit', minute: '2-digit' }).format(at)
}

/** 有多旧。秒和分钟都太细（一眼要看的是「几十分钟还是几小时」），超过一天写天数。 */
function ageText(seconds: number, locale: string): string {
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' })
  if (seconds < 3600) return rtf.format(-Math.max(1, Math.floor(seconds / 60)), 'minute')
  if (seconds < 86400) return rtf.format(-Math.floor(seconds / 3600), 'hour')
  return rtf.format(-Math.floor(seconds / 86400), 'day')
}

/** 一行账号的状态那句话。三段各自可选，用 ` · ` 串起来：
 *  状态（冷却中 / 已停用 / 可用 / 状态未知）+ 解冻时刻 + 连续失败次数。
 *  失败次数为 0 或读不出来就不说 —— 「连续失败 0 次」在「可用」旁边是纯噪音，而
 *  `null` 是「不知道」，画成 0 就是替代理编一个数。 */
function stateText(account: StatsClaudePool['accounts'][number], t: Trans, locale: string, now: number): string {
  const key = STATE_KEY[account.state] ?? STATE_KEY.unknown!
  const cooling = account.state === 'cooling' && account.until !== null
  const head = cooling
    ? t('feedback.dashboard.pool.coolingUntil', { time: untilText(account.until!, locale, now) })
    : t(key)
  const failures =
    account.failures !== null && account.failures > 0
      ? t('feedback.dashboard.pool.failures', { n: account.failures })
      : ''
  return failures ? `${head} · ${failures}` : head
}

/** 底下那句总述。三种状态各数一遍，再补一句最早什么时候能恢复。
 *
 *  「最早恢复」只在有账号冷却时说：`disabled` 是等不来的（要人去重置），有 `disabled`
 *  时补一句「需人工重置」，比一个不会发生的时刻诚实。 */
function summaryText(pool: StatsClaudePool, t: Trans, locale: string, now: number): string {
  const accounts = pool.accounts
  const count = (state: string) => accounts.filter((a) => a.state === state).length
  const available = count('available')
  if (accounts.length === 0) {
    const reason = pool.reason ?? ''
    return t(REASON_KEY[reason] ?? 'feedback.dashboard.pool.reason.unknown')
  }
  if (available === accounts.length) {
    return t('feedback.dashboard.pool.allAvailable', { n: accounts.length })
  }
  const cooling = count('cooling')
  const disabled = count('disabled')
  const unknown = count('unknown')
  const parts = [
    cooling ? t('feedback.dashboard.pool.coolingCount', { n: cooling }) : '',
    disabled ? t('feedback.dashboard.pool.disabledCount', { n: disabled }) : '',
    unknown ? t('feedback.dashboard.pool.unknownCount', { n: unknown }) : '',
  ].filter(Boolean)
  const head =
    cooling === accounts.length
      ? t('feedback.dashboard.pool.allCooling', { n: accounts.length })
      : t('feedback.dashboard.pool.counts', {
          n: accounts.length,
          parts: parts.join(t('feedback.dashboard.pool.separator')),
        })
  // 最早那个解冻时刻直接用行上的绝对时刻现算，不取 `pool.retry_after`：那是服务端在
  // **它那一刻**算的倒计时，页面画的时候离那次请求可能已经过了一会儿。同一个时刻的
  // 两种写法，这里要的是时刻（读的人想知道「几点」）。
  const deadlines = accounts.filter((a) => a.state === 'cooling' && a.until !== null).map((a) => a.until!)
  let tail = ''
  if (cooling && deadlines.length > 0) {
    tail = t('feedback.dashboard.pool.earliest', {
      time: untilText(Math.min(...deadlines), locale, now),
    })
  } else if (disabled) {
    tail = t('feedback.dashboard.pool.needsReset')
  }
  return tail ? `${head}${t('feedback.dashboard.pool.tailSeparator')}${tail}` : head
}

/** 这一段要画的全部。`pool` 是 `null`（旧后端 / 还没到货）时给空的一段：那一屏本来
 *  就自带加载态，这里不自作主张编一句话。 */
export function poolView(
  pool: StatsClaudePool | null | undefined,
  t: Trans,
  locale: string,
  now: number = Date.now()
): PoolView {
  if (!pool) return { rows: [], summary: '', stale: '', empty: true }
  return {
    rows: pool.accounts.map((account) => ({
      name: account.name,
      state: stateText(account, t, locale, now),
    })),
    summary: summaryText(pool, t, locale, now),
    stale:
      pool.stale && pool.age_seconds !== null
        ? t('feedback.dashboard.pool.stale', { age: ageText(pool.age_seconds, locale) })
        : '',
    empty: pool.accounts.length === 0,
  }
}
