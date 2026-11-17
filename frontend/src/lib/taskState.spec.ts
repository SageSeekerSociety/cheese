/** 任务状态的样子：每件任务只显示七个状态词之一；进行中只在会话真的在跑时动；
 *  待协助没留话也说一句；待审阅说清等谁；在等结果说清在等什么、等了多久。 */
import type { BoardPhrase, RoomTask } from '@/cx_types'

import { beforeEach, describe, expect, it } from 'vitest'

import { firstSight, taskState, taskStatus } from './taskState'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

const NOW = Date.parse('2026-10-21T10:00:00Z')
const minutesAgo = (n: number) => new Date(NOW - n * 60_000).toISOString()
const NAMES: Record<string, string> = { linxiao: '林晓', chenmo: '陈默' }
const ctx = { viewer: 'chenmo', agentName: '小研', nameOf: (h: string) => NAMES[h] ?? h, now: NOW }

function task(phrase: BoardPhrase, over: Partial<RoomTask> = {}) {
  return { id: 't1', presentation: { column: 'building' as const, phrase }, ...over }
}

describe('进行中', () => {
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
      expect(state.label).toBe('进行中')
      expect(state.line).not.toContain('等 CI 跑完')
      expect(state.line).not.toContain('25 分钟')
    }
  })
})

describe('进行中，在等结果', () => {
  it('说在等什么、等了多久', () => {
    const state = taskState(task('waiting', { progress_note: '等 CI 跑完', progress_set_at: minutesAgo(25) }), ctx)
    expect(state.line).toContain('等 CI 跑完')
    expect(state.line).toContain('已等 25 分钟')
  })
})

describe('待协助', () => {
  it('它说了要什么，就写它的原话', () => {
    const state = taskState(task('blocked', { progress_note: '需要仓库的写权限' }), ctx)
    expect(state.line).toBe('需要仓库的写权限')
  })

  it('它没留话：叫出做它的那位队友', () => {
    expect(taskState(task('blocked', { progress_note: null }), ctx).line).toContain('小研')
  })
})

