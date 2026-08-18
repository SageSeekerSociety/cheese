import { describe, expect, it } from 'vitest'

import { topicPhase, topicPhaseBadge, topicShortId, topicStateBadge } from './topicState'

describe('话题状态标', () => {
  it('归档的话题读作「已采纳」，不是 git 的 merged', () => {
    expect(topicStateBadge('archived')).toEqual({ label: '已采纳', cls: 'pr-state--merged' })
  })

  it('草稿单独一档', () => {
    expect(topicStateBadge('draft').label).toBe('草稿')
  })

  // 未知状态不该把标去掉：一个没有标的话题读起来像「还没开始」，而它其实在跑。
  it('其余一律「进行中」，包括后端将来新加的状态', () => {
    expect(topicStateBadge('active').label).toBe('进行中')
    expect(topicStateBadge(undefined).label).toBe('进行中')
    expect(topicStateBadge('something-new').label).toBe('进行中')
  })

  it('短 id 取前 6 位，没有 id 时是空串而不是 "undefine"', () => {
    expect(topicShortId('0123456789abcdef')).toBe('012345')
    expect(topicShortId(null)).toBe('')
    expect(topicShortId(undefined)).toBe('')
  })
})

// 话题此刻处在哪一段：话题状态、正在跑的轮次、验收卡三样折成一个词。头部拿它当
// 常驻状态条，工作面板拿它选打开时停在哪个 tab —— 所以这里定的是那一个词。
describe('话题所处阶段', () => {
  it('已归档盖过一切，包括还在跑的轮次', () => {
    expect(topicPhase({ status: 'archived', working: true, card: 'pending' })).toBe('archived')
  })

  // 卡说「等你验收」，而芝士这会儿正在改——正在发生的事才是此刻为真的那件。
  it('正在跑的时候是「施工中」，压过手上那张卡', () => {
    expect(topicPhase({ status: 'active', working: true, card: 'pending' })).toBe('working')
  })

  it('闸门和待采纳都是「待验收」——对人来说是同一件事：等这张卡', () => {
    expect(topicPhase({ card: 'pending' })).toBe('reviewing')
    expect(topicPhase({ card: 'gate' })).toBe('reviewing')
  })

  it('点完采纳之后是「交付中」，不是「已采纳」——合并还在跑', () => {
    expect(topicPhase({ card: 'delivering' })).toBe('delivering')
  })

  it('没有卡也没在跑就是「进行中」', () => {
    expect(topicPhase({ status: 'active', card: null })).toBe('open')
  })

  // 待验收 = 有人在等你，用 --warn 的三件套；机器在忙的两段是中性陈述。
  it('只有「待验收」这一档带颜色，因为只有它在等人', () => {
    expect(topicPhaseBadge('reviewing')).toEqual({ label: '待验收', cls: 'pr-state--reviewing' })
    expect(topicPhaseBadge('working').label).toBe('施工中')
    expect(topicPhaseBadge('delivering').label).toBe('交付中')
    expect(topicPhaseBadge('archived').label).toBe('已采纳')
  })
})
