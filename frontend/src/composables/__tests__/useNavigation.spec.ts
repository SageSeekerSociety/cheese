// 组件和路由之间那扇窄门：宿主装了路由就通，没装就是 null。
//
// 门后是组件要用到的那几样：去某处、某处的地址、此刻在哪儿；另有两处是给确实要
// 整台 router 的地方开的（`router` 本体、history 里的上一格）。前三种够画完一颗
// 组件 —— 这正是「components/** 不 import vue-router」那条纪律在运行时的那一半。
import type { RouteLocationRaw } from 'vue-router'

import { ref } from 'vue'
import { createMemoryHistory, createRouter, createWebHistory } from 'vue-router'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import { type Navigation, useNavigation } from '@/composables/useNavigation'

const Blank = { render: () => null }

const TO = { name: 'member', params: { projectId: 'p1', handle: 'alice' } }

/** 一个只把 useNavigation() 的结果摊给测试看的小宿主：组件里怎么调，这里就怎么调。 */
const Probe = {
  props: ['to'],
  setup(props: { to: unknown }) {
    const nav = useNavigation()
    return { nav: nav as Navigation | null, props }
  },
  template: `<div>
    <span data-nav>{{ nav ? 'yes' : 'no' }}</span>
    <span data-route>{{ nav?.route?.path ?? '-' }}</span>
    <span data-param>{{ nav?.route?.params.projectId ?? '-' }}</span>
    <span data-hash>{{ nav?.route?.hash ?? '-' }}</span>
    <span data-matched>{{ (nav?.route?.matched ?? []).map((r) => r.name).join(',') || '-' }}</span>
    <span data-router>{{ nav?.router ? 'yes' : 'no' }}</span>
    <span data-back>{{ nav?.historyState?.back ?? '-' }}</span>
    <span data-href>{{ nav?.href(props.to) ?? '-' }}</span>
  </div>`,
}

function text(container: Element, attr: string): string {
  return container.querySelector(`[data-${attr}]`)?.textContent ?? ''
}

describe('useNavigation：宿主没装路由', () => {
  it('就是 null —— 调用方按「没有去处、没有当前位置」画，不是崩', () => {
    const { container } = render(Probe, { props: { to: TO } })
    expect(text(container, 'nav')).toBe('no')
  })
})

describe('useNavigation：宿主装了路由', () => {
  it('去某处、给地址、报当前位置', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/projects/:projectId/members/:handle', name: 'member', component: Blank },
        { path: '/projects/:projectId/settings', component: Blank },
        { path: '/:any(.*)*', component: Blank },
      ],
    })
    await router.push('/projects/p1/settings')
    const { container } = render(Probe, { props: { to: TO }, global: { plugins: [router] } })
    expect(text(container, 'nav')).toBe('yes')
    // route 是实时的：这一颗读的是 $route 那个 getter，不是安装那一刻的快照。
    expect(text(container, 'route')).toBe('/projects/p1/settings')
    expect(text(container, 'param')).toBe('p1')
    expect(text(container, 'href')).toBe('/projects/p1/members/alice')
    await router.push('/projects/p1/members/alice')
    expect(text(container, 'route')).toBe('/projects/p1/members/alice')
  })

  it('这条路不认识这个去处：href 是空，不是一条点了会炸的链接', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: Blank }] })
    const { container } = render(Probe, {
      props: { to: { name: 'member', params: { projectId: 'p1' } } },
      global: { plugins: [router] },
    })
    expect(text(container, 'href')).toBe('-')
  })
})

/** 一个点一下就跳的宿主：`navigate` 到底返不返回 promise，只有点下去才知道。 */
const NavProbe = {
  props: ['to'],
  setup(props: { to: RouteLocationRaw }) {
    const nav = useNavigation()
    const outcome = ref('-')
    async function go(replace: boolean) {
      const pending = nav?.navigate(props.to, { replace })
      outcome.value = pending ? 'promise' : 'none'
      await pending
    }
    return { outcome, go }
  },
  template: `<div>
    <span data-outcome>{{ outcome }}</span>
    <button data-push @click="go(false)">push</button>
    <button data-replace @click="go(true)">replace</button>
  </div>`,
}

describe('useNavigation：快照之外那几样', () => {
  it('锚点、记录链、router 本体、history 里的上一格都递得出来', async () => {
    const router = createRouter({
      history: createWebHistory(),
      routes: [
        { path: '/projects/:projectId', name: 'project', component: Blank },
        { path: '/projects/:projectId/settings', name: 'settings', component: Blank },
        { path: '/:any(.*)*', component: Blank },
      ],
    })
    await router.push('/projects/p1')
    await router.push('/projects/p1/settings#mcp')
    const { container } = render(Probe, { props: { to: TO }, global: { plugins: [router] } })
    expect(text(container, 'hash')).toBe('#mcp')
    // 页签条就是靠这条链知道自己在「管理的第几层」下面。
    expect(text(container, 'matched')).toBe('settings')
    expect(text(container, 'router')).toBe('yes')
    // 贴链接直接打开的第一页 back 是空；应用内跳过一格之后它记着上一格。
    expect(text(container, 'back')).toBe('/projects/p1')
  })

  it('navigate 返回 push / replace 那一支的 promise，并且真的走一趟', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: Blank },
        { path: '/projects/:projectId/members/:handle', name: 'member', component: Blank },
        { path: '/:any(.*)*', component: Blank },
      ],
    })
    await router.push('/')
    const { container, getByText } = render(NavProbe, { props: { to: TO }, global: { plugins: [router] } })
    await fireEvent.click(getByText('push'))
    expect(text(container, 'outcome')).toBe('promise')
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/projects/p1/members/alice'))
    // replace 同一处：换掉当前这一格，不抛错、不叠一条。
    await fireEvent.click(getByText('replace'))
    expect(text(container, 'outcome')).toBe('promise')
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/projects/p1/members/alice'))
  })
})
