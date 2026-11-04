import { beforeEach, expect, it } from 'vitest'

import { webPickLabel } from './webPickLabel'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

it('网页里圈中的一处有标题就叫标题，没有就按标签说是哪一类东西', () => {
  expect(webPickLabel(' 二、时间安排 ', 'section')).toBe('二、时间安排')
  expect(webPickLabel('', 'p')).toBe('网页里的一处段落')
  expect(webPickLabel(undefined, 'DIV')).toBe('网页里的一处区块')
  expect(webPickLabel('', 'h2')).toBe('网页里的一处标题')
  // 认不出的标签不露出标签名，也不露出选择器。
  expect(webPickLabel('', 'x-widget')).toBe('网页里的一处元素')
})
