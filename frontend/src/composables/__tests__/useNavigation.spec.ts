// 组件和路由之间那扇窄门：宿主装了路由就通，没装就是 null。
//
// 门后只有三件事（去某处、某处的地址、此刻在哪儿），拿不到 vue-router 的任何实例
// 方法 —— 这正是「components/** 不 import vue-router」那条纪律在运行时的那一半。
import { createMemoryHistory, createRouter } from 'vue-router'
import { render } from '@testing-library/vue'
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
