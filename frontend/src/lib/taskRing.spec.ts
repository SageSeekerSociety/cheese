import { describe, expect, it } from 'vitest'

import { ringRank, taskRing } from './taskRing'

/** 一条最普通的活：开着、闲着、没递过卡。 */
function task(over: Partial<Parameters<typeof taskRing>[0]> = {}) {
  return { status: 'open', residency: 'idle' as const, queued_at: null, accepted_at: null, card: null, ...over }
}

describe('taskRing', () => {
  it('把「在跑」和「闲着」分开 —— 两条都还开着', () => {
    // 圆环存在的第一个理由：open 答不了「现在有什么在动」。
    expect(taskRing(task({ residency: 'running' })).state).toBe('running')
    expect(taskRing(task()).state).toBe('idle')
  })

  it('把「排队中」和「闲着」分开', () => {
    // 闲着是没人找它，排队是它想跑但房间四个槽位满了。对看的人是两件事：
    // 一个要去催，一个只能等。
    expect(taskRing(task({ queued_at: '2026-08-27T01:00:00Z' })).state).toBe('queued')
  })

  it('等人验收也是安静的，但不是闲着', () => {
    expect(taskRing(task({ card: { id: 'c', status: 'pending' } })).state).toBe('reviewing')
    expect(taskRing(task({ card: { id: 'c', status: 'pr_open' } })).state).toBe('reviewing')
  })

  it('结算完的卡不再让这条活显示成等验收', () => {
    // 驳回、作废、闸门红了 —— 卡还在，但没人在等它了，这条活回到闲着。
    for (const status of ['rejected', 'revoked', 'gate_failed', 'gate_blocked']) {
      expect(taskRing(task({ card: { id: 'c', status } })).state).toBe('idle')
    }
  })

  it('在跑压过等验收', () => {
    // 卡描述的是它可能马上要顶掉的那一版；「在跑」是此刻真的成立的那件事。
    const t = task({ residency: 'running', card: { id: 'c', status: 'pending' } })
    expect(taskRing(t).state).toBe('running')
  })

  it('已交付压过一切，包括它已经被关掉', () => {
    const t = task({ status: 'closed', accepted_at: '2026-08-27T01:00:00Z' })
    expect(taskRing(t).state).toBe('delivered')
    expect(taskRing(t).label).toBe('已交付')
  })

  it('关掉却什么都没交付，和已交付不是一格', () => {
    // 「已交付 / 已关闭未交付」要分开显示，靠的就是这两格不同。
    expect(taskRing(task({ status: 'closed' })).state).toBe('closed')
    expect(taskRing(task({ status: 'closed' })).label).toBe('已收工')
  })

  it('后端没给 residency 时不假装它在跑', () => {
    // 老接口、或者一次没带上这个字段的响应：宁可说闲着，也不能凭空说在跑。
    expect(taskRing({ status: 'open', accepted_at: null, card: null }).state).toBe('idle')
  })
})

describe('ringRank', () => {
  it('在动的排在做完的前面', () => {
    const order = (['running', 'queued', 'reviewing', 'idle', 'delivered', 'closed'] as const).map(ringRank)
    expect(order).toEqual([...order].sort((a, b) => a - b))
    expect(ringRank('running')).toBeLessThan(ringRank('idle'))
    expect(ringRank('idle')).toBeLessThan(ringRank('delivered'))
  })
})
