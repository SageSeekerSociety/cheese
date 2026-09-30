// 关掉设置回到打开它之前的那一页；在设置里换了几页也还是那一页；没有来处时去给定的
// 落脚处。
import type { RouteLocationNormalized } from 'vue-router'

import { describe, expect, it } from 'vitest'

import { pageBeforeSettings, rememberPageBeforeSettings } from './settingsReturn'

const page = (fullPath: string, settings = false) =>
  ({ fullPath, matched: [{ meta: settings ? { settingsOverlay: true } : {} }] }) as unknown as RouteLocationNormalized

describe('关掉设置去哪', () => {
  it('还没去过别的页面就去落脚处', () => {
    expect(pageBeforeSettings('/home')).toBe('/home')
  })

  it('回到打开设置之前的那一页，设置里换页不算', () => {
    rememberPageBeforeSettings(page('/spaces/1/tasks?topic=2'))
    rememberPageBeforeSettings(page('/users/settings/profile', true))
    rememberPageBeforeSettings(page('/users/settings/devices', true))
    expect(pageBeforeSettings('/home')).toBe('/spaces/1/tasks?topic=2')
  })
})
