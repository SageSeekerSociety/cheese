/** 任务状态的样子：「在做」只在会话真的在跑时动；卡住了没留话也要说一句能做的事；
 *  等审说清待谁审阅；在等说清在等什么、等了多久。 */
import type { BoardPhrase, RoomTask } from '@/cx_types'

import { beforeEach, describe, expect, it } from 'vitest'

import { firstSight, taskState } from './taskState'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

const NOW = Date.parse('2026-10-21T10:00:00Z')
const minutesAgo = (n: number) => new Date(NOW - n * 60_000).toISOString()
const NAMES: Record<string, string> = { linxiao: '林晓', chenmo: '陈默' }
const ctx = { viewer: 'chenmo', agentName: '小研', nameOf: (h: string) => NAMES[h] ?? h, now: NOW }

function task(phrase: BoardPhrase, over: Partial<RoomTask> = {}) {
  return { id: 't1', presentation: { column: 'building' as const, phrase }, ...over }
}

describe('在做', () => {
  it('会话在跑时记号在动', () => {
    expect(taskState(task('running', { session_running: true }), ctx).live).toBe(true)
  })

  it('队友说在做、会话却停着：不动，图标也和在跑的不一样', () => {
    const idle = taskState(task('running', { session_running: false }), ctx)
    const live = taskState(task('running', { session_running: true }), ctx)
    expect(idle.live).toBe(false)
    expect(idle.icon).not.toBe(live.icon)
  })

  it('只有 moving 的行（侧栏、项目级列表）照 moving 判', () => {
    expect(taskState(task('running', { moving: true }), ctx).live).toBe(true)
    expect(taskState(task('running', { moving: false }), ctx).live).toBe(false)
  })

  it('别的状态会话在跑也不动', () => {
    expect(taskState(task('waiting', { session_running: true }), ctx).live).toBe(false)
  })

  it('下面一行是它报的那句和已经做了多久', () => {
    const state = taskState(task('running', { progress_note: '在改登录表单', progress_set_at: minutesAgo(12) }), ctx)
    expect(state.line).toContain('在改登录表单')
    expect(state.line).toContain('已做 12 分钟')
  })
})

describe('在等', () => {
  it('说在等什么、等了多久', () => {
    const state = taskState(task('waiting', { progress_note: '等 CI 跑完', progress_set_at: minutesAgo(25) }), ctx)
    expect(state.line).toContain('等 CI 跑完')
    expect(state.line).toContain('已等 25 分钟')
  })
})

describe('卡住', () => {
  it('它说了要什么，就写它的原话', () => {
    const state = taskState(task('blocked', { progress_note: '需要仓库的写权限' }), ctx)
    expect(state.line).toBe('需要仓库的写权限')
  })

  it('它没留话：说做它的那位队友卡住了，打开看看它需要什么', () => {
    expect(taskState(task('blocked', { progress_note: null }), ctx).line).toBe('小研卡住了，打开看看它需要什么')
  })

  it('不知道队友叫什么时用默认名', () => {
    expect(taskState(task('blocked'), { viewer: 'chenmo' }).line).toBe('芝士卡住了，打开看看它需要什么')
  })
})

describe('等审', () => {
  const card = { id: 'c1', status: 'pending', since: minutesAgo(30), version: 3, subject: '原型' }

  it('等的是看的人：写「待你审阅」', () => {
    const state = taskState(task('awaiting_review', { waiting_on: 'chenmo', card }), ctx)
    expect(state.line).toContain('待你审阅')
    expect(state.line).toContain('第 3 版')
  })

  it('等的是别人：写出是谁', () => {
    const state = taskState(task('awaiting_review', { waiting_on: 'linxiao', card }), ctx)
    expect(state.line).toContain('待 林晓 审阅')
    expect(state.line).not.toContain('待你审阅')
  })

  it('卡上缺的几样不写', () => {
    const state = taskState(task('awaiting_review', { waiting_on: 'linxiao', card: { id: 'c1', status: 'x' } }), ctx)
    expect(state.line).toBe('待 林晓 审阅')
  })

  it('卡还在跑检查、修检查时下一步在平台手上，不说在等谁审阅', () => {
    for (const phrase of [
      'gate_running',
      'awaiting_checks',
      'fixing_checks',
      'resolving_conflict',
      'updating_branch',
    ] as const) {
      const state = taskState(task(phrase, { reviewer_handle: 'chenmo', card: { ...card, reviewer: 'chenmo' } }), ctx)
      expect(state.line).not.toContain('审阅')
    }
  })

  it('改由别人审阅之后，看板和侧栏写卡上的审阅人，不写任务建时定的那位', () => {
    // 项目级列表和侧栏的行不带 waiting_on，只有卡上的审阅人说得准。
    const row = task('awaiting_review', { reviewer_handle: 'chenmo', card: { ...card, reviewer: 'linxiao' } })
    const state = taskState(row, ctx)
    expect(state.line).toContain('待 林晓 审阅')
    expect(state.line).not.toContain('待你审阅')
  })

  it('检查没过时下一步在负责人手上，不说在等谁审阅', () => {
    const state = taskState(task('checks_failed', { reviewer_handle: 'linxiao', card }), ctx)
    expect(state.line).not.toContain('审阅')
  })
})

describe('其余几种', () => {
  it('退回是危险色，不是「等你」', () => {
    expect(taskState(task('bounced'), ctx).tone).toBe('danger')
  })

  it('开启和关闭没有第二行', () => {
    expect(taskState(task('started', { progress_note: '旧的一句' }), ctx).line).toBe('')
    expect(taskState(task('accepted', { progress_note: '旧的一句' }), ctx).line).toBe('')
  })

  it('卡住、待回答第一次出现时闪一下，之后同一件不再闪；它再卡住一次才再闪', () => {
    const first = taskState(task('blocked', { id: 'once', progress_set_at: minutesAgo(5) }), ctx)
    expect(first.attention).not.toBeNull()
    expect(firstSight(first.attention!)).toBe(true)
    const again = taskState(task('blocked', { id: 'once', progress_set_at: minutesAgo(5) }), ctx)
    expect(firstSight(again.attention!)).toBe(false)
    const later = taskState(task('blocked', { id: 'once', progress_set_at: minutesAgo(1) }), ctx)
    expect(firstSight(later.attention!)).toBe(true)
    expect(taskState(task('running'), ctx).attention).toBeNull()
  })
})
