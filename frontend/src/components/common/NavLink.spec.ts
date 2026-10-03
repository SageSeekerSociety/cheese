// NavLink 是为「模板里原来写 `<router-link>` 的地方」准备的，所以这里测的正是
// router-link 给不了的那两件事：**没装路由也画得出来**（这是 components/** 不再
// 被路由拖下水的那一步），以及装了路由时它和 router-link 一样是真链接。
import type { Component } from 'vue'

import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import NavLink from './NavLink.vue'

const Link = NavLink as unknown as Component

const Blank = { render: () => null }

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/projects/:projectId/members/:handle', name: 'member', component: Blank },
      { path: '/:any(.*)*', component: Blank },
    ],
  })
}

const TO = { name: 'member', params: { projectId: 'p1', handle: 'alice' } }

afterEach(() => vi.restoreAllMocks())

describe('NavLink：装了路由就是一条真链接', () => {
  it('画成 <a href>，点了走路由', async () => {
    const router = makeRouter()
    await router.push('/')
    const { getByText } = render(Link, {
      props: { to: TO },
      slots: { default: '爱丽丝' },
      global: { plugins: [router] },
    })
    const a = getByText('爱丽丝')
    expect(a.tagName).toBe('A')
    expect(a.getAttribute('href')).toBe('/projects/p1/members/alice')
    await fireEvent.click(a)
    await waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/projects/p1/members/alice'))
  })

  it('`replace` 的链接换掉当前这一格，不在身后压上一条', async () => {
    const router = makeRouter()
    await router.push('/')
    const push = vi.spyOn(router, 'push')
    const replace = vi.spyOn(router, 'replace')
    const { getByText } = render(Link, {
      props: { to: TO, replace: true },
      slots: { default: '爱丽丝' },
      global: { plugins: [router] },
    })
    await fireEvent.click(getByText('爱丽丝'))
    await waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/projects/p1/members/alice'))
    // 设置里换栏、从某一栏回目录走的就是这一条：换了这一格，身后不留痕。
    expect(replace).toHaveBeenCalled()
    expect(push).not.toHaveBeenCalled()
  })

  it('带组合键的点击交给浏览器（不拦、不自己跳）', async () => {
    const router = makeRouter()
    await router.push('/')
    const { getByText } = render(Link, {
      props: { to: TO },
      slots: { default: '爱丽丝' },
      global: { plugins: [router] },
    })
    const a = getByText('爱丽丝')
    await fireEvent.click(a, { metaKey: true })
    // happy-dom 的中键/组合键语义不完整，这里只钉住「没有自己 push」。
    await new Promise((r) => setTimeout(r, 0))
    expect(router.currentRoute.value.fullPath).toBe('/')
  })

  it('`target="_blank"` 的点击也交给浏览器（新开一页，不在原地跳）', async () => {
    const router = makeRouter()
    await router.push('/')
    const { getByText } = render(Link, {
      props: { to: TO },
      attrs: { target: '_blank' },
      slots: { default: '爱丽丝' },
      global: { plugins: [router] },
    })
    const a = getByText('爱丽丝')
    // 属性照透传到 `<a>` 上：没有它浏览器也不会开新标签页。
    expect(a.getAttribute('target')).toBe('_blank')
    await fireEvent.click(a)
    // 替它 preventDefault 会把新标签页吞掉、原地跳走 —— 用了 `_blank` 的地方（协议
    // 那两句）正是最不能原地跳走的。判据与 router-link 的 `guardEvent` 一致。
    await new Promise((r) => setTimeout(r, 0))
    expect(router.currentRoute.value.fullPath).toBe('/')
  })

  it('这条路不认识这个去处（少一个必填参数）：画得出来，但不是一条链接', async () => {
    const router = makeRouter()
    await router.push('/')
    const { getByText } = render(Link, {
      props: { to: { name: 'member', params: { projectId: 'p1' } } },
      slots: { default: '爱丽丝' },
      global: { plugins: [router] },
    })
    // `resolve` 对少参数的去处是抛，不是返回半条地址：画成不可点的，而不是画一条点了
    // 会炸的链接。
    expect(getByText('爱丽丝').hasAttribute('href')).toBe(false)
  })
})

describe('NavLink：没装路由照样渲染', () => {
  it('没有 href、不进 Tab 顺序、点了什么也不发生 —— 而且不报「组件没解析」', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    // 刻意不给任何 plugin：能渲染出来就是「不依赖路由」的证据。
    const { getByText } = render(Link, { props: { to: TO }, slots: { default: '爱丽丝' } })
    const a = getByText('爱丽丝')
    expect(a.tagName).toBe('A')
    expect(a.hasAttribute('href')).toBe(false)
    expect(a.getAttribute('data-nav-inert')).toBe('')
    // 文本照画（一行数据不该因为没装路由就少一截），只是不再是链接。
    expect(a.textContent).toBe('爱丽丝')
    await fireEvent.click(a)
    expect(warn).not.toHaveBeenCalled()
  })
})
