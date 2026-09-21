import { beforeEach, describe, expect, it } from 'vitest'

import { columnLabel } from './board'
import { topicPhase, topicPhaseBadge, topicShortId, topicStateBadge } from './topicState'

import { setLocale } from '@/i18n'

// 状态词现在走词表，断言的是中文那一边：happy-dom 的 navigator.language 是
// en-US，不钉语言的话徽章上写的是 Accepted / Building。
beforeEach(() => setLocale('zh-CN'))

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

// 徽章上的词和看板列名是**同一张表**（`workspace.status.*`）：施工中 / 交付中 /
// 已完成在屏幕上是同一个词，一处改了另一处没改，看的人只会以为界面在说两件事。
// 这一组钉的就是那句「一处改、两处跟着变」——两处各写一份词也能各自通过自己的
// 用例，只有把它俩摆在一起才看得出来。
describe('状态词和看板列名共用一张表', () => {
  it('施工中 / 交付中 / 已完成两处说同一个词', () => {
    expect(columnLabel('building')).toBe(topicPhaseBadge('working').label)
    expect(columnLabel('delivering')).toBe(topicPhaseBadge('delivering').label)
    expect(columnLabel('done')).toBe(topicStateBadge('closed').label)
  })

  it('换语言时两处一起换', () => {
    expect(columnLabel('building')).toBe('施工中')
    setLocale('en')
    expect(columnLabel('building')).toBe('Building')
    expect(topicPhaseBadge('working').label).toBe('Building')
  })

  // 反面：`archived` 这个标识符在两处不是一件事。看板那一列是「已归档」（房间收了，
  // 列还画着），话题这一档是「已采纳」（验收通过、收工）。共用一个键的话，总有一屏
  // 会说错，所以它们是两个键——这条守住别被「顺手合并」掉。
  it('archived 在两处同名不同义：列是「已归档」，话题是「已采纳」', () => {
    expect(columnLabel('archived')).toBe('已归档')
    expect(topicStateBadge('archived').label).toBe('已采纳')
  })
})
