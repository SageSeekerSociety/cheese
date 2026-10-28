/**
 * 一格数字按文件自己的格式排成屏幕上那几个字。
 *
 * 这张表盯的是「排出来的样子读得对」：千分位、小数位、百分数各自按格式走，货币和
 * 「元」这种前后缀留在原位；认不出来的格式一律退回数本身，宁可不排，不能把数改掉。
 */
import { expect, it } from 'vitest'

import { alignmentOf, formatNumber } from './sheetNumberFormat'

it('千分位与小数位按格式走', () => {
  expect(formatNumber(386200, '#,##0')).toBe('386,200')
  expect(formatNumber(1234.5, '#,##0.00')).toBe('1,234.50')
  expect(formatNumber(0, '0.00')).toBe('0.00')
  expect(formatNumber(7, '0')).toBe('7')
})

it('百分数把数乘一百，并且带上那一颗百分号', () => {
  expect(formatNumber(0.041, '0.0%')).toBe('4.1%')
  expect(formatNumber(0.045, '0%')).toBe('5%')
  expect(formatNumber(1.25, '#,##0.0%')).toBe('125.0%')
})

it('前缀和后缀字面量留在原位：货币、单位', () => {
  expect(formatNumber(1200, '¥#,##0')).toBe('¥1,200')
  expect(formatNumber(12, '#,##0.00 元')).toBe('12.00 元')
  expect(formatNumber(1200, '"$"#,##0')).toBe('$1,200')
  // 带币种的方括号写法也是同一个币种符号。
  expect(formatNumber(1200, '[$¥-804]#,##0')).toBe('¥1,200')
})

it('负数按格式自己的那一段排：负号，或者会计的括号', () => {
  expect(formatNumber(-1234, '#,##0')).toBe('-1,234')
  expect(formatNumber(-5, '0.00;[Red](0.00)')).toBe('(5.00)')
})

it('认不出来的格式退回数本身，不猜', () => {
  // General、日期、文本、科学计数法、分数：都不是这里能排的，排错了比不排更坏。
  expect(formatNumber(386200, undefined)).toBeNull()
  expect(formatNumber(386200, 'General')).toBeNull()
  expect(formatNumber(45000, 'yyyy-mm-dd')).toBeNull()
  expect(formatNumber(1.2, '0.00E+00')).toBeNull()
  expect(formatNumber(1.5, '# ?/?')).toBeNull()
  expect(formatNumber(Number.NaN, '#,##0')).toBeNull()
})

it('数字该靠右、别的按文件写的来', () => {
  expect(alignmentOf(undefined, true)).toBe('right')
  expect(alignmentOf(undefined, false)).toBeUndefined()
  expect(alignmentOf('center', false)).toBe('center')
  expect(alignmentOf('left', true)).toBe('left')
  // `justify` 和 `distributed` 在表格里读起来就是铺满一列，靠左排最接近。
  expect(alignmentOf('justify', true)).toBe('left')
})
