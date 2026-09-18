// 这一页是写死三条模拟赛题的静态页，没有接口，所以断言直接对着渲染结果来。
// 三件事：中文原文没变、英文整页无汉字、底部说明那条 router-link 还插得进去
// （走 `#accessLog` 插槽，槽名对不上只会把 `{accessLog}` 印出来，不报错）。
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DataSharing from './DataSharing.vue'

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
  const stub = { template: '<div />' }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: DataSharing },
      { path: '/access-logs', name: 'PrivacyCenterAccessLogs', component: stub },
    ],
  })
  return {
    router,
    view: render(DataSharing, {
      global: { plugins: [router, createPinia(), i18n, createVuetify({ components, directives })] },
    }),
  }
}

describe('DataSharing', () => {
  it('中文下画的是原文', async () => {
    setLocale('zh-CN')
    const { view } = mountPage()

    await view.findByText('需要实名的赛题')
    await view.findByText('3 个赛题')
    await view.findByText('智慧城市设计挑战赛')
    await view.findByText('机器学习算法大赛')
    // 「创新创业项目展示」和标题里的「需要实名的赛题」不同字，这里挑一个只出现一次的来认。
    await view.findByText('校级活动')
    expect(view.getAllByText('进行中')).toHaveLength(2)
    expect(view.getAllByText('提交日期')).toHaveLength(3)
  })

  it('英文下整页没有汉字', async () => {
    setLocale('en')
    const { view } = mountPage()

    await view.findByText('Contests that need your real name')
    await view.findByText('Smart City Design Challenge')
    expect(view.getAllByText('Team entry')).toHaveLength(2)
    expect(view.getAllByText('Submitted')).toHaveLength(3)
    expect(view.getAllByText('Ongoing')).toHaveLength(2)
    expect(view.getByText('Ended')).toBeTruthy()

    // getAllByText 只找叶子里正好等于这句的元素，句子里混着链接的元素匹配不上，
    // 所以这里改扫整页的文本节点，汉字一个都不许剩。
    const text = view.container.textContent ?? ''
    expect(CJK.test(text)).toBe(false)
  })

  it('英文下的说明句里插着指向访问记录的链接', async () => {
    setLocale('en')
    const { view } = mountPage()

    const link = await view.findByText('the access log')
    expect(link.getAttribute('href')).toBe('/access-logs')
    // 插槽没接上时占位符会原样印出来，这里正好把那种情况挡住。
    expect(view.container.textContent).not.toContain('{accessLog}')
  })

  it('中文下的说明句里同样插着链接', async () => {
    setLocale('zh-CN')
    const { view } = mountPage()

    const link = await view.findByText('访问记录')
    expect(link.getAttribute('href')).toBe('/access-logs')
    expect(view.container.textContent).not.toContain('{accessLog}')
  })
})
