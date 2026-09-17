// member-tasks 三个组件共用的那几个「状态词 → 人话」的小函数。
//
// 这些函数是纯 ts，用的是模块级的 `t`（不是 useI18n()）：所以这里不装插件、直接调，
// 顺便把「纯 ts 也能跟着 locale 走」这件事钉住。颜色和计数那几支是数据、不是文案，
// 断言它们跟语言无关，免得以后有人顺手把 'success' 也翻译了。
import dayjs from 'dayjs'
import { afterEach, expect, it, vi } from 'vitest'

import {
  approvalColor,
  approvalText,
  completionColor,
  completionText,
  formatCount,
  formatDate,
  formatDateTime,
  formatDeadline,
  formatPercent,
  identityText,
  visibilityStatusColor,
  visibilityStatusText,
} from './helpers'

import { setLocale } from '@/i18n'

const CJK = /[㐀-䶿一-鿿豈-﫿]/

/** deadline 的四支是按「今天」算出来的，所以用相对天数，别写死日期。 */
const daysFromNow = (days: number) => dayjs().add(days, 'day').startOf('day').valueOf()

afterEach(() => {
  setLocale('zh-CN')
  vi.unstubAllGlobals()
})

it('讲中文：状态词、占位符、截止时间都是中文', () => {
  setLocale('zh-CN')

  expect(approvalText('NONE')).toBe('待审核')
  expect(approvalText('APPROVED')).toBe('已通过')
  expect(approvalText('DISAPPROVED')).toBe('未通过')

  expect(visibilityStatusText('ENDED')).toBe('已结项')
  expect(visibilityStatusText('APPROVED_VISIBLE')).toBe('已通过且可见')
  expect(visibilityStatusText('APPROVED_HIDDEN')).toBe('已通过但隐藏')
  expect(visibilityStatusText('REJECTED')).toBe('已驳回')
  expect(visibilityStatusText('PENDING_APPROVAL')).toBe('待审核')
  // 认不出来的值退回审批中那一句，别抛也别漏字
  expect(visibilityStatusText('WHATEVER' as never)).toBe('待审核')

  expect(completionText('SUCCESS')).toBe('已成功')
  expect(completionText('FAILED')).toBe('未完成')
  expect(completionText('PENDING_REVIEW')).toBe('待评审')
  expect(completionText('REJECTED_RESUBMITTABLE')).toBe('可重提')
  expect(completionText('NOT_SUBMITTED')).toBe('待提交')

  expect(identityText('TEAM')).toBe('团队参与')
  expect(identityText('USER')).toBe('个人参与')

  expect(formatDateTime(null)).toBe('暂无')
  expect(formatDate(undefined)).toBe('暂无')
  expect(formatDeadline(null)).toBe('无截止时间')
})

it('截止时间：昨天、今天、明天、2~6 天、更远，各走各的', () => {
  setLocale('zh-CN')

  const yesterday = dayjs().subtract(1, 'day').startOf('day')
  expect(formatDeadline(yesterday.valueOf())).toBe(`已于 ${yesterday.format('MM-DD')} 截止`)
  expect(formatDeadline(daysFromNow(0))).toBe('今日截止')
  expect(formatDeadline(daysFromNow(1))).toBe('明日截止')
  expect(formatDeadline(daysFromNow(2))).toBe('2 天后截止')
  expect(formatDeadline(daysFromNow(6))).toBe('6 天后截止')

  const far = dayjs().add(30, 'day').startOf('day')
  expect(formatDeadline(far.valueOf())).toBe(`${far.format('MM-DD')} 截止`)
})

