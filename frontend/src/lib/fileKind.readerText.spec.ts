// 读者拿哪一段正文：所有阅读器都从这里取，别各自 `?.head ?? null`。
import { describe, expect, it } from 'vitest'

import { readerText } from './fileKind'

describe('读者看的那一段正文', () => {
  it('后端给了 head 就用它：那是超出编辑器上限时的开头', () => {
    expect(readerText({ head: 'import os\n', content: null })).toBe('import os\n')
  })

  it('没有 head 就退回 content：装得下的文件两者本来就一样', () => {
    // 只带 content 的调用方（和它们的夹具）不少，只认 head 会让它们整片空白。
    expect(readerText({ content: 'print(1)\n' })).toBe('print(1)\n')
  })

  it('head 明说是 null（二进制、或 committed 大文件没读）就什么都不给', () => {
    expect(readerText({ head: null, content: null })).toBeNull()
  })

  it('head 是空串也算给了：那是「是文本，但一个字都没有」，不是「没有开头」', () => {
    expect(readerText({ head: '', content: 'ignored' })).toBe('')
  })

  it('没有这一份文件就是 null', () => {
    expect(readerText(null)).toBeNull()
    expect(readerText(undefined)).toBeNull()
  })
})
