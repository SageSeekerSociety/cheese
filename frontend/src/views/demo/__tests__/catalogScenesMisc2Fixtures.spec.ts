/**
 * `../catalogScenesMisc2Fixtures` 里「告警」那六个计数：一屏六张卡，各看各的那个数。
 *
 * 这一份不只预览站用（`AlertsView.spec.ts`、`Overview.spec.ts` 也从它取数），所以它的形状
 * 是别人的输入：六个计数两两不同，一个名字对一个数。这里把这两条钉住 —— 谁让两个名字装
 * 同一个数，或者把某个数挪到别的名字底下，都得先在这里改一次，改得看得见。
 *
 * 两两不同是钉得住的前提：两个桶本来就装同一个数时，把它们对调看上去还是同一份数据，
 * 「一个名字对一个数」这条就指不出错在哪。对调那一次是故意做的变异，证明这组断言会响。
 */
import { expect, it } from 'vitest'

import { ANALYTICS_ALERTS } from '../catalogScenesMisc2Fixtures'

/** 预览站要画的这六个数，一个名字对一个数。 */
const EXPECTED_ALERTS = {
  pendingTaskApprovalCount: 3,
  pendingParticipantApprovalCount: 5,
  pendingSubmissionReviewCount: 2,
  stalledTaskCount: 1,
  overdueUnreviewedSubmissionCount: 4,
  inactivePublisherCount: 0,
}

/** 一组六张卡各自要显示的数，就在这里判。 */
function expectTheAlerts(alerts: Record<string, number>): void {
  expect(alerts).toEqual(EXPECTED_ALERTS)
  // 两两不同：两个格子装同一个数，上面那一比就指不出是哪个装错了。
  expect(new Set(Object.values(alerts)).size).toBe(Object.keys(alerts).length)
}

it('告警的六个计数各自不同，就是预览站要画的那六个', () => {
  expectTheAlerts(ANALYTICS_ALERTS)
})

it('两个桶的数对调，上面那组断言就红', () => {
  // 故意换一次键，证明这组断言真的会响：把两个桶的数挪个位置，钉死的那一份就对不上。
  const swapped = {
    ...ANALYTICS_ALERTS,
    pendingTaskApprovalCount: ANALYTICS_ALERTS.pendingSubmissionReviewCount,
    pendingSubmissionReviewCount: ANALYTICS_ALERTS.pendingTaskApprovalCount,
  }

  expect(() => expectTheAlerts(swapped)).toThrow()
})
