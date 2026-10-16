/**
 * proto-admin-fixtures/ratchet-board.ts — 把上面两份摊成归档里现在的那一块看板。
 *
 * 归档里其实有 90 次采集，这一屏只带回最近 12 个点：`RATCHET_RUNS` 给出这 12 次的提交、
 * 时刻和 CI run 编号，`ratchetPoints` 把每道检查摊到这 12 格上，`RATCHET_BOARD` 是接口
 * 回的那一份。**不碰 GitHub** —— 这一页读的是已经存下来的那些点。
 */
import type { RatchetArea, RatchetBoard, RatchetCheck, RatchetPoint } from '@/views/admin/ratchetApi'

import { RATCHET_CHECKS } from './ratchet-checks'
import { RATCHET_DETAILS } from './ratchet-details'

/** 这 12 次采集的提交、时刻、以及 CI run 的编号 —— 逐字抄自归档，所以时间线上
 *  每个短 sha 点开都是那一次真的 run。 */
const RATCHET_RUNS: [commit: string, at: string, run: string][] = [
  ['c5da2b147ec417f10725ce397682acbec43dad5e', '2026-09-30T17:26:06+00:00', '36750988019'],
  ['f45be47bef9c50b4daa49710fce97946eda7635b', '2026-09-30T18:54:55+00:00', '36761593999'],
  ['9745b40718af7556337c3cac16be80421b811135', '2026-09-30T21:22:19+00:00', '36778710410'],
  ['149615b38727cf6bdf9c16075a9777e7f31069a2', '2026-09-30T23:04:40+00:00', '36788862659'],
  ['9b52e40dff873c79e74eeb3218e5b40425acc292', '2026-10-01T02:49:51+00:00', '36807453565'],
  ['a523012777e4dc398ab3899225a6a293dc18187e', '2026-10-01T04:59:54+00:00', '36817462966'],
  ['83d3efa2e5c9da78be0278d25b664459fc411211', '2026-10-01T06:26:42+00:00', '36824510067'],
  ['87a44b9499b2417d6e1f81826172f4af1fc3f8b4', '2026-10-01T07:16:01+00:00', '36829106324'],
  ['27090463e498923678f0292ae0d0c5ed079d9e81', '2026-10-01T09:53:22+00:00', '36845275654'],
  ['a8b412731db545e6b4d90e5e33b866ed5be017da', '2026-10-01T10:39:18+00:00', '36850157556'],
  ['da6421fce537bc43b179fad52a6b13481618ed1c', '2026-10-01T11:24:18+00:00', '36854813696'],
  ['9467ffb053deddaf0318d7bd29e0019e54c853a7', '2026-10-01T11:48:36+00:00', '36857342568'],
]

/** 换过规则指纹的那两道，换之前的指纹长什么样。两段必须真的不同 —— 走势线是靠相邻
 *  两格的指纹不相等来断开的，写成一样的话线不会断，而页面上看不出来它该断。 */
const RATCHET_OLD_RULE = '0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0'

export const RATCHET_REPO = 'SageSeekerSociety/cheese'
const runUrl = (run: string) => `https://github.com/${RATCHET_REPO}/actions/runs/${run}`

/** 把一份检查摊成 12 个点。明细只挂在最后那一格上：`detailsOf()` 读的就是它，
 *  每一格都挂一遍会让这个文件大出十倍，而页面上一点区别都没有。 */
function ratchetPoints(check: (typeof RATCHET_CHECKS)[number]): RatchetPoint[] {
  const last = check.runs.length - 1
  return check.runs.map(([actual, frozen, stale, ruleChanged, newExemptions], slot) => {
    const [commit, at, run] = RATCHET_RUNS[slot]
    return {
      commit,
      collected_at: at,
      run_url: runUrl(run),
      collection: 'ok',
      status: check.status,
      actual,
      frozen,
      stale_count: stale,
      rule_fingerprint: check.ruleAt >= 0 && slot < check.ruleAt ? RATCHET_OLD_RULE : check.rule_fingerprint,
      rule_changed: ruleChanged,
      new_exemptions: newExemptions,
      details: slot === last ? RATCHET_DETAILS[check.id] ?? [] : [],
      reason: null,
    }
  })
}

/** 分区顺序跟着服务端来：它按采集器登记表的顺序拼，`场景` 在最前。 */
const RATCHET_AREAS: RatchetArea[] = []
for (const raw of RATCHET_CHECKS) {
  const area = RATCHET_AREAS.find((one) => one.area === raw.area)
  const entry: RatchetCheck = {
    id: raw.id,
    area: raw.area,
    better: raw.better,
    direction: raw.direction,
    status: raw.status,
    actual: raw.actual,
    frozen: raw.frozen,
    stale_count: raw.stale_count,
    stale: raw.stale,
    rule_fingerprint: raw.rule_fingerprint,
    tree: raw.tree,
    points: ratchetPoints(raw),
  }
  if (area) area.checks.push(entry)
  else RATCHET_AREAS.push({ area: raw.area, checks: [entry] })
}

const RATCHET_LAST = RATCHET_RUNS[RATCHET_RUNS.length - 1]

/** 归档里现在的样子。**不碰 GitHub** —— 这一页读的是已经存下来的那些点。 */
export const RATCHET_BOARD: RatchetBoard = {
  repo: RATCHET_REPO,
  generated_at: new Date().toISOString(),
  deployed_commit: RATCHET_LAST[0],
  collected_commit: RATCHET_LAST[0],
  collected_at: RATCHET_LAST[1],
  run_url: runUrl(RATCHET_LAST[2]),
  collection: 'ok',
  points: RATCHET_RUNS.length,
  // 归档里其实有 90 次，这一屏只带回最近 12 个点 —— 两个数在页面上各说各的话，
  // 所以假数据里也得是两个不同的数（相等的话那句话读起来永远是「全都在这儿」）。
  total_stored: 90,
  collections: RATCHET_RUNS.map(([commit, at, run]) => ({
    commit,
    collected_at: at,
    run_url: runUrl(run),
    collection: 'ok',
    reason: null,
  })),
  areas: RATCHET_AREAS,
}
