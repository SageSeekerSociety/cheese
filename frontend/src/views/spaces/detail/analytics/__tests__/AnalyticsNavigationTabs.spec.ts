// 数据页的六格：打开哪一格，就只有那一格是当前的。「总览」的地址就是数据页本身，
// 其余几格都在它下面，按前缀认它会一直亮着。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen } from '@testing-library/vue'
import { afterEach, describe, expect, it } from 'vitest'

import AnalyticsNavigationTabs from '../components/AnalyticsNavigationTabs.vue'

import i18n, { setLocale } from '@/i18n'
import { ANALYTICS_ROUTE_NAMES } from '@/lib/spaceRouteNames'

const Blank = defineComponent({ render: () => h('div') }) as Component

async function mountAt(name: string) {
  setLocale('zh-CN')
  const paths: Record<string, string> = {
    overview: '',
    alerts: 'alerts',
    publishers: 'publishers',
    tasks: 'tasks',
    participants: 'participants',
    learning: 'learning',
  }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/spaces/:spaceId/manage/analytics',
        component: Blank,
        children: Object.entries(ANALYTICS_ROUTE_NAMES).map(([key, routeName]) => ({
          path: paths[key],
          name: routeName,
          component: Blank,
        })),
      },
    ],
  })
  await router.push({ name, params: { spaceId: '1' } })
  await router.isReady()
  render(AnalyticsNavigationTabs, { global: { plugins: [createVuetify({ components, directives }), router, i18n] } })
}

const current = () =>
  screen
    .getAllByRole('link')
    .filter((tab) => tab.getAttribute('aria-current') === 'page')
    .map((tab) => tab.textContent?.trim())

describe('数据页的页签', () => {
  afterEach(cleanup)

  it('打开「告警」时只有它是当前的', async () => {
    await mountAt(ANALYTICS_ROUTE_NAMES.alerts)
    expect(current()).toEqual(['告警'])
  })

  it('打开数据页本身时当前的是「总览」', async () => {
    await mountAt(ANALYTICS_ROUTE_NAMES.overview)
    expect(current()).toEqual(['总览'])
  })
})
