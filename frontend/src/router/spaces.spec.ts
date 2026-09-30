// 空间设置的每一栏都能单独链接：把地址发给另一位管理员，他打开的就是那一栏，
// 不是被带回第一栏。
import type { RouteRecordRaw } from 'vue-router'

import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'

import spaces from './spaces'

// 路由树和重定向照旧，页面不挂。
function withoutViews(record: RouteRecordRaw): RouteRecordRaw {
  return {
    ...record,
    components: { default: { template: '<div />' } },
    children: record.children?.map(withoutViews),
  } as RouteRecordRaw
}

function router() {
  return createRouter({ history: createMemoryHistory(), routes: [withoutViews(spaces)] })
}

describe('空间设置的地址', () => {
  it.each([
    ['basic', 'SpacesDetailSettingsBasic'],
    ['categories', 'SpacesDetailSettingsCategories'],
    ['templates', 'SpacesDetailSettingsTemplates'],
    ['invite-codes', 'SpacesDetailSettingsInviteCodes'],
    ['domain-groups', 'SpacesDetailSettingsDomainGroups'],
    ['templates/create', 'SpacesDetailCreateTemplate'],
    ['templates/2/edit', 'SpacesDetailEditTemplate'],
  ])('直接打开 %s 这一栏，停在这一栏', async (tab, name) => {
    const r = router()
    await r.push(`/spaces/42/manage/settings/${tab}`)
    expect(r.currentRoute.value.name).toBe(name)
  })
})