it('日期与数字：有值时还是原本那个格式', () => {
  setLocale('zh-CN')

  const stamp = dayjs('2026-03-04T05:06:07').valueOf()
  expect(formatDateTime(stamp)).toBe('2026-03-04 05:06')
  expect(formatDate(stamp)).toBe('2026-03-04')

  // 这几支是数据、不是文案：换语言也不该变
  expect(formatCount(1234)).toBe('1,234')
  expect(formatCount(null)).toBe('0')
  expect(formatPercent(0.5)).toBe('50.0%')
  expect(formatPercent(0.1234, 2)).toBe('12.34%')
  for (const color of [
    approvalColor('NONE'),
    approvalColor('APPROVED'),
    approvalColor('DISAPPROVED'),
    visibilityStatusColor('ENDED'),
    visibilityStatusColor('APPROVED_VISIBLE'),
    visibilityStatusColor('APPROVED_HIDDEN'),
    visibilityStatusColor('REJECTED'),
    visibilityStatusColor('WHATEVER' as never),
    completionColor('SUCCESS'),
    completionColor('FAILED'),
    completionColor('PENDING_REVIEW'),
    completionColor('REJECTED_RESUBMITTABLE'),
    completionColor('NOT_SUBMITTED' as never),
  ]) {
    expect(CJK.test(color), color).toBe(false)
  }
})

it('讲英文：状态词、占位符、截止时间全换掉，一个汉字都不剩', () => {
  setLocale('en')

  const english = [
    approvalText('NONE'),
    approvalText('APPROVED'),
    approvalText('DISAPPROVED'),
    visibilityStatusText('ENDED'),
    visibilityStatusText('APPROVED_VISIBLE'),
    visibilityStatusText('APPROVED_HIDDEN'),
    visibilityStatusText('REJECTED'),
    completionText('SUCCESS'),
    completionText('FAILED'),
    completionText('PENDING_REVIEW'),
    completionText('REJECTED_RESUBMITTABLE'),
    completionText('NOT_SUBMITTED'),
    identityText('USER'),
    formatDateTime(null),
    formatDate(undefined),
    formatDeadline(null),
    formatDeadline(daysFromNow(0)),
    formatDeadline(daysFromNow(1)),
    formatDeadline(daysFromNow(3)),
    formatDeadline(daysFromNow(30)),
    formatPercent(0.5),
  ]

  expect(approvalText('NONE')).toBe('Pending review')
  expect(approvalText('APPROVED')).toBe('Approved')
  expect(approvalText('DISAPPROVED')).toBe('Not approved')
  expect(visibilityStatusText('APPROVED_VISIBLE')).toBe('Approved and visible')
  expect(completionText('PENDING_REVIEW')).toBe('Awaiting review')
  expect(identityText('USER')).toBe('Individual')
  expect(formatDateTime(null)).toBe('N/A')
  expect(formatDeadline(null)).toBe('No deadline')
  expect(formatDeadline(daysFromNow(0))).toBe('Closes today')
  expect(formatDeadline(daysFromNow(1))).toBe('Closes tomorrow')
  expect(formatDeadline(daysFromNow(3))).toBe('Closes in 3 days')
  expect(formatDeadline(daysFromNow(30))).toBe(`Closes ${dayjs().add(30, 'day').format('MM-DD')}`)

  for (const value of english) expect(CJK.test(value), value).toBe(false)
})

it('切一次语言：同一个入参当场给出另一种语言', () => {
  setLocale('zh-CN')
  expect(completionText('SUCCESS')).toBe('已成功')
  expect(formatDate(null)).toBe('暂无')

  setLocale('en')
  expect(completionText('SUCCESS')).toBe('Succeeded')
  expect(formatDate(null)).toBe('N/A')
  expect(CJK.test(completionText('FAILED'))).toBe(false)

  setLocale('zh-CN')
  expect(completionText('SUCCESS')).toBe('已成功')
})

it('团队参与是记了账的欠债：英文下会回落成中文', () => {
  // 术语表 5.4 说 team / 队伍 / 小队 的中文还没统一，所以这一条故意不翻，
  // 挂在 untranslated.json 上。回落行为变了，这条会先红。
  setLocale('en')
  expect(identityText('TEAM')).toBe('团队参与')
  expect(identityText('USER')).toBe('Individual')
})
