// 隐私中心的壳：标题、四个标签页、底部那张帮助卡。标签页是带 name 的
// router-link，名字写错不会报错，只会渲染成点不动的死链——所以这些名字在
// 这里也一并断言。
import { h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import PrivacyCenter from './PrivacyCenter.vue'

import i18n, { setLocale } from '@/i18n'

const CJK = /[㐀-䶿一-鿿豈-﫿]/

beforeEach(() => vi.stubGlobal('visualViewport', new EventTarget()))
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function mountPage() {
  const stub = { template: '<div />' }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/privacy', name: 'PrivacyCenter', component: PrivacyCenter },
      { path: '/privacy/real-name', name: 'PrivacyCenterRealNameInfo', component: stub },
      { path: '/privacy/access-logs', name: 'PrivacyCenterAccessLogs', component: stub },
      { path: '/privacy/policy', name: 'PrivacyCenterPolicy', component: stub },
    ],
  })
  await router.push('/privacy')
  // 通过 router-view 挂载，而不是直接 render(PrivacyCenter)：这一页自己带一个
  // <router-view>，直接挂的话它会把当前路由再渲染一遍，页面上就有两份。
  return render(
    { render: () => h(RouterView) },
    {
      global: { plugins: [router, createPinia(), i18n, createVuetify({ components, directives })] },
    }
  )
}

describe('privacy center shell', () => {
  it('还是说原来的那几句中文', async () => {
    setLocale('zh-CN')
    const view = await mountPage()

    expect(view.getByText('隐私与安全中心')).toBeTruthy()
    expect(view.getByText('概览')).toBeTruthy()
    expect(view.getByText('访问记录')).toBeTruthy()
    expect(view.getByText('需要帮助？')).toBeTruthy()
  })

  it('renders title, tabs and the help card from the catalog in English', async () => {
    setLocale('en')
    const view = await mountPage()

    expect(view.getByText('Privacy & security center')).toBeTruthy()
    expect(view.getByText('Overview')).toBeTruthy()
    expect(view.getByText('Access log')).toBeTruthy()
    expect(view.getByText('Privacy policy')).toBeTruthy()
    expect(view.getByText('Need help?')).toBeTruthy()
    expect(view.getByText('Contact support')).toBeTruthy()
    expect(view.container.textContent ?? '').not.toMatch(CJK)
  })

  it('keeps every tab pointing at a route that exists', async () => {
    setLocale('en')
    const view = await mountPage()

    // router-link 解析不出名字时挂不上 href，这几个标签就废了。
    const tabs = Array.from(view.container.querySelectorAll('.v-tab'))
    expect(tabs.map((tab) => tab.getAttribute('href'))).toEqual([
      '/privacy',
      '/privacy/real-name',
      '/privacy/access-logs',
      '/privacy/policy',
    ])
  })
})
