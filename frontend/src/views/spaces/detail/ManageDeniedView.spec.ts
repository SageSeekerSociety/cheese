/** 管理那一段「这一页打不开」那一屏（纯展示）。
 *
 * 这一屏此前自己读地址拼「回题目列表」的链接，于是被场景棘轮判成 D（新场景必须
 * 只靠 props 和事件渲染，见 docs/manual/dev/scenes.md）。读地址那一半留在了页面
 * `ManageDenied.vue` 上，这一份只管画——所以断言只有两件：说的是哪一句、给的那
 * 条路通向哪个空间的题目列表。
 */
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import ManageDeniedView from './ManageDeniedView.vue'

import i18n, { setLocale } from '@/i18n'

// 断言按中文写；测试环境默认是英文界面。
beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

function show(spaceId: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/spaces/:spaceId/tasks', name: 'SpacesDetailTasksList', component: { template: '<div />' } },
    ],
  })
  return render(ManageDeniedView, {
    props: { spaceId },
    global: { plugins: [router, createVuetify({ components, directives }), i18n] },
  })
}

describe('空间管理打不开的时候', () => {
  it('说清是权限不够，且不把人往管理页上引', () => {
    const { getByText } = show('sp-1')
    getByText('你没有管理权限')
    expect(getByText(/只有这个空间的所有者和管理员能打开/)).toBeTruthy()
  })

  // 读者通常是这个空间里的成员，出得去的路是回题目列表，不是离开这个空间。
  it('给的那条路通向这个空间的题目列表', () => {
    const { getByRole } = show('sp-1')
    expect(getByRole('link', { name: '回到题目列表' }).getAttribute('href')).toBe('/spaces/sp-1/tasks')
  })

  it('路跟着传进来的空间走，不是写死的', () => {
    const { getByRole } = show('sp-42')
    expect(getByRole('link', { name: '回到题目列表' }).getAttribute('href')).toBe('/spaces/sp-42/tasks')
  })
})
