// 装页面的那个 router-view 外面包着 keep-alive，这一段是整个页面缓存改动里最
// 容易被后人无声破坏的部分——三种破坏方式都不会让任何别的测试变红：
//
//   1. 白名单被改成黑名单、或者顺手把登录页加进去 → 上一个账号的表单状态留在
//      内存里给下一个人看（安全问题，屏幕上一切正常）。
//   2. v-memo="[]" 被挪进里面的 <component :is>（看着只是「放得更贴近一点」）
//      → 路由切换再也不换页，静默地一直显示旧页面。
//   3. :include 里的名字和组件的 defineOptions({ name }) 对不上 → 保活整个失
//      效，回退成「每进一个页面都要加载」，也就是这轮要修的那个毛病本身。
//
// 所以这里测的是从外面看得见的三件事：谁活着、谁不活、切了到底换没换页。
import { defineComponent, h, onActivated, onMounted, onUnmounted } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, screen, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { createDialogPlugin } from './plugins/dialog'
import App from './App.vue'
import i18n from './i18n'

vi.mock('@/api', async (original) => ({
  ...(await original<typeof import('@/api')>()),
  listProjects: vi.fn(async () => ({ data: [] })),
}))
vi.mock('@/components/common/VersionBadge.vue', () => ({ default: { template: '<span />' } }))

beforeAll(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  Object.defineProperty(window, 'innerWidth', { value: 1280, writable: true, configurable: true })
})

// 每个页面把自己的生命周期写进这里，测试再来看这串账。
let lifecycle: string[] = []
beforeEach(() => {
  lifecycle = []
})

function page(name: string, body: string) {
  return defineComponent({
    name,
    setup() {
      onMounted(() => lifecycle.push(`mount:${name}`))
      onUnmounted(() => lifecycle.push(`unmount:${name}`))
      onActivated(() => lifecycle.push(`activate:${name}`))
      return () => h('div', body)
    },
  })
}

function mountApp() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      // 懒加载的路由记录（真实路由全是 `() => import(...)`）——保活的名字匹配
      // 必须穿得过这一层，不然线上一个页面都保不住。
      { path: '/overview', component: () => Promise.resolve(page('OverviewView', '总览内容')) },
      { path: '/calendar', component: () => Promise.resolve(page('CalendarView', '日历内容')) },
      { path: '/elsewhere', component: page('ElsewhereView', '别处的内容') },
      {
        path: '/account/signin',
        name: 'signin',
        component: page('SignInView', '登录表单'),
        meta: { hideAppBar: true },
      },
      // 顶栏和 rail 会去解析这几个地址，配上它们只是为了别把测试输出淹在
      // 「No match found」里。
      { path: '/', component: page('HomeView', '首页内容') },
      { path: '/spaces', component: page('SpacesView', '空间内容') },
      { path: '/inbox', component: page('InboxView', '待办内容') },
    ],
  })
  render(App, {
    global: {
      plugins: [createVuetify({ components, directives }), createPinia(), router, i18n, createDialogPlugin],
    },
  })
  return router
}

async function goTo(router: ReturnType<typeof mountApp>, path: string, expected: string) {
  await router.push(path)
  await waitFor(() => expect(screen.getByText(expected)).toBeTruthy())
}

describe('装页面的 router-view', () => {
  it('白名单里的页面走开再回来还是原来那一个，不重新挂载', async () => {
    const router = mountApp()
    await goTo(router, '/overview', '总览内容')
    await goTo(router, '/elsewhere', '别处的内容')
    await goTo(router, '/overview', '总览内容')

    // 只挂载过一次：回来的是同一个组件实例，所以它 onMounted 里的取数不会再跑
    // 一遍，屏幕上也就不会再转一次圈。
    expect(lifecycle.filter((e) => e === 'mount:OverviewView')).toHaveLength(1)
    expect(lifecycle).toContain('activate:OverviewView')
    expect(lifecycle).not.toContain('unmount:OverviewView')
  })

  it('白名单外的页面照旧卸载——登录页绝不能被留在内存里', async () => {
    const router = mountApp()
    await goTo(router, '/account/signin', '登录表单')
    await goTo(router, '/elsewhere', '别处的内容')

    // 这一条是「白名单被改成黑名单」「登录页被顺手加进白名单」的哨兵：缓存住
    // 登录页等于把上一个账号的填写状态留给下一台前的下一个人。
    expect(lifecycle).toContain('unmount:SignInView')
    expect(screen.queryByText('登录表单')).toBeNull()
    // 没被保活的东西不会「回来」，所以也不该有 activate。
    expect(lifecycle).not.toContain('activate:SignInView')
  })

  it('路由切换照常换页：屏幕上永远是当前这一页', async () => {
    // 这一条是 v-memo="[]" 被挪错位置的哨兵。它挂在 router-view 上（挡住父组件
    // 的重渲染），不能挂到里面的 <component :is> 上——挪进去就变成「这一格永远
    // 是第一次渲染的那个组件」，路由再怎么切页面都不换，而且一声不吭。
    const router = mountApp()

    await goTo(router, '/overview', '总览内容')
    expect(screen.queryByText('日历内容')).toBeNull()

    await goTo(router, '/calendar', '日历内容')
    expect(screen.queryByText('总览内容')).toBeNull()

    await goTo(router, '/overview', '总览内容')
    expect(screen.queryByText('日历内容')).toBeNull()
  })
})
