import { describe, expect, it } from 'vitest'

import { noteTone } from './noteTone'

describe('noteTone', () => {
  const card = (note: string, note_level: 'error' | 'info' | null) => ({ note, note_level })

  it('空 note 不显示，哪怕后端给了码', () => {
    expect(noteTone(card('', 'error'))).toBeNull()
    expect(noteTone(card('   ', 'error'))).toBeNull()
  })

  it('停住了的卡是 error', () => {
    expect(noteTone(card('⚠️ CI 检查未通过：boom', 'error'))).toBe('error')
    // 这一条正是过去漏掉的：`🌿` 不在浏览器那份写死的 emoji 列表里，于是「分叉了、
    // 等人动手」和「还在等检查」渲染成同一个颜色。分级挪到后端之后它自己就对了。
    expect(noteTone(card('🌿 本地分支与 PR 分支已分叉', 'error'))).toBe('error')
  })

  it('还在走的 note 是 info', () => {
    expect(noteTone(card('PR #12 检查全绿，已自动合并', 'info'))).toBe('info')
  })

  it('不认识的码退回 info，而不是去读文案开头那个字符', () => {
    // 前端不再有「哪些 emoji 算严重」这份知识 —— 这正是重构要达到的效果。
    expect(noteTone(card('❌ 部署失败：boom', null))).toBe('info')
  })
})
