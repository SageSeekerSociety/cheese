// 方案与额度页的数据形状（`/admin/plans`、`/admin/teams` 的回答），和由它们推出的判断：
// 哪些数怎么算、哪一档叫什么。只吃数据，不认识接口与界面。

export type PlanAudience = 'personal' | 'team' | 'both'
export type ModelTier = 'included' | 'premium' | 'frontier'

/** 时间窗口：按小时（从团队第一次调用起算）或按周、按月（日历重置），二者给一个。 */
export interface PlanWindow {
  hours?: number
  calendar?: 'week' | 'month'
  credits: number
}

export interface Plan {
  key: string
  name: string
  audience: PlanAudience
  /** 每月发放的额度；按时间窗口限额或不限的方案为 `null`。 */
  credits_per_period: number | null
  period: string
  /** 按时间窗口限额的方案的窗口；按月发放的方案为空。 */
  windows: PlanWindow[]
  /** `null` = 不限档位。 */
  model_tiers: ModelTier[] | null
  unlimited: boolean
  admin_only: boolean
  /** 排序，小的在前：列表按它排，模型选择提示「需要哪个方案」时取排在最前、又能用的那个。 */
  rank: number
  /** 有多少个团队在这个方案上。 */
  team_count: number
  /** 新团队默认挂在这个方案上。 */
  is_default: boolean
}

export type PackSource = 'plan_period' | 'task_earmark' | 'purchase' | 'admin_grant'

export interface CreditPack {
  id: string
  source: PackSource | string
  project_id: string | null
  task_id: number | null
  credits_total: number
  credits_used: number
  period_start: string | null
  expires_at: string | null
  reason: string | null
  created_at: string
  /** 只在团队详情里给：定向额度落在哪个项目、来自哪道题。 */
  project_name?: string | null
  task_name?: string | null
}

/** 本期的方案额度。`credits_total === null`：这一期还没发（本月第一次调用时才发）。 */
export interface CreditPeriod {
  start: string
  credits_total: number | null
  credits_used: number
}

export interface CreditTeamRow {
  id: number
  name: string
  handle: string
  /** 个人团队的主人（handle）；共享团队为 `null`。 */
  personal_owner: string | null
  /** 个人团队主人的昵称；没设或共享团队为 `null`。 */
  personal_owner_nickname: string | null
  /** 共享团队的成员数；个人团队为 `null`。 */
  member_count: number | null
  plan_key: string
  period: CreditPeriod
  packs: CreditPack[]
}

export interface CreditTeamPage {
  items: CreditTeamRow[]
  total: number
  page: number
  page_size: number
}

export interface CreditTeamDetail {
  id: number
  name: string
  handle: string
  personal_owner: string | null
  personal_owner_nickname: string | null
  member_count: number | null
  plan: Plan
  period: CreditPeriod
  packs: CreditPack[]
}

export interface CreditAudit {
  created_at: string
  actor_handle: string
  /** The administrator's nickname now; null when they have none. */
  actor_name?: string | null
  action: string
  target: string
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
}

export interface PlanInput {
  name: string
  audience: PlanAudience
  /** 不限的方案两样都不传。 */
  credits_per_period?: number | null
  windows?: PlanWindow[]
  model_tiers: ModelTier[] | null
  rank: number
}

export interface GrantInput {
  credits: number
  /** 带时区的 ISO 时间；不给 = 不过期。 */
  expires_at?: string | null
  reason?: string | null
}

export const MODEL_TIERS: ModelTier[] = ['included', 'premium', 'frontier']

/** 词条键逐字写全：i18n 闸门照源码字面量认「这个键有人用」。 */
export const TIER_KEY: Record<ModelTier, string> = {
  included: 'credits.tier.included',
  premium: 'credits.tier.premium',
  frontier: 'credits.tier.frontier',
}

export const AUDIENCE_KEY: Record<PlanAudience, string> = {
  personal: 'credits.audience.personal',
  team: 'credits.audience.team',
  both: 'credits.audience.both',
}

/** `model_tiers: null` 是不限档位，等于三档全开。 */
export function planTiers(plan: Pick<Plan, 'model_tiers'>): ModelTier[] {
  return plan.model_tiers === null ? [...MODEL_TIERS] : MODEL_TIERS.filter((tier) => plan.model_tiers?.includes(tier))
}

export type MeterTone = 'ink' | 'warn' | 'danger'

export function meterTone(ratio: number): MeterTone {
  if (ratio >= 1) return 'danger'
  if (ratio >= 0.8) return 'warn'
  return 'ink'
}

/** 本月方案额度那一格画什么：不限、按时间窗口限额（不发月额度）、还没发，或已用多少。 */
export type PeriodUse =
  | { kind: 'unlimited' }
  | { kind: 'windows' }
  | { kind: 'notIssued' }
  | { kind: 'used'; used: number; total: number; ratio: number }

export function periodUse(period: CreditPeriod, plan: Pick<Plan, 'unlimited' | 'windows'> | null): PeriodUse {
  if (plan?.unlimited) return { kind: 'unlimited' }
  if (plan?.windows.length) return { kind: 'windows' }
  if (period.credits_total === null) return { kind: 'notIssued' }
  const total = period.credits_total
  const ratio = total > 0 ? Math.min(1, period.credits_used / total) : 1
  return { kind: 'used', used: period.credits_used, total, ratio }
}

/** 可用余额：手上还能花的额度之和。本月方案额度还没发时，把方案这个月要发的那一份
 *  算进去（第一次调用时就会发）。按时间窗口限额的方案不动用方案额度，只算方案之外的
 *  额度。方案不限时为 `null`。 */
export function availableCredits(
  packs: CreditPack[],
  period: CreditPeriod,
  plan: Pick<Plan, 'unlimited' | 'credits_per_period' | 'windows'> | null
): number | null {
  if (plan?.unlimited) return null
  const windowed = !!plan?.windows.length
  const held = packs
    .filter((pack) => !(windowed && pack.source === 'plan_period'))
    .reduce((sum, pack) => sum + Math.max(0, pack.credits_total - pack.credits_used), 0)
  return period.credits_total === null && !windowed ? held + (plan?.credits_per_period ?? 0) : held
}

export type TeamKind = 'personal' | 'team'

/** 列表和面板里怎么称呼一个团队：个人团队用主人的昵称（没有就用 handle）。 */
export function teamTitle(team: Pick<CreditTeamRow, 'name' | 'personal_owner' | 'personal_owner_nickname'>): string {
  if (!team.personal_owner) return team.name
  return team.personal_owner_nickname || team.personal_owner
}

/** 额度保留到一位小数；整数不带小数点。 */
export function fmtCredits(n: number, locale: string): string {
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(n)
}

export function fmtDate(iso: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, { year: 'numeric', month: 'long', day: 'numeric' }).format(new Date(iso))
}

export function fmtMonth(iso: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, { month: 'long', timeZone: 'UTC' }).format(new Date(iso))
}

export function fmtDateTime(iso: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, {
    month: 'long',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(iso))
}

/** 日期选择器给的是 `YYYY-MM-DD`：到期按那一天本地时间的最后一刻算，发给服务端带时区。 */
export function endOfDayIso(day: string): string {
  return new Date(`${day}T23:59:59`).toISOString()
}
