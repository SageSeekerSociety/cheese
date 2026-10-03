/**
 * 交出去的那一份引用长什么样。
 *
 * 这一条守两件事：形状不对的引用不许进对话（房间那头按这份结构读「他指的是哪儿」），
 * 以及冻结之后谁都改不动——它描述的是「当时那一版」，被改过就不再是同一件事了。
 */
import type { PagePinQuote, SheetCellQuote, SlidePageQuote, TextRangeQuote } from './quotedContext'

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

const cell: SheetCellQuote = {
  kind: 'sheet-cell',
  path: 'budget.xlsx',
  source: 'committed',
  version: 'v7',
  task_id: null,
  sheet: '预算',
  address: 'B7',
  value: '1200',
}

const range: TextRangeQuote = {
  kind: 'text-range',
  path: '说明.md',
  source: 'live',
  version: 'v7',
  task_id: null,
  text: '失败以后重试 3 次',
  heading: '配置',
  prefix: '退避',
  suffix: '，超过就报错',
}

it('四种形状都收：整页文字、页上的一点、表格一格、正文一段', () => {
  expect(isQuotedContext(page)).toBe(true)
  expect(isQuotedContext(pin)).toBe(true)
  expect(isQuotedContext(cell)).toBe(true)
  expect(isQuotedContext(range)).toBe(true)
})

it('共同的部分缺一样就不收：文件身份、版本、页码', () => {
  expect(isQuotedContext({ ...pin, path: undefined })).toBe(false)
  expect(isQuotedContext({ ...pin, version: 7 })).toBe(false)
  expect(isQuotedContext({ ...pin, source: 'working' })).toBe(false)
  expect(isQuotedContext({ ...pin, page: 0 })).toBe(false)
  expect(isQuotedContext({ ...pin, page: 1.5 })).toBe(false)
  expect(isQuotedContext({ ...pin, task_id: 3 })).toBe(false)
  // 空串：后端那两个字段是 min_length=1，收下去也是 422。这边先拦。
  expect(isQuotedContext({ ...pin, path: '' })).toBe(false)
  expect(isQuotedContext({ ...pin, version: '' })).toBe(false)
  expect(isQuotedContext({ ...page, path: '' })).toBe(false)
  expect(isQuotedContext({ ...page, version: '' })).toBe(false)
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

it('表格一格：地址不能空，工作表名可以是空的（CSV），不需要页码', () => {
  // CSV 没有工作表名，空串是正常形状，不是缺字段。
  expect(isQuotedContext({ ...cell, sheet: '' })).toBe(true)
  expect(isQuotedContext({ ...cell, value: '' })).toBe(true)
  expect(isQuotedContext({ ...cell, address: '' })).toBe(false)
  expect(isQuotedContext({ ...cell, address: undefined })).toBe(false)
  expect(isQuotedContext({ ...cell, sheet: 2 })).toBe(false)
  expect(isQuotedContext({ ...cell, value: undefined })).toBe(false)
  expect(isQuotedContext({ ...cell, kind: 'grid' })).toBe(false)
})

it('正文一段：标题可以是空（文件开头），原文和前后文必须在', () => {
  expect(isQuotedContext({ ...range, heading: null })).toBe(true)
  expect(isQuotedContext({ ...range, heading: '' })).toBe(true)
  expect(isQuotedContext({ ...range, text: undefined })).toBe(false)
  expect(isQuotedContext({ ...range, prefix: undefined })).toBe(false)
  expect(isQuotedContext({ ...range, suffix: 3 })).toBe(false)
  expect(isQuotedContext({ ...range, heading: 5 })).toBe(false)
  expect(isQuotedContext({ ...range, kind: 'document' })).toBe(false)
})

it('文件身份那一半对四种形状一视同仁', () => {
  for (const quote of [page, pin, cell, range]) {
    expect(isQuotedContext({ ...quote, path: '' })).toBe(false)
    expect(isQuotedContext({ ...quote, path: undefined })).toBe(false)
    expect(isQuotedContext({ ...quote, version: '' })).toBe(false)
    expect(isQuotedContext({ ...quote, source: 'working' })).toBe(false)
    expect(isQuotedContext({ ...quote, task_id: 3 })).toBe(false)
  }
})

it('冻结之后改不动：这一份说的是当时那一版', () => {
  const frozen = frozenQuote(pin)
  expect(Object.isFrozen(frozen)).toBe(true)
  expect(() => {
    ;(frozen as { version: string }).version = 'v8'
  }).toThrow()
  expect(frozen.version).toBe('v7')
})
