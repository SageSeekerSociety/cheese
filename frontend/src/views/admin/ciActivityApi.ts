// 「CI 与合并队列」页（`/admin/ci-activity`）的 HTTP 层与形状。
//
// 和 `ratchetApi.ts` 同一个理由单独一个文件：这一片跟着服务端自己的目录走
// （`app/domain/ci_activity/`）。这一层**一个数都不算**：每个数的口径都在服务端的
// `board.py` 文件头写着，前端只换单位、排版。`null` 是「没有可算的」，不是 0。
import { request } from '@/api'

/** 一组值的概括。`n` 是算进去的样本数；没有样本时其余全是 `null`。 */
export interface CiSummary {
  n: number
  mean: number | null
  p50: number | null
  p90: number | null
  max: number | null
}

export type CiEventClass = 'pr' | 'queue' | 'main' | 'other'

export interface CiSweepStatus {
  at: string
  error: string
  jobs_owed: number
  covered_from: string | null
  /** 运行列表已经回填到窗口起点。`false` 时页面上的数只覆盖 `covered_from` 之后。 */
  complete: boolean
}

export interface CiDaily {
  day: string
  merged: number
  merge_queue_p50: number | null
  merge_queue_p90: number | null
  runner_wait_hosted_p50: number | null
  runner_wait_self_hosted_p50: number | null
  ci_required_pr_p50: number | null
  ci_required_queue_p50: number | null
}

export interface CiHourly {
  hour: string
  busy_mean: number
  busy_peak: number
  pr_mean: number
  queue_mean: number
  main_mean: number
  other_mean: number
  hosted_mean: number
  self_hosted_mean: number
  queue_depth_peak: number
}

export interface CiHourOfDay {
  hour: number
  busy_mean: number | null
  busy_peak: number | null
  pr_mean: number | null
  queue_mean: number | null
  main_mean: number | null
  other_mean: number | null
}

export interface CiRunner {
  runner: string
  kind: 'hosted' | 'self_hosted'
  jobs: number
  failed: number
  busy_hours: number
  /** 只有自托管机器有：忙的时间占窗口的比例。GitHub 托管的是一池机器，没有这个比例。 */
  utilisation: number | null
  wait_p50_s: number | null
  duration_p50_s: number | null
}

export interface CiEjection {
  reason: string
  actor: string
  count: number
}

export interface CiBoard {
  repo: string
  days: number
  since: string
  until: string
  generated_at: string
  sweep: CiSweepStatus | null
  headline: {
    merge_queue_minutes: CiSummary
    runner_wait_seconds: { all: CiSummary; hosted: CiSummary; self_hosted: CiSummary }
    ci_required_minutes: { pull_request: CiSummary; merge_group: CiSummary }
    rounds: {
      prs: number
      pr_rounds: CiSummary
      queue_rounds: CiSummary
      more_than_one: number | null
      histogram: { rounds: number; prs: number }[]
    }
    fail_then_pass: { runs: number; of: number; share: number | null }
    busy_runners: CiSummary & { share: Record<CiEventClass, number | null> }
  }
  daily: CiDaily[]
  hourly: CiHourly[]
  hour_of_day: CiHourOfDay[]
  runners: CiRunner[]
  ejections: CiEjection[]
}

export interface CiJobRow {
  name: string
  workflow: string
  jobs: number
  failed: number
  duration_s: CiSummary
  wait_p50_s: number | null
  busy_hours: number
}

export interface CiStepRow {
  name: string
  number: number | null
  duration_s: CiSummary
}

export function getCiBoard(days: number): Promise<CiBoard> {
  return request<CiBoard>(`/admin/ci-activity?days=${days}`)
}

export function getCiJobs(days: number, runner: string | null): Promise<{ jobs: CiJobRow[] }> {
  const query = new URLSearchParams({ days: String(days) })
  if (runner) query.set('runner', runner)
  return request<{ jobs: CiJobRow[] }>(`/admin/ci-activity/jobs?${query}`)
}

export function getCiSteps(
  days: number,
  job: string,
  runner: string | null
): Promise<{ job: string; steps: CiStepRow[] }> {
  const query = new URLSearchParams({ days: String(days), job })
  if (runner) query.set('runner', runner)
  return request<{ job: string; steps: CiStepRow[] }>(`/admin/ci-activity/steps?${query}`)
}
