import { describe, expect, it } from 'vitest'

import { filterSlashItems, SLASH_ITEMS } from './docSlashMenu'

function keys(query: string): string[] {
  return filterSlashItems(query).map((i) => i.key)
}

describe('slash 菜单的筛选', () => {
  it('没输东西就是整张表', () => {
    expect(filterSlashItems('')).toEqual(SLASH_ITEMS)
    expect(filterSlashItems('   ')).toEqual(SLASH_ITEMS)
  })

  it('按中文标签认', () => {
    expect(keys('标题')).toEqual(['h1', 'h2', 'h3'])
  })

  it('按英文名认，且不分大小写', () => {
    expect(keys('H1')).toEqual(['h1'])
    expect(keys('quote')).toContain('quote')
  })

  // 打不出中文的时候用拼音是这张表存在关键词的全部理由；写错了不会报错，只会
  // 「打了 bt 出不来标题」，所以这里逐条钉住。
  it('按拼音首字母和全拼都认', () => {
    expect(keys('bt')).toEqual(['h1', 'h2', 'h3'])
    expect(keys('biaoti')).toEqual(['h1', 'h2', 'h3'])
    expect(keys('renwu')).toEqual(['task'])
    expect(keys('fgx')).toEqual(['hr'])
  })

  it('认不出来就是空——菜单收起来，那段「/query」原样留在文档里', () => {
    expect(filterSlashItems('没有这种块')).toEqual([])
  })
})
