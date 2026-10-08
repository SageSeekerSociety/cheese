import type { StatsClaudeAccount, StatsClaudePool } from '@/api'
import type { Trans } from './adminStats'

import { beforeEach, describe, expect, it } from 'vitest'

import { poolView } from './adminPool'

import { setLocale, t } from '@/i18n'

/** `@/i18n` 导出的那个全局 `t` 与 `Trans` 是同一个函数的两个泛型实例化：全局那个的消息
 *  表和语言是具体的（`'zh-CN' | 'en'`），而 `Trans` 用的是 `Composer` 的默认泛型，收不下
 *  它。运行时是同一个函数，类型上收一次就够了 —— 别为了测试把 `Trans` 放宽，组件里的
 *  `useI18n()` 用的就是收紧的那一组。 */
const trans = t as unknown as Trans

// 这一段的规则钉在中文上（英文只在形状不同的地方各来一条）。
beforeEach(() => setLocale('zh-CN'))

/** 本地时间 2026-10-07 14:00。**不写死 UTC**：时刻那一格比的是本地日历日（同一天只
 *  写 `14:20`），所以基准点用本地构造，断言才能在任何一个时区里成立。 */
const NOW = new Date(2026, 9, 7, 14, 0, 0).getTime()

const at = (minutes: number) => NOW / 1000 + minutes * 60

function account(over: Partial<StatsClaudeAccount> = {}): StatsClaudeAccount {
  return { name: 'primary', state: 'available', until: null, failures: 0, ...over }
}

function pool(over: Partial<StatsClaudePool> = {}): StatsClaudePool {
  return {
    accounts: [],
    reason: null,
    written_at: NOW / 1000,
    age_seconds: 0,
    stale: false,
    retry_after: null,
    ...over,
  }
}

const view = (over: Partial<StatsClaudePool> = {}) => poolView(pool(over), trans, 'zh-CN', NOW)

describe('账号池那一段', () => {
  it('可用 / 冷却 / 已停用各一行，冷却那行带解冻时刻', () => {
    const body = view({
      accounts: [
        account(),
        account({ name: 'second', state: 'cooling', until: at(20), failures: 2 }),
        account({ name: 'third', state: 'disabled', failures: 5 }),
      ],
    })

    expect(body.rows).toEqual([
      { name: 'primary', state: '可用' },
      { name: 'second', state: '冷却中，14:20 恢复 · 连续失败 2 次' },
      { name: 'third', state: '已停用 · 连续失败 5 次' },
    ])
  })

  it('失败次数为 0 或读不出来就不说 —— 「0 次」和「不知道」都不该印出来', () => {
    const body = view({
      accounts: [account({ name: 'a' }), account({ name: 'b', state: 'cooling', until: at(5), failures: null })],
    })

    expect(body.rows[0]!.state).toBe('可用')
    expect(body.rows[1]!.state).toBe('冷却中，14:05 恢复')
  })

  it('隔了天的解冻时刻带上日期，不然会被读成今天', () => {
    const body = view({ accounts: [account({ state: 'cooling', until: at(25 * 60), failures: 1 })] })
    expect(body.rows[0]!.state).toContain('10/8 15:00')
  })

  it('全部可用时是一句话，不再数状态', () => {
    const body = view({
      accounts: [account(), account({ name: 'second' })],
    })
    expect(body.summary).toBe('2 张账号都可用')
  })

  it('全部冷却时给出最早恢复的时刻 —— 这是这一块最要紧的一句话', () => {
    const body = view({
      accounts: [
        account({ name: 'a', state: 'cooling', until: at(47), failures: 1 }),
        account({ name: 'b', state: 'cooling', until: at(20), failures: 3 }),
        account({ name: 'c', state: 'cooling', until: at(33), failures: 2 }),
      ],
    })
    // 最早的是 20 分钟后的那张，不是行里的第一张。
    expect(body.summary).toBe('3 张账号全部冷却中，最早 14:20 恢复')
  })

  it('混着的时候把各状态数一遍，再补最早恢复', () => {
    const body = view({
      accounts: [
        account(),
        account({ name: 'b', state: 'cooling', until: at(20), failures: 1 }),
        account({ name: 'c', state: 'disabled', failures: 2 }),
        account({ name: 'd', state: 'unknown' }),
      ],
    })
    expect(body.summary).toBe('4 张账号：1 张冷却中、1 张已停用、1 张状态未知，最早 14:20 恢复')
  })

  it('没有冷却的账号时不承诺恢复时刻，改说「要人工重置」', () => {
    const body = view({
      accounts: [account(), account({ name: 'b', state: 'disabled', failures: 4 })],
    })
    expect(body.summary).toBe('2 张账号：1 张已停用，已停用的要人工重置')
  })

  it('一行都没有时画的是原因代号翻出来的那一句', () => {
    expect(view({ reason: 'not-configured' }).summary).toContain('没接订阅版计量代理')
    expect(view({ reason: 'missing' }).summary).toContain('还没写下')
    expect(view({ reason: 'unreadable' }).summary).toContain('读不出来')
    expect(view({ reason: 'malformed' }).summary).toContain('不是能读的形状')
    expect(view({ reason: 'read-only-tuesday' }).summary).toContain('读不出来')
    expect(view({ reason: null }).summary).toContain('读不出来')
  })

  it('没有行的那一段只画一句话', () => {
    const body = view({ reason: 'missing' })
    expect(body.empty).toBe(true)
    expect(body.rows).toEqual([])
  })

  it('旧了的快照照样给行，另注明它写于何时', () => {
    const body = view({
      accounts: [account({ state: 'cooling', until: at(20), failures: 1 })],
      written_at: NOW / 1000 - 7200,
      age_seconds: 7200,
      stale: true,
    })
    expect(body.rows).toHaveLength(1)
    expect(body.stale).toBe('这份快照写于 2小时前')
  })

  it('新鲜时不画那一行', () => {
    expect(view({ accounts: [account()] }).stale).toBe('')
  })

  it('旧后端没有这一块（`null`）时整段不画', () => {
    const body = poolView(null, trans, 'zh-CN', NOW)
    expect(body).toEqual({ rows: [], summary: '', stale: '', empty: true })
  })

  it('英文界面里同样成立（形状不同的地方各一条）', () => {
    setLocale('en')
    const body = poolView(
      pool({
        accounts: [
          account({ state: 'cooling', until: at(20), failures: 2 }),
          account({ name: 'b', state: 'disabled', failures: 1 }),
        ],
      }),
      trans,
      'en',
      NOW
    )
    expect(body.rows[0]!.state).toBe('Cooling until 02:20 PM · 2 consecutive failures')
    expect(body.summary).toBe('2 accounts: 1 cooling, 1 disabled — earliest recovery 02:20 PM')
  })
})
