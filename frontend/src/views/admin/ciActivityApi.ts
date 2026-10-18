// The CI activity API returns metrics computed by domain/ci_activity/board.py.
// The frontend changes units and presentation only; null means no sample.
import { request } from '@/api'

/** An empty sample has n=0 and null statistics. */
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
  /** Whether run-list backfill reaches the requested window's beginning. */
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
  /** Busy fraction for one self-hosted runner; hosted pools have no fixed capacity. */
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
      covered_from: string | null
      incomplete_prs: number
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

export interface CiJobIdentity {
  name: string
  workflow_path: string
}

export interface CiJobRow extends CiJobIdentity {
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
  job: CiJobIdentity,
  runner: string | null
): Promise<{ job: string; steps: CiStepRow[] }> {
  const query = new URLSearchParams({ days: String(days), job: job.name, workflow: job.workflow_path })
  if (runner) query.set('runner', runner)
  return request<{ job: string; steps: CiStepRow[] }>(`/admin/ci-activity/steps?${query}`)
}
