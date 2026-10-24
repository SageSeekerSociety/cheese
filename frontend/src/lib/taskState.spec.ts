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
    const state = taskState(
      task('running', { progress_state: 'working', progress_note: '在改登录表单', progress_set_at: minutesAgo(12) }),
      ctx
    )
    expect(state.line).toContain('在改登录表单')
    expect(state.line).toContain('已做 12 分钟')
  })

  it('它说过在等、卡住，会话又跑起来了：在做，后面不写那句旧话，也不从那时算已做多久', () => {
    for (const progress_state of ['waiting', 'blocked'] as const) {
      const state = taskState(
        task('running', {
          session_running: true,
          progress_state,
          progress_note: '等 CI 跑完',
          progress_set_at: minutesAgo(25),
        }),
        ctx
      )
      expect(state.label).toBe('在做')
      expect(state.line).not.toContain('等 CI 跑完')
      expect(state.line).not.toContain('25 分钟')
    }
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

  it('它没留话：叫出做它的那位队友，让人打开看看它需要什么', () => {
    expect(taskState(task('blocked', { progress_note: null }), ctx).line).toBe('打开看看小研需要什么')
  })
})

describe('等审', () => {
  const card = { id: 'c1', status: 'pending', since: minutesAgo(30), version: 3, subject: '原型' }

  it('等的是看的人：后面那一行说「等你」', () => {
    const state = taskState(task('awaiting_review', { waiting_on: 'chenmo', needs_my_action: true, card }), ctx)
    expect(state.line).toContain('等你')
    expect(state.line).toContain('第 3 版')
  })

  it('等的是别人：写出是谁', () => {
    const state = taskState(task('awaiting_review', { waiting_on: 'linxiao', card }), ctx)
    expect(state.line).toContain('等 林晓')
    expect(state.line).not.toContain('等你')
  })

  it('卡上缺的几样不写', () => {
    const state = taskState(task('awaiting_review', { waiting_on: 'linxiao', card: { id: 'c1', status: 'x' } }), ctx)
    expect(state.line).toBe('等 林晓')
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
      expect(state.line).not.toContain('等你')
    }
  })

  it('改由别人审阅之后，看板和侧栏写卡上的审阅人，不写任务建时定的那位', () => {
    // 项目级列表和侧栏的行不带 waiting_on，只有卡上的审阅人说得准。
    const row = task('awaiting_review', { reviewer_handle: 'chenmo', card: { ...card, reviewer: 'linxiao' } })
    const state = taskState(row, ctx)
    expect(state.line).toContain('等 林晓')
    expect(state.line).not.toContain('等你')
  })

  it('卡在检查那几步时，标签是待审阅，后面那一行说到了哪一步', () => {
    const running = taskState(task('gate_running', { card }), ctx)
    expect(running.label).toBe('待审阅')
    expect(running.line).toContain('平台在跑检查')
    const failed = taskState(task('checks_failed', { card }), ctx)
    expect(failed.label).toBe('待审阅')
    expect(failed.line).toContain('检查没过')
  })

  it('检查没过时下一步在负责人手上，不说在等谁审阅', () => {
    const state = taskState(task('checks_failed', { reviewer_handle: 'linxiao', card }), ctx)
    expect(state.line).not.toContain('等 林晓')
  })
})

describe('当前行动人和关注人', () => {
  it.each(['awaiting_answer', 'awaiting_review'] as const)('关注提醒不代表要本人执行：%s', (phrase) => {
    const state = taskState(task(phrase, { waiting_on: 'linxiao', awaits_me: true, needs_my_action: false }), ctx)
    expect(state.line).not.toContain('等你')
    expect(state.line).toContain('林晓')
  })

  it('多人问题没有单个人名时，实际被问者仍看到等你', () => {
    const state = taskState(task('awaiting_answer', { waiting_on: null, awaits_me: true, needs_my_action: true }), ctx)
    expect(state.line).toContain('等你')
  })
})

describe('标签只说状态', () => {
  it('每种状态的标签是那两三个字，后面那一行不再重复它', () => {
    const cases: [BoardPhrase, Partial<RoomTask>, string][] = [
      ['running', {}, '在做'],
      ['waiting', {}, '在等'],
      ['blocked', {}, '卡住'],
      ['awaiting_answer', { waiting_on: 'chenmo', needs_my_action: true }, '待回答'],
      ['awaiting_review', { waiting_on: 'linxiao' }, '待审阅'],
      ['bounced', {}, '退回'],
      ['started', {}, '开启'],
    ]
    for (const [phrase, over, label] of cases) {
      const state = taskState(task(phrase, over), ctx)
      expect(state.label).toBe(label)
      expect(state.line).not.toContain(label)
    }
  })

  it('待回答：等的是看的人就说「等你」', () => {
    expect(taskState(task('awaiting_answer', { waiting_on: 'chenmo', needs_my_action: true }), ctx).line).toBe('等你')
    expect(taskState(task('awaiting_answer', { waiting_on: 'linxiao' }), ctx).line).toBe('等 林晓')
  })

  it('最后一步采纳了、任务还开着（在写总结）：还是在做', () => {
    const state = taskState(task('accepted', { status: 'open' }), ctx)
    expect(state.label).toBe('在做')
    expect(state.kind).toBe('running')
  })

  it('关了、交付被采纳：绿色的勾，写「已采纳」；没交付就关的照旧淡色', () => {
    const accepted = taskState(task('accepted', { status: 'closed' }), ctx)
    expect(accepted.label).toBe('已采纳')
    expect(accepted.tone).toBe('okMark')
    const closed = taskState(task('closed', { status: 'closed' }), ctx)
    expect(closed.label).toBe('已关闭')
    expect(closed.tone).toBe('faint')
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
