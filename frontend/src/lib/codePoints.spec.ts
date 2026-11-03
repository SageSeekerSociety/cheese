// 指针那几处的预告写着「前 200 字」：数的是字，不是 UTF-16 下标。
import { expect, it } from 'vitest'

import { firstCodePoints } from './codePoints'

it('够短就原样回来', () => {
  const text = '一二三'
  expect(firstCodePoints(text, 200)).toBe(text)
  // 短的那一份不必再拷一次。
  expect(firstCodePoints(text, 3)).toBe(text)
})

it('数的是字，不是下标：一个 emoji 算一个', () => {
  // 300 个 emoji 在下标上占 600 格；按下标切 200 会把第 100 个劈成半个。
  const clipped = firstCodePoints('😀'.repeat(300), 200)
  expect([...clipped]).toHaveLength(200)
  expect(clipped).not.toMatch(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])/)
})

it('切点正好落在一个 emoji 后面时不多不少', () => {
  expect(firstCodePoints('ab😀cd', 3)).toBe('ab😀')
  expect(firstCodePoints('ab😀cd', 4)).toBe('ab😀c')
})
