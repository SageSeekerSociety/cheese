// 空间设置是一层浮层，左边五栏，每一栏是一条子路由。守的是：打开哪一栏，目录里就只有
// 那一栏是选中的；模板表单算在「题目模板」下面；桌面上打开设置本身落在「基本信息」，
// 手机上停在目录。「选中」读的是 aria-current，高亮和它是同一个判断。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import Index from './Index.vue'

import i18n, { setLocale } from '@/i18n'

const Blank = defineComponent({ render: () => h('div') }) as Component

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  vi.stubGlobal('visualViewport', {
    width: 1280,
    height: 800,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
})
afterAll(() => vi.unstubAllGlobals())

async function mountAt(name: string, width = 1280, params: Record<string, string> = {}) {
  ;(window as unknown as { innerWidth: number }).innerWidth = width
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId', name: 'SpacesDetailTasksList', component: Blank },
      {
        path: '/spaces/:spaceId/manage/settings',
        name: 'SpacesDetailSettings',
        component: Index,
        children: [
          { path: 'basic', name: 'SpacesDetailSettingsBasic', component: Blank },
          { path: 'categories', name: 'SpacesDetailSettingsCategories', component: Blank },
          { path: 'templates', name: 'SpacesDetailSettingsTemplates', component: Blank },
          { path: 'invite-codes', name: 'SpacesDetailSettingsInviteCodes', component: Blank },
          { path: 'domain-groups', name: 'SpacesDetailSettingsDomainGroups', component: Blank },
          { path: 'templates/:templateIndex/edit', name: 'SpacesDetailEditTemplate', component: Blank },
        ],
      },
    ],
  })
  await router.push({ name, params: { spaceId: '1', ...params } })
  await router.isReady()
  render(
    { template: '<v-app><router-view /></v-app>' },
    { global: { plugins: [createVuetify({ components, directives }), router, i18n, createPinia()] } }
  )
  return router
}

const current = () =>
  screen
    .getAllByRole('link')
    .filter((link) => link.getAttribute('aria-current') === 'page')
    .map((link) => link.textContent?.trim())

describe('空间设置的目录', () => {
  beforeEach(() => setLocale('zh-CN'))
  afterEach(() => {
    cleanup()
    document.body.innerHTML = ''
  })

  it('打开「邀请码」时只有它是选中的', async () => {
    await mountAt('SpacesDetailSettingsInviteCodes')
    await waitFor(() => expect(current()).toEqual(['邀请码']))
  })

  it('编辑一个模板时选中的是「题目模板」', async () => {
    await mountAt('SpacesDetailEditTemplate', 1280, { templateIndex: '0' })
    await waitFor(() => expect(current()).toEqual(['题目模板']))
  })

  it('桌面上打开设置本身，落在「基本信息」', async () => {
    const router = await mountAt('SpacesDetailSettings')
    await waitFor(() => expect(router.currentRoute.value.name).toBe('SpacesDetailSettingsBasic'))
    await waitFor(() => expect(current()).toEqual(['基本信息']))
  })

  it('手机上打开设置本身，停在目录', async () => {
    const router = await mountAt('SpacesDetailSettings', 390)
    await waitFor(() => expect(screen.getByText('邮箱域名组')).toBeTruthy())
    expect(router.currentRoute.value.name).toBe('SpacesDetailSettings')
  })
})
