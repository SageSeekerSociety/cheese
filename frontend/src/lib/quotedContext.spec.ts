/**
 * 交出去的那一份引用长什么样。
 *
 * 这一条守两件事：形状不对的引用不许进对话（房间那头按这份结构读「他指的是哪儿」），
 * 以及冻结之后谁都改不动——它描述的是「当时那一版」，被改过就不再是同一件事了。
 */
import type { PagePinQuote, SlidePageQuote } from './quotedContext'

import { frozenQuote, isQuotedContext } from './quotedContext'

const page: SlidePageQuote = {
  kind: 'slide-page',
  path: 'deck.pptx',
  source: 'committed',
  version: 'v7',
  task_id: 'task',
  page: 3,
  text: '这一段不对',
}

const pin: PagePinQuote = {
  kind: 'page-pin',
  path: 'deck.pdf',
  source: 'live',
  version: 'v7',
  task_id: null,
  page: 1,
  x: 0.42,
  y: 0.17,
}

it('两种形状都收：整页文字和页上的一点', () => {
  expect(isQuotedContext(page)).toBe(true)
  expect(isQuotedContext(pin)).toBe(true)
})

it('共同的部分缺一样就不收：文件身份、版本、页码', () => {
  expect(isQuotedContext({ ...pin, path: undefined })).toBe(false)
  expect(isQuotedContext({ ...pin, version: 7 })).toBe(false)
  expect(isQuotedContext({ ...pin, source: 'working' })).toBe(false)
  expect(isQuotedContext({ ...pin, page: 0 })).toBe(false)
  expect(isQuotedContext({ ...pin, page: 1.5 })).toBe(false)
  expect(isQuotedContext({ ...pin, task_id: 3 })).toBe(false)
})

it('比例是闭区间里的数，别的东西不算', () => {
  expect(isQuotedContext({ ...pin, x: 0, y: 1 })).toBe(true)
  expect(isQuotedContext({ ...pin, x: -0.01 })).toBe(false)
  expect(isQuotedContext({ ...pin, y: 1.01 })).toBe(false)
  expect(isQuotedContext({ ...pin, x: Number.NaN })).toBe(false)
  expect(isQuotedContext({ ...pin, x: undefined })).toBe(false)
})

it('整页那一支少了原文就不收；页上那一点少了比例也不收', () => {
  expect(isQuotedContext({ ...page, text: undefined })).toBe(false)
  expect(isQuotedContext({ ...pin, x: undefined, y: undefined })).toBe(false)
  expect(isQuotedContext({ ...pin, kind: 'region' })).toBe(false)
  expect(isQuotedContext(null)).toBe(false)
  expect(isQuotedContext('quote')).toBe(false)
})

it('冻结之后改不动：这一份说的是当时那一版', () => {
  const frozen = frozenQuote(pin)
  expect(Object.isFrozen(frozen)).toBe(true)
  expect(() => {
    ;(frozen as { version: string }).version = 'v8'
  }).toThrow()
  expect(frozen.version).toBe('v7')
})
