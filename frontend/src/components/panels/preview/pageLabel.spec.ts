/**
 * 卡上「这一处在哪儿」怎么写。原型写的是 `第 1 页 · 二、时间安排`；认不出节时只能写页码，
 * 不能拿页码冒充节。
 */
import { beforeEach, describe, expect, it } from 'vitest'

import { pageLabel } from './pageLabel'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

describe('页式文档的位置文案', () => {
  it('认得出节时，页码后面跟着节名', () => {
    expect(pageLabel(2, '二、时间安排')).toBe('第 2 页 · 二、时间安排')
  })

  it('认不出来时只写页码，不留一个空的点', () => {
    expect(pageLabel(2)).toBe('第 2 页')
    expect(pageLabel(2, '')).toBe('第 2 页')
    expect(pageLabel(2, '   ')).toBe('第 2 页')
  })
})
