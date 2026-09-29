// 命令面板里一条东西对不对得上输入：名字开头 > 名字中间 > 拼音首字母；每个词都要对上。
import { describe, expect, it } from 'vitest'

import { initialsOf, match } from './match'

describe('命令面板的匹配', () => {
  it('名字以输入开头的排在名字中间含有它的前面', () => {
    const head = match('登录', '登录页改成深色')!
    const middle = match('登录', '改一下登录页')!
    expect(head.score).toBeGreaterThan(middle.score)
  })

  it('拼音首字母对得上', () => {
    expect(match('dlyg', '登录页改成深色')).not.toBeNull()
    expect(match('djdy', '搭建第一个原型')).not.toBeNull()
    expect(match('xyz', '登录页改成深色')).toBeNull()
  })

  it('常用多音字两个读音都认', () => {
    expect(match('cmm', '重命名')).not.toBeNull()
    expect(match('zmm', '重命名')).not.toBeNull()
  })

  it('字面对上的排在只有首字母对上的前面', () => {
    expect(match('资料', '资料库')!.score).toBeGreaterThan(match('zl', '资料库')!.score)
  })

  it('几个词都要对上，少一个就不算', () => {
    expect(match('登录 深色', '登录页改成深色')).not.toBeNull()
    expect(match('登录 浅色', '登录页改成深色')).toBeNull()
  })

  it('别名也算', () => {
    expect(match('改名', '重命名', ['改名'])).not.toBeNull()
  })

  it('大小写不论', () => {
    expect(match('bm25', 'BM25 搜索恢复')).not.toBeNull()
  })

  it('英文和数字按原样参与首字母', () => {
    expect(initialsOf('BM25 搜索').join('')).toBe('bm25ss')
  })
})
