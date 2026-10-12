/**
 * 一条 check run 的结论怎么读 —— 卡面数「未通过」和「已取消」用的就是这一份。
 *
 * 判「这个 PR 的检查过没过」有三个面：后端的合并信号、`/pr-checks` 列表、卡面的
 * 计数。三个面说的是同一件事，词表就只能有一份；前端这一份对应后端的两处
 * （`merge_state.RED_CONCLUSIONS` 和 `github_pr._NO_VERDICT_CONCLUSIONS`），
 * 改这里要连那两处一起改。
 *
 * `cancelled` 哪边都不算：它不是「未通过」——2026-10-12 的 PR #3463 就是把它读成
 * 了红，GitHub 的合并框说 clean、卡上却顶一条红条 ——但它也绝不算通过，它没有结论。
 * 被顶替的旧运行留下的 check run 在后端就丢掉了（`superseded_runs`），走到这里的
 * 「已取消」是真的被取消，照实标出来。
 */

/** 跑完了、结论是失败的那些。 */
export const FAILED_CONCLUSIONS: readonly string[] = ['failure', 'timed_out', 'action_required', 'startup_failure']

/** 跑完了、却没有结论的那些：既不能说通过，也不能说没通过。 */
export const NO_VERDICT_CONCLUSIONS: readonly string[] = ['cancelled', 'stale']

/** 只要 `status` / `conclusion` 两个字段 —— 后端那两个接口给的行都够用。 */
export interface CheckVerdictRow {
  status?: string | null
  conclusion?: string | null
}

export function isFailed(check: CheckVerdictRow): boolean {
  return FAILED_CONCLUSIONS.includes(check.conclusion ?? '')
}

export function isNoVerdict(check: CheckVerdictRow): boolean {
  return NO_VERDICT_CONCLUSIONS.includes(check.conclusion ?? '')
}

/** 卡面展开之后的排序：红的排最前（要人动手），其次是还在跑的和已取消的，
 *  最后才是绿的。没有结论的不插到红的里面冒充红的。 */
export function checkVerdictRank(check: CheckVerdictRow): number {
  if (isFailed(check)) return 0
  if (check.status !== 'completed') return 1
  return isNoVerdict(check) ? 2 : 3
}
