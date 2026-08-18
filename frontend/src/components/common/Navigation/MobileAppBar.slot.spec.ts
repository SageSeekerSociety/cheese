// 手机上只有一条顶栏，从不卸载 —— 变的是里面装什么。
//
// 以前自带头的页面（话题列表、话题页）是让整条顶栏不渲染，于是在两类页面之间
// 切换时 v-main 的 padding 从 57 滑到 0（Vuetify 给 .v-main 定的 transition
// 是 .2s），整页跟着抖一下，看起来像两条头在打架。现在那些页面改成把自己的
// 东西填进顶栏那一格，所以这里测的是：路由说自己填，顶栏就只给落点、不写标题。
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeAll, describe, expect, it } from 'vitest'

import MobileAppBar from './MobileAppBar.vue'

import i18n from '@/i18n'

const blank = { template: '<div />' }
const routes = [
  { path: '/inbox', name: 'inbox', component: blank, meta: { title: '待办' } },
  { path: '/account/signin', name: 'signin', component: blank },
  { path: '/projects/:id', name: 'list', component: blank, meta: { title: '项目工作台', barSlot: true } },
  {
    path: '/projects/:id/topics/:tid',
    name: 'topic',
    component: blank,
    meta: { title: '话题', barSlot: true, backTo: 'list' },
  },
]

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

async function mountAt(path: string) {
  const router = createRouter({ history: createWebHistory(), routes })
  await router.push(path)
  await router.isReady()
  const Host = { components: { MobileAppBar }, template: '<v-layout><MobileAppBar /></v-layout>' }
  return render(Host, {
    global: { plugins: [createVuetify({ components, directives }), router, createPinia(), i18n] },
  })
}

describe('手机顶栏', () => {
  it('路由说自己填，就只给落点、不写标题', async () => {
    const { container } = await mountAt('/projects/p1')
    expect(container.querySelector('#app-bar-slot')).toBeTruthy()
    expect(container.textContent).not.toContain('项目工作台')
  })

  it('其余页面照常写路由的标题', async () => {
    const { container } = await mountAt('/inbox')
    expect(container.querySelector('#app-bar-slot')).toBeNull()
    expect(container.textContent).toContain('待办')
  })

  // 页面栈里的那几层右边留给这一页自己的操作：个人项在那儿既不相关，也挤掉
  // 标题的宽度（设计文档 §3.4 的那张图就是 ← / 标题 / ⋯）。
  it('栈里的层不挂个人入口，一级目的地挂', async () => {
    const stacked = await mountAt('/projects/p1/topics/t1')
    expect(stacked.container.textContent).not.toContain('登录')

    const top = await mountAt('/inbox')
    expect(top.container.textContent).toContain('登录')
  })
})
