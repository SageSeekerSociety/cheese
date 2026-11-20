import { expect, it } from 'vitest'

import { reflowDocPage } from './docReflow'

it('重排那一层放在原文自己的样式后面：同是 !important 时后写的赢', () => {
  const html = '<html><head><style>.page{width:612pt}</style></head><body><div class="page"></div></body></html>'
  const out = reflowDocPage(html)
  const own = out.indexOf('.page{width:612pt}')
  const ours = out.indexOf('data-cheese-reflow')
  expect(own).toBeGreaterThan(-1)
  expect(ours).toBeGreaterThan(own)
  expect(ours).toBeLessThan(out.indexOf('</head>'))
  // 拆掉的是「纸」：页宽、缩放；正文原样留着。
  expect(out).toMatch(/\.page \{[^}]*width: auto !important/)
  expect(out).toMatch(/transform: none !important/)
  expect(out).toContain('<div class="page"></div>')
})

it('没有 head 的那一页也盖得上', () => {
  expect(reflowDocPage('<div class="page">正文</div>').startsWith('<style data-cheese-reflow>')).toBe(true)
})
