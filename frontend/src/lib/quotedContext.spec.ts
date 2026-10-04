/**
 * 交出去的那一份引用长什么样。
 *
 * 这一条守两件事：形状不对的引用不许进对话（房间那头按这份结构读「他指的是哪儿」），
 * 以及冻结之后谁都改不动——它描述的是「当时那一版」，被改过就不再是同一件事了。
 */
import type {
  PagePinQuote,
  SheetCellQuote,
  SlidePageQuote,
  TextRangeQuote,
  WebElementQuote,
  WebRegionQuote,
  WebTextQuote,
} from './quotedContext'

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

it('幻灯片选中一段也带前后文：可选，但带上就得是字符串', () => {
  const selection: SlidePageQuote = { ...page, scope: 'selection', prefix: '退避', suffix: '，超过就报错' }
  expect(isQuotedContext(selection)).toBe(true)
  // 整页本来就没有「哪一处」可分，库里更早的整页引用也没有这两个键：都照收。
  expect(isQuotedContext(page)).toBe(true)
  expect(isQuotedContext({ ...page, scope: 'selection' })).toBe(true)
  expect(isQuotedContext({ ...page, prefix: 2 })).toBe(false)
  expect(isQuotedContext({ ...page, suffix: null })).toBe(false)
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
  expect((frozen as { version: string }).version).toBe('v7')
})

const webElement: WebElementQuote = {
  kind: 'web-element',
  path: 'report.html',
  source: 'live',
  version: 'v7',
  task_id: null,
  selector: 'body > main > p:nth-of-type(2)',
  tag: 'p',
  text: '这一句说错了',
  rect: { x: 12.5, y: 40, w: 300, h: 24 },
  viewport: { w: 1024, h: 768 },
}

const webText: WebTextQuote = {
  ...webElement,
  kind: 'web-text',
  text: '只选中这一句',
  prefix: '退避',
  suffix: '，超过就报错',
}

const webRegion: WebRegionQuote = {
  kind: 'web-region',
  url: 'https://app.tunnel.example:8443/dashboard',
  rect: { x: 0, y: 0, w: 200, h: 100 },
  viewport: { w: 800, h: 600 },
}

it('网页预览的三形状都收：点一个元素、选一段文字、圈一块区域', () => {
  expect(isQuotedContext(webElement)).toBe(true)
  expect(isQuotedContext(webText)).toBe(true)
  expect(isQuotedContext(webRegion)).toBe(true)
})

it('网页元素：选择器不能空，标签可空，位置和视口必须在', () => {
  // 元素本身的文字可以是空（图、按钮、占位），但选择器是指回那一处的唯一凭据。
  expect(isQuotedContext({ ...webElement, text: '' })).toBe(true)
  expect(isQuotedContext({ ...webElement, tag: '' })).toBe(true)
  expect(isQuotedContext({ ...webElement, selector: '' })).toBe(false)
  expect(isQuotedContext({ ...webElement, selector: undefined })).toBe(false)
  expect(isQuotedContext({ ...webElement, selector: '甲'.repeat(257) })).toBe(false)
  expect(isQuotedContext({ ...webElement, text: '甲'.repeat(501) })).toBe(false)
  expect(isQuotedContext({ ...webElement, rect: undefined })).toBe(false)
  expect(isQuotedContext({ ...webElement, rect: { x: 0, y: 0, w: -1, h: 1 } })).toBe(false)
  // 像素可以是负的（元素滚到了视口左边、上边），但不能不是数；后端 WebRectIn 同样收
  // 得下负的 x/y，两边一致，不会自己放过一个后端要拒的值。
  expect(isQuotedContext({ ...webElement, rect: { x: -5, y: -5, w: 10, h: 10 } })).toBe(true)
  expect(isQuotedContext({ ...webElement, rect: { x: Number.NaN, y: 0, w: 1, h: 1 } })).toBe(false)
  expect(isQuotedContext({ ...webElement, viewport: { w: 800 } })).toBe(false)
  expect(isQuotedContext({ ...webElement, viewport: { w: 800, h: -1 } })).toBe(false)
  // 视口必须是正的（后端 gt=0）：量出 0 说明这一处还原不出版面，拦下让它落回普通那句话。
  expect(isQuotedContext({ ...webElement, viewport: { w: 800, h: 0 } })).toBe(false)
  expect(isQuotedContext({ ...webElement, viewport: { w: 0, h: 600 } })).toBe(false)
})

it('网页选段：原文不能空，两侧前后文可空但带上了得是字符串', () => {
  expect(isQuotedContext({ ...webText, prefix: '', suffix: '' })).toBe(true)
  expect(isQuotedContext({ ...webText, text: '' })).toBe(false)
  expect(isQuotedContext({ ...webText, text: undefined })).toBe(false)
  expect(isQuotedContext({ ...webText, prefix: undefined })).toBe(false)
  expect(isQuotedContext({ ...webText, suffix: 3 })).toBe(false)
  expect(isQuotedContext({ ...webText, suffix: '乙'.repeat(65) })).toBe(false)
})

it('网页圈选一块区域：只有地址和位置，没有文件身份', () => {
  // 应用没有版本：网址就是它唯一的身份，多塞一份文件身份反而该拦下。
  expect(isQuotedContext({ ...webRegion, url: '' })).toBe(false)
  expect(isQuotedContext({ ...webRegion, url: undefined })).toBe(false)
  expect(isQuotedContext({ ...webRegion, rect: undefined })).toBe(false)
  // 视口量出 0 不是一处能还原的位置，两边都拦（后端 gt=0）。
  expect(isQuotedContext({ ...webRegion, viewport: { w: 0, h: 0 } })).toBe(false)
  expect(isQuotedContext({ ...webRegion, viewport: { w: 0 } })).toBe(false)
  expect(isQuotedContext({ ...webRegion, kind: 'web-page' })).toBe(false)
})

it('网页那两种也走文件身份那一半：路径和版本缺一样就不收', () => {
  for (const quote of [webElement, webText]) {
    expect(isQuotedContext({ ...quote, path: '' })).toBe(false)
    expect(isQuotedContext({ ...quote, path: undefined })).toBe(false)
    expect(isQuotedContext({ ...quote, version: '' })).toBe(false)
    expect(isQuotedContext({ ...quote, source: 'working' })).toBe(false)
    expect(isQuotedContext({ ...quote, task_id: 3 })).toBe(false)
  }
})
