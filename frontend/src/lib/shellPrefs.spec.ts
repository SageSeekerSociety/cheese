import { beforeEach, describe, expect, it } from 'vitest'

import { loadRevealedPages, withRevealedPage } from './shellPrefs'

// 「默认收起」要立得住，靠的是这一小段存储：收起的功能打开过一次就记住，而且记的是
// **这个人**的偏好，不是这个浏览器的、更不是这个项目的。
const KEY_FOR_A = 'cheesex.shellRevealed.v1:' + encodeURIComponent('alice')

describe('shellPrefs: 手动展开过的项目页', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('没打开过任何一页时是空集合', () => {
    expect(loadRevealedPages('alice').size).toBe(0)
  })

  it('打开过一次就记住，并且落盘', () => {
    const next = withRevealedPage(new Set<string>(), 'calendar', 'alice')
    expect([...next]).toEqual(['calendar'])
    expect(loadRevealedPages('alice')).toEqual(new Set(['calendar']))
    // 落盘的形状是 JSON 数组，键按 handle 编址（handle 里可能有 @ . 之类）。
    expect(JSON.parse(localStorage.getItem(KEY_FOR_A) || 'null')).toEqual(['calendar'])
  })

  it('再打开一次不重复', () => {
    const once = withRevealedPage(new Set<string>(), 'calendar', 'alice')
    const twice = withRevealedPage(once, 'calendar', 'alice')
    expect([...twice]).toEqual(['calendar'])
  })

  it('按 handle 分开：换个账号进来看到的是他自己的侧栏', () => {
    withRevealedPage(new Set<string>(), 'calendar', 'alice')
    expect(loadRevealedPages('bob').size).toBe(0)
    withRevealedPage(new Set<string>(), 'project-delivery', 'bob')
    expect(loadRevealedPages('alice')).toEqual(new Set(['calendar']))
    expect(loadRevealedPages('bob')).toEqual(new Set(['project-delivery']))
  })

  it('不分项目：这是一个人对一个壳的选择，不是对某一个项目的数据', () => {
    withRevealedPage(new Set<string>(), 'calendar', 'alice')
    // 换一个课程项目（同一个 handle）读的还是同一条记录 —— 函数压根不收 projectId。
    expect(loadRevealedPages('alice')).toEqual(new Set(['calendar']))
  })

  it('没有 handle（没登录）时不写、也不崩', () => {
    expect(loadRevealedPages('').size).toBe(0)
    expect(loadRevealedPages('   ').size).toBe(0)
    withRevealedPage(new Set<string>(), 'calendar', '')
    expect(localStorage.length).toBe(0)
  })

  it('存坏了就当没展开过，壳的默认照样能用', () => {
    localStorage.setItem(KEY_FOR_A, '{ not json')
    expect(loadRevealedPages('alice').size).toBe(0)
    localStorage.setItem(KEY_FOR_A, '{"a":1}')
    expect(loadRevealedPages('alice').size).toBe(0)
    localStorage.setItem(KEY_FOR_A, '[1,2,"calendar"]')
    expect(loadRevealedPages('alice')).toEqual(new Set(['calendar']))
  })

  it('withRevealedPage 不改传进来的那个集合', () => {
    const before = new Set(['calendar'])
    withRevealedPage(before, 'overview', 'alice')
    expect([...before]).toEqual(['calendar'])
  })
})
