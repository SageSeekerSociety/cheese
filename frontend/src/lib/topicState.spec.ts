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

describe('频道叫什么', () => {
  it('项目本身那个频道按读者的语言叫，别的照存着的标题显示', () => {
    setLocale('en')
    expect(topicTitle({ kind: 'root', title: 'P · 项目总览' })).toBe('General')
    expect(topicTitle({ kind: 'topic', title: '分页调研' })).toBe('分页调研')
  })
})

// 还没起名的任务：库里存的是给 agent 读的占位标题，屏幕按语言叫它。
describe('活叫什么', () => {
  it('未命名的活在英文界面叫 New task，中文界面叫「新任务」', () => {
    const unnamed = { title: '新任务', title_source: 'placeholder' }
    setLocale('en')
    expect(taskTitle(unnamed)).toBe('New task')
    setLocale('zh-CN')
    expect(taskTitle(unnamed)).toBe('新任务')
  })

  it('起过名的活、没带这一位的活照存着的标题显示', () => {
    setLocale('en')
    expect(taskTitle({ title: '新任务', title_source: 'human' })).toBe('新任务')
    expect(taskTitle({ title: '拆导入' })).toBe('拆导入')
  })
})
