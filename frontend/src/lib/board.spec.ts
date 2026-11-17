import { beforeEach, describe, expect, it } from 'vitest'

import { columnDotStyle, liveTasks } from './board'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

describe('columnDotStyle', () => {
  it('颜色一律走 token —— 写死的颜色在两个主题里必然错一个', () => {
    for (const column of ['building', 'delivering', 'needs_you', 'done', 'archived'] as const) {
      for (const value of Object.values(columnDotStyle(column))) {
        if (value === 'dashed') continue
        expect(value).toMatch(/^var\(--[a-z0-9-]+\)$/)
      }
    }
  })

  it('只有「待处理」是实心暖色 —— 板上唯一该抓眼睛的一列', () => {
    expect(columnDotStyle('needs_you')).toEqual({ borderColor: 'var(--warn)', background: 'var(--warn)' })
    // 其余没有一个用暖色，不然「该谁动」就没法一眼看出来。
    for (const column of ['building', 'delivering', 'archived'] as const) {
      expect(JSON.stringify(columnDotStyle(column))).not.toContain('--warn')
    }
  })

  it('把颜色关掉也读得出来 —— 三列的形状互不相同', () => {
    const shape = (c: 'building' | 'delivering' | 'needs_you') => {
      const s = columnDotStyle(c)
      return `${s.borderStyle ?? 'solid'}/${s.background ?? 'none'}`
    }
    expect(new Set([shape('building'), shape('delivering'), shape('needs_you')]).size).toBe(3)
  })
})

describe('liveTasks', () => {
  const on = (room_id: string, column: 'building' | 'delivering' | 'needs_you' | 'done') => ({
    room_id,
    presentation: { column },
  })

  it('已归档频道里没走完的任务不列，交付过的照列', () => {
    const tasks = [on('live', 'building'), on('gone', 'needs_you'), on('gone', 'done')]
    expect(liveTasks(tasks, new Set(['gone']))).toEqual([on('live', 'building'), on('gone', 'done')])
  })
})
