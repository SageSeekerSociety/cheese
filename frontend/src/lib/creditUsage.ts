// 芝士额度页（个人、团队）的数据形状和判断：`/users/me/credits/usage` 与
// `/teams/{id}/credits/usage` 的回答。额度数以点计（1 点 = 0.01 美元），时间窗口只给比例；
// 不含 token。

/** 协作、问答、写作是模型用量，算力是云端沙箱和云虚拟机跑的时间。 */
export type UsageLine = 'collab' | 'ask' | 'write' | 'compute'

export const USAGE_LINES: UsageLine[] = ['collab', 'ask', 'write', 'compute']

/** 词条键逐字写全：i18n 闸门照源码字面量认「这个键有人用」。 */
export const LINE_KEY: Record<UsageLine, string> = {
  collab: 'usage.line.collab',
  ask: 'usage.line.ask',
  write: 'usage.line.write',
  compute: 'usage.line.compute',
}

/** 这份用量分了哪几条线，按固定顺序：个人页四条都有，团队页只有协作和算力。 */
export function linesOf(lines: Partial<Record<UsageLine, number>> | null | undefined): UsageLine[] {
  return lines ? USAGE_LINES.filter((line) => line in lines) : []
}

export interface UsagePeriod {
  unlimited: boolean
  /** 本月方案额度共多少点、用了多少点；不限时为 `null`。 */
  credits_total: number | null
  credits_used: number | null
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
  credits_total: number
  credits_remaining: number
  remaining_ratio: number
  expires_at: string | null
}

export interface UsageDay {
  date: string
  /** 这一天用了多少点；还没到的日子为 `null`。 */
  credits: number | null
  lines?: Partial<Record<UsageLine, number>>
}

export interface UsageProject {
  id: string
  name: string | null
  /** 这个项目本月用了多少点。 */
  credits: number
}

export interface UsageTeam {
  id: number
  name: string
  handle: string
  plan: { key: string; name: string }
  unlimited: boolean
  remaining_ratio: number | null
  /** 按月发放的方案本月还剩多少点；按时间窗口限额或不限时为 `null`，看 `remaining_ratio`。 */
  credits_remaining: number | null
}

/** 方案包含什么：按月发放的点数，或有哪些时间窗口，和能用哪些模型。 */
export interface UsagePlan {
  key: string
  name: string
  unlimited: boolean
  credits_per_period: number | null
  windows: { hours: number | null; calendar: 'week' | 'month' | null; credits: number }[]
  models: string[]
}

export interface CreditUsage {
  plan: UsagePlan
  /** 按月发放的方案（或不限）；按时间窗口限额的方案为 `null`，看 `windows`。 */
  period: UsagePeriod | null
  windows: UsageWindow[]
  packs: UsagePack[]
  days: UsageDay[]
  projects: UsageProject[]
  /** 本月各条线用了多少点：个人页是协作、问答、写作、算力，团队页是协作和算力。 */
  lines: Partial<Record<UsageLine, number>>
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

/** 点数：10 点以下留一位小数，以上取整。 */
export function fmtPoints(n: number, locale: string): string {
  return new Intl.NumberFormat(locale, { maximumFractionDigits: Math.abs(n) < 10 ? 1 : 0 }).format(n)
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
