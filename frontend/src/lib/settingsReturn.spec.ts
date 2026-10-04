// 关掉设置回到打开它之前的那一页；在设置里换了几页也还是那一页；没有来处时去给定的
// 落脚处。
import type { RouteLocationNormalized } from 'vue-router'

import { describe, expect, it } from 'vitest'

import { pageBeforeSettings, rememberPageBeforeSettings } from './settingsReturn'

const page = (fullPath: string, meta: Record<string, boolean> = {}) =>
  ({ fullPath, matched: [{ meta }] }) as unknown as RouteLocationNormalized

describe('关掉设置去哪', () => {
  it('还没去过别的页面就去落脚处', () => {
    expect(pageBeforeSettings('/home')).toBe('/home')
  })

  it('回到打开设置之前的那一页，设置里换页不算', () => {
    rememberPageBeforeSettings(page('/spaces/1/tasks?topic=2'))
    rememberPageBeforeSettings(page('/users/settings/profile', { settingsOverlay: true }))
    rememberPageBeforeSettings(page('/users/settings/devices', { settingsOverlay: true }))
    expect(pageBeforeSettings('/home')).toBe('/spaces/1/tasks?topic=2')
  })

  it('登录页不算：没登录时点开设置，登录后关掉回到登录之前的那一页', () => {
    rememberPageBeforeSettings(page('/inbox'))
    rememberPageBeforeSettings(page('/account/signin?redirect=/users/settings', { hideAppBar: true }))
    rememberPageBeforeSettings(page('/users/settings/profile', { settingsOverlay: true }))
    expect(pageBeforeSettings('/home')).toBe('/inbox')
  })
})

// 从设置里点出去的一页（设置里那颗「市场」就是）不算「离开设置」：它是从设置这一层
// 点开的下一层，关掉设置该回最初打开它的那一页，而不是刚点开的那一页。
describe('从设置里点出去的一页', () => {
  const settings = (path = '/projects/1/settings/agents') => page(path, { settingsOverlay: true })

  it('不算离开设置：关掉设置回最初那一页，不回刚点开的市场', () => {
    rememberPageBeforeSettings(page('/projects/1/topics'))
    rememberPageBeforeSettings(settings())
    rememberPageBeforeSettings(page('/market'), settings())
    expect(pageBeforeSettings('/home')).toBe('/projects/1/topics')
  })

  it('从普通页面走到市场，再开设置关掉，回的是市场', () => {
    rememberPageBeforeSettings(page('/projects/1/topics'))
    rememberPageBeforeSettings(page('/market'), page('/projects/1/topics'))
    rememberPageBeforeSettings(settings())
    expect(pageBeforeSettings('/home')).toBe('/market')
  })
})
