import { beforeEach, describe, expect, it } from 'vitest'

import { taskTitle, topicShortId, topicStateBadge, topicTitle } from './topicState'

import { setLocale } from '@/i18n'

// 断言按中文写；测试环境默认是英文界面。
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

// 还没名字的话题按读者的语言叫；认它靠 `title_source`，不靠库里那几个中文字。
describe('话题叫什么', () => {
  it('未命名的话题在英文界面叫 New topic，中文界面叫「新话题」', () => {
    const unnamed = { kind: 'channel', title: '新频道', title_source: 'placeholder' }
    setLocale('en')
    expect(topicTitle(unnamed)).toBe('New channel')
    setLocale('zh-CN')
    expect(topicTitle(unnamed)).toBe('新频道')
  })

  it('有人起过的名字照原样显示，哪怕它恰好就是「新话题」', () => {
    setLocale('en')
    expect(topicTitle({ kind: 'channel', title: '新频道', title_source: 'human' })).toBe('新频道')
    expect(topicTitle({ kind: 'channel', title: '分页调研', title_source: 'auto' })).toBe('分页调研')
    // 没带这一位的话题（旧接口、别处拼出来的行）照存着的标题显示。
    expect(topicTitle({ kind: 'channel', title: '分页调研' })).toBe('分页调研')
  })
})

// 从一条消息升级出来、还没起名的活：库里存的是房间的占位标题，屏幕按语言叫它。
describe('活叫什么', () => {
  it('未命名的活在英文界面叫 New task，中文界面叫「新任务」', () => {
    const unnamed = { title: '新频道', title_source: 'placeholder' }
    setLocale('en')
    expect(taskTitle(unnamed)).toBe('New task')
    setLocale('zh-CN')
    expect(taskTitle(unnamed)).toBe('新任务')
  })

  it('起过名的活、没带这一位的活照存着的标题显示', () => {
    setLocale('en')
    expect(taskTitle({ title: '新频道', title_source: 'human' })).toBe('新频道')
    expect(taskTitle({ title: '拆导入' })).toBe('拆导入')
  })
})
