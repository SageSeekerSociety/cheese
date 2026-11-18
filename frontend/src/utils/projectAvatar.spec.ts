import { describe, expect, it } from 'vitest'

import { fitsTile, isOneEmoji, projectAvatarColor, projectInitials } from './projectAvatar'

describe('默认格子从项目名取的字', () => {
  it('开头是汉字，只取一个字', () => {
    expect(projectInitials('数据结构课程设计')).toBe('数')
  })

  it('跳过开头的括号、标点和空格', () => {
    expect(projectInitials('【期末】操作系统实验')).toBe('期')
    expect(projectInitials('  (draft) Compiler')).toBe('DC')
    expect(projectInitials('“算法”小组')).toBe('算')
    expect(projectInitials('#1 hackathon')).toBe('1H')
  })

  it('拉丁字母的名字：两个词取两个首字母，一个词取前两个字母', () => {
    expect(projectInitials('Web App')).toBe('WA')
    expect(projectInitials('cache lab')).toBe('CL')
    expect(projectInitials('Cheese')).toBe('Ch')
    expect(projectInitials('x')).toBe('X')
  })

  it('只差后一个词的两个项目，格子上的字不一样', () => {
    expect(projectInitials('Lab 1')).not.toBe(projectInitials('Lab 2'))
    expect(projectInitials('CacheLab A')).not.toBe(projectInitials('CacheLab B'))
  })

  it('拉丁字母接汉字，不把汉字拼进两个字母里', () => {
    expect(projectInitials('AI助手')).toBe('AI')
    expect(projectInitials('Web 前端')).toBe('We')
  })

  it('没有字母数字时画第一个字形，什么都没有时画一个点', () => {
    expect(projectInitials('🚀')).toBe('🚀')
    expect(projectInitials('   ')).toBe('·')
    expect(projectInitials(null)).toBe('·')
  })
})

describe('自定文字放得进格子', () => {
  it('最多两个汉字或四个字母数字', () => {
    expect(fitsTile('算法')).toBe(true)
    expect(fitsTile('AB12')).toBe(true)
    expect(fitsTile('数A')).toBe(true)
    expect(fitsTile('数据结构')).toBe(false)
    expect(fitsTile('ABCDE')).toBe(false)
  })

  it('只收字母数字，空的不算', () => {
    expect(fitsTile('A-B')).toBe(false)
    expect(fitsTile('  ')).toBe(false)
  })
})

describe('表情只收一个', () => {
  it('肤色、国旗、组合表情都算一个', () => {
    for (const emoji of ['🚀', '👍🏽', '🇨🇳', '👨‍👩‍👧']) expect(isOneEmoji(emoji)).toBe(true)
  })

  it('两个表情、字母、汉字都不算', () => {
    for (const text of ['🚀🚀', 'A', '中', '']) expect(isOneEmoji(text)).toBe(false)
  })
})

describe('格子底色', () => {
  it('没挑过颜色时跟着项目走，改名不变；挑过就用挑的', () => {
    expect(projectAvatarColor('p-1')).toBe(projectAvatarColor('p-1', null))
    expect(projectAvatarColor('p-1', 200)).toBe(projectAvatarColor('p-2', 200))
    expect(projectAvatarColor('p-1', 200)).not.toBe(projectAvatarColor('p-1', 20))
  })
})