describe('待审阅', () => {
  const card = { id: 'c1', status: 'pending', since: minutesAgo(30), version: 3, subject: '原型' }

  it('等的是看的人：后面那一行说「等你」', () => {
    const state = taskState(task('awaiting_review', { waiting_on: 'chenmo', needs_my_action: true, card }), ctx)
    expect(state.line).toContain('等你')
    expect(state.line).toContain('第 3 版')
  })

  it('等的是别人：写出是谁', () => {
    const state = taskState(task('awaiting_review', { waiting_on: 'linxiao', card }), ctx)
    expect(state.line).toContain('等林晓')
    expect(state.line).not.toContain('等你')
  })

  it('卡上缺的几样不写', () => {
    const state = taskState(task('awaiting_review', { waiting_on: 'linxiao', card: { id: 'c1', status: 'x' } }), ctx)
    expect(state.line).toBe('等林晓')
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
    expect(state.line).toContain('等林晓')
    expect(state.line).not.toContain('等你')
  })

  it('卡在检查那几步时，状态词是待审阅，下面那一行说到了哪一步', () => {
    const running = taskState(task('gate_running', { card }), ctx)
    expect(running.label).toBe('待审阅')
    expect(running.line).toContain('检查运行中')
    const failed = taskState(task('checks_failed', { card }), ctx)
    expect(failed.label).toBe('待审阅')
    expect(failed.line).toContain('检查未通过')
  })

  it('下面那一行的几段用逗号连起来，不用「·」', () => {
    const state = taskState(task('awaiting_review', { waiting_on: 'linxiao', card }), ctx)
    expect(state.line).toContain('，')
    expect(state.line).not.toContain('·')
  })

  it('检查没过时下一步在负责人手上，不说在等谁审阅', () => {
    const state = taskState(task('checks_failed', { reviewer_handle: 'linxiao', card }), ctx)
    expect(state.line).not.toContain('等林晓')
  })

  it('检查没过、芝士清不掉：写等负责人，是看的人就写等你', () => {
    const theirs = taskState(task('checks_failed', { owner_handle: 'linxiao', card }), ctx)
    expect(theirs.line).toContain('检查未通过，等林晓')
    const mine = taskState(task('checks_failed', { owner_handle: 'chenmo', needs_my_action: true, card }), ctx)
    expect(mine.line).toContain('检查未通过，等你')
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

describe('七个状态词', () => {
  it('每个后端码归到七个词中的一个', () => {
    const cases: [BoardPhrase, string][] = [
      ['started', 'not_started'],
      ['running', 'in_progress'],
      ['waiting', 'in_progress'],
      ['bounced', 'in_progress'],
      ['awaiting_answer', 'awaiting_answer'],
      ['blocked', 'needs_help'],
      ['gate_running', 'in_review'],
      ['awaiting_checks', 'in_review'],
      ['fixing_checks', 'in_review'],
      ['resolving_conflict', 'in_review'],
      ['updating_branch', 'in_review'],
      ['checks_failed', 'in_review'],
      ['awaiting_review', 'in_review'],
      ['accepted', 'done'],
      ['completed', 'done'],
      ['closed', 'closed'],
    ]
    for (const [phrase, status] of cases) expect(taskStatus(phrase)).toBe(status)
  })

  it('状态词是那七个之一，下面那一行不再重复它', () => {
    const words = ['未开始', '进行中', '待回答', '待协助', '待审阅', '已完成', '已关闭']
    const cases: [BoardPhrase, Partial<RoomTask>, string][] = [
      ['running', {}, '进行中'],
      ['waiting', {}, '进行中'],
      ['blocked', {}, '待协助'],
      ['awaiting_answer', { waiting_on: 'chenmo', needs_my_action: true }, '待回答'],
      ['awaiting_review', { waiting_on: 'linxiao' }, '待审阅'],
      ['bounced', {}, '进行中'],
      ['started', {}, '未开始'],
      ['completed', { status: 'closed' }, '已完成'],
      ['closed', { status: 'closed' }, '已关闭'],
    ]
    for (const [phrase, over, label] of cases) {
      const state = taskState(task(phrase, over), ctx)
      expect(words).toContain(state.label)
      expect(state.label).toBe(label)
      expect(state.line).not.toContain(label)
    }
  })

  it('待回答：等的是看的人就说「等你」', () => {
    expect(taskState(task('awaiting_answer', { waiting_on: 'chenmo', needs_my_action: true }), ctx).line).toBe('等你')
    expect(taskState(task('awaiting_answer', { waiting_on: 'linxiao' }), ctx).line).toBe('等林晓')
  })

  it('被退回、平台把它放回进行中：写谁退回的，修改中', () => {
    const state = taskState(
      task('running', { progress_state: 'working', card: { id: 'c1', status: 'rejected', decided_by: 'linxiao' } }),
      ctx
    )
    expect(state.label).toBe('进行中')
    expect(state.line).toBe('林晓已退回，修改中')
  })

  it('被退回之后没人在改：写谁退回的，不说修改中', () => {
    const state = taskState(task('bounced', { card: { id: 'c1', status: 'rejected', decided_by: 'linxiao' } }), ctx)
    expect(state.label).toBe('进行中')
    expect(state.line).toBe('林晓已退回')
  })

  it('队友退回后说了自己在改什么：写它的原话', () => {
    const state = taskState(
      task('running', {
        progress_state: 'working',
        progress_note: '按意见改标题',
        card: { id: 'c1', status: 'rejected', decided_by: 'linxiao' },
      }),
      ctx
    )
    expect(state.line).toContain('按意见改标题')
  })

  it('最后一步采纳了、任务还开着（在写总结）：已完成', () => {
    expect(taskState(task('accepted', { status: 'open' }), ctx).label).toBe('已完成')
  })

  it('交付被采纳：绿色的勾，写「已完成」和谁采纳的；没做成就关的照旧淡色，写「已关闭」', () => {
    const accepted = taskState(
      task('accepted', { status: 'closed', card: { id: 'c1', status: 'accepted', decided_by: 'linxiao' } }),
      ctx
    )
    expect(accepted.label).toBe('已完成')
    expect(accepted.line).toBe('林晓已采纳')
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

  it('未开始没有第二行，没人采纳的已完成也没有', () => {
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

describe('在做：进度很久没变 / 可能已断线', () => {
  const working = (over: Partial<RoomTask>) =>
    task('running', { progress_state: 'working', progress_note: '在跑 e2e 全套', ...over })

  it('会话在跑、那一句话 30 分钟内更新：不算很久没变', () => {
    const s = taskState(working({ session_running: true, progress_set_at: minutesAgo(29) }), ctx)
    expect(s.stale).toBe(false)
    expect(s.live).toBe(true)
  })

  it('会话在跑、那一句话超过 30 分钟没变：橙点、图标不转', () => {
    const s = taskState(working({ session_running: true, progress_set_at: minutesAgo(45) }), ctx)
    expect(s.stale).toBe(true)
    expect(s.detail).toContain('进度很久没变')
    expect(s.detail).toContain('在跑 e2e 全套')
  })

  it('会话停着、15 分钟内：只是静止的钟，不算断线', () => {
    const s = taskState(working({ session_running: false, progress_set_at: minutesAgo(10) }), ctx)
    expect(s.offline).toBe(false)
    expect(s.icon).toBe('mdi-progress-clock')
  })

  it('会话停着、超过 15 分钟没回来：可能已断线', () => {
    const s = taskState(working({ session_running: false, progress_set_at: minutesAgo(20) }), ctx)
    expect(s.offline).toBe(true)
    expect(s.label).toBe('进行中')
    expect(s.detail).toContain('可能已断线')
    expect(s.icon).toBe('mdi-cloud-off-outline')
    expect(s.tone).toBe('danger')
  })

  it('队友已离开：立即算可能已断线', () => {
    const s = taskState(
      working({ session_running: false, progress_set_at: minutesAgo(2), agent_left_at: minutesAgo(1) }),
      ctx
    )
    expect(s.offline).toBe(true)
  })

  it('报的是在等：不按断线算', () => {
    const s = taskState(task('waiting', { progress_state: 'waiting', progress_set_at: minutesAgo(90) }), ctx)
    expect(s.label).toBe('进行中')
    expect(s.offline).toBe(false)
    expect(s.stale).toBe(false)
  })
})
