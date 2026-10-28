// 面板页签 → 「在看哪一份文件」：资料库文档的页签不算文件（芝士读不到 `doc:<编号>`）。
import { expect, it } from 'vitest'

import { tabFile } from './viewingContext'

it('文件页签给出路径，文档页签和其他页签给 null', () => {
  expect(tabFile('file:output/a.docx', 'file:', 'doc:')).toBe('output/a.docx')
  expect(tabFile('file:doc:3f2a', 'file:', 'doc:')).toBeNull()
  expect(tabFile('file:', 'file:', 'doc:')).toBeNull()
  expect(tabFile('changes', 'file:', 'doc:')).toBeNull()
})
