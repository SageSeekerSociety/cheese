// 设置的五栏各是一条子路由。守的是：打开哪一栏，就只有那一栏是选中的。
// 「基本信息」的地址就是设置页本身，其余几栏都在它下面，按前缀认选中会让它一直亮着。
// 「当前这一栏」读的是 aria-current：页签的高亮和它是同一个判断。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import SettingsTabs from './SettingsTabs.vue'

import i18n, { setLocale } from '@/i18n'

const Blank = defineComponent({ render: () => h('div') }) as Component

async function mountAt(name: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/spaces/:spaceId/manage/settings',
        component: Blank,
        children: [
          { path: '', name: 'SpacesDetailSettingsBasic', component: Blank },
          { path: 'categories', name: 'SpacesDetailSettingsCategories', component: Blank },
          { path: 'templates', name: 'SpacesDetailSettingsTemplates', component: Blank },
          { path: 'invite-codes', name: 'SpacesDetailSettingsInviteCodes', component: Blank },
          { path: 'domain-groups', name: 'SpacesDetailSettingsDomainGroups', component: Blank },
        ],
      },
    ],
  })
  await router.push({ name, params: { spaceId: '1' } })
  await router.isReady()
  render(SettingsTabs, { global: { plugins: [createVuetify({ components, directives }), router, i18n] } })
}

const current = () =>
  screen
    .getAllByRole('tab')
    .filter((tab) => tab.getAttribute('aria-current') === 'page')
    .map((tab) => tab.textContent?.trim())

describe('设置的页签', () => {
  beforeEach(() => setLocale('zh-CN'))
  afterEach(cleanup)

  it('打开「邀请码」时只有它是选中的', async () => {
    await mountAt('SpacesDetailSettingsInviteCodes')
    expect(current()).toEqual(['邀请码'])
  })

  it('打开设置页本身时选中的是「基本信息」', async () => {
    await mountAt('SpacesDetailSettingsBasic')
    expect(current()).toEqual(['基本信息'])
  })
})
