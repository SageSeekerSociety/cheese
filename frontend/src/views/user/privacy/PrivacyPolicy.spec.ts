// 这一页是四小节的长文，每小节一句话标题加四条正文，翻完最容易整段漏掉一条。
// 断言两件事：两种语言下四小节的正文都在，且英文整页一个汉字都没有。
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import PrivacyPolicy from './PrivacyPolicy.vue'

import i18n, { setLocale } from '@/i18n'

const CJK = /[㐀-䶿一-鿿豈-﫿]/

beforeEach(() => {
  vi.stubGlobal('visualViewport', new EventTarget())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function mountPage() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: PrivacyPolicy }] })
  return render(PrivacyPolicy, {
    global: { plugins: [router, createPinia(), i18n, createVuetify({ components, directives })] },
  })
}

describe('PrivacyPolicy', () => {
  it('中文下四小节齐全', async () => {
    setLocale('zh-CN')
    const view = mountPage()

    await view.findByText('最近更新: 2023/12/01')
    // 「信息收集」这类小节名同时出现在顶部导航按钮和小节标题里，各两处。
    expect(view.getAllByText('信息收集')).toHaveLength(2)
    expect(view.getAllByText('信息使用')).toHaveLength(2)
    expect(view.getAllByText('信息保护')).toHaveLength(2)
    expect(view.getAllByText('用户权利')).toHaveLength(2)
    await view.findByText('账户信息')
    await view.findByText('权限管理')
  })

  it('英文下整页没有汉字', async () => {
    setLocale('en')
    const view = mountPage()

    await view.findByText('Last updated: 2023/12/01')
    expect(view.getAllByText('What we collect')).toHaveLength(2)
    expect(view.getAllByText('How we use it')).toHaveLength(2)
    expect(view.getAllByText('How we protect it')).toHaveLength(2)
    expect(view.getAllByText('Your rights')).toHaveLength(2)

    const text = view.container.textContent ?? ''
    expect(CJK.test(text)).toBe(false)
  })

  it('英文下每小节四条正文都在', async () => {
    setLocale('en')
    const view = mountPage()

    for (const snippet of [
      'Access logs and IP address',
      'Clicks and browsing behaviour',
      'Cookies and similar technologies',
      'Analysis and improvement',
      'Security audits',
      'Data separation',
      'Viewing the access log',
      'Managing permissions',
    ]) {
      await view.findByText(snippet)
    }
  })
})
