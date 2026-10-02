// 芝士额度页（个人、团队）的数据形状和判断：`/users/me/credits/usage` 与
// `/teams/{id}/credits/usage` 的回答，全是比例，不含额度数和 token。

export type UsageLine = 'collab' | 'ask' | 'write'

export const USAGE_LINES: UsageLine[] = ['collab', 'ask', 'write']

/** 词条键逐字写全：i18n 闸门照源码字面量认「这个键有人用」。 */
export const LINE_KEY: Record<UsageLine, string> = {
  collab: 'usage.line.collab',
  ask: 'usage.line.ask',
  write: 'usage.line.write',
}

export interface UsagePeriod {
  unlimited: boolean
  /** 本月方案额度用掉的比例；不限或这个方案不按月发时为 `null`。 */
  used_ratio: number | null
  remaining_ratio: number | null
  resets_at: string | null
}

/** 按时间窗口限额的方案里的一个窗口：按小时（从第一次使用起算）或按周、按月。 */
export interface UsageWindow {
  hours: number | null
  calendar: 'week' | 'month' | null
  used_ratio: number
  /** 什么时候清零；按小时的窗口还没开始时为 `null`。 */
  resets_at: string | null
}

export interface UsagePack {
  id: string
  source: string
  project_id: string | null
  task_id: number | null
  project_name: string | null
  task_name: string | null
  remaining_ratio: number
  expires_at: string | null
}

export interface UsageDay {
  date: string
  /** 这一天占本月用量的比例；还没到的日子为 `null`。 */
  share: number | null
  lines?: Record<UsageLine, number>
}

export interface UsageProject {
  id: string
  name: string | null
  /** 这个项目占本月用量的比例，各项目加起来是 1。 */
  share: number
}

export interface UsageTeam {
  id: number
  name: string
  handle: string
  plan: { key: string; name: string }
  unlimited: boolean
  remaining_ratio: number | null
}

export interface CreditUsage {
  plan: { key: string; name: string }
  /** 按月发放的方案（或不限）；按时间窗口限额的方案为 `null`，看 `windows`。 */
  period: UsagePeriod | null
  windows: UsageWindow[]
  packs: UsagePack[]
  days: UsageDay[]
  projects: UsageProject[]
  /** 个人页才有：本月用量按产品线分的比例。 */
  lines?: Record<UsageLine, number>
  /** 个人页才有：我所在的团队。 */
  teams?: UsageTeam[]
}

/** 剩余快见底或已用完时，剩余那个数换成提醒色。 */
export type RemainingTone = 'ok' | 'low' | 'out'

export function remainingTone(remaining: number | null): RemainingTone {
  if (remaining === null) return 'ok'
  if (remaining <= 0) return 'out'
  if (remaining <= 0.2) return 'low'
  return 'ok'
}

export function pct(ratio: number): string {
  return `${Math.round(ratio * 100)}%`
}

export function fmtMonthDay(iso: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, { month: 'long', day: 'numeric' }).format(new Date(iso))
}

export function fmtResetAt(iso: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, { month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(
    new Date(iso)
  )
}

/** 本月是几月：从这个月第一天的日期取。 */
export function fmtMonth(days: UsageDay[], locale: string): string {
  const first = days[0]?.date
  if (!first) return ''
  return new Intl.DateTimeFormat(locale, { month: 'long', timeZone: 'UTC' }).format(new Date(`${first}T00:00:00Z`))
}

/** 拒绝原因是额度不够（本月用完、窗口满了……）：这时给一个「查看用量」的去处。
 *  认的是错误体里 `error.i18n` 的键，和后端拒绝时说的那几句一一对应。 */
const CREDIT_REFUSALS = new Set(['creditsMonthSpent', 'creditsSpent', 'creditsWindowFull', 'creditsRefused'])

export function isCreditRefusal(body: unknown): boolean {
  const key = (body as { error?: { i18n?: { key?: unknown } } } | null)?.error?.i18n?.key
  return typeof key === 'string' && CREDIT_REFUSALS.has(key)
}
