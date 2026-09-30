// 分布图的每一行：后端给的是状态码（APPROVED、WITH_REAL_NAME …）时换成人读的名字，
// 分类、年级这种本来就是名字的原样留着 —— 一个叫「APPROVED」的分类不该被改名。
import { describe, expect, it } from 'vitest'

import { labelDistributionCodes } from '../helpers'

import i18n, { setLocale } from '@/i18n'

describe('分布图的行名', () => {
  it('状态码换成名字，别的原样留着', () => {
    setLocale('zh-CN')
    const { t, te } = i18n.global
    const rows = labelDistributionCodes(
      [
        { label: 'WITH_REAL_NAME', count: 2, percentage: 0.5 },
        { label: '计算机视觉', count: 2, percentage: 0.5 },
      ],
      t,
      te
    )
    expect(rows.map((r) => r.label)).toEqual(['已实名', '计算机视觉'])
    expect(rows.map((r) => r.count)).toEqual([2, 2])
  })
})
