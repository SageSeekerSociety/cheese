/**
 * 反馈列表里的一**行**。
 *
 * 这一行上唯一「去哪」的入口是正文那一块：整块是一条去详情页的真链接。组件文件头
 * 写着为什么它不能退回「整行 @click + router.push」——那条路鼠标能用，键盘和读屏
 * 完全够不着。所以这里钉的就是那一条的地址：它错了，或者退化成不可点的东西，这一
 * 行在键盘和读屏里就没有入口了，而画面上看不出任何区别。
 *
 * 第二条钉的是它和「支持」的关系：正文那条链接里**不能**套着支持按钮。`<button>`
 * 套在 `<a>` 里是「交互内容套交互内容」，浏览器会各画各的，而真嵌进去之后点支持
 * 会顺手打开详情。
 *
 * 第三条钉的是这一件**不自己发请求**：点支持只在原地报一个 `support` 事件（带这一条
 * 的 id），谁渲染它谁去调 store。所以这一件挂起来**不需要 pinia** —— 下面这几条都
 * 只装 Vuetify 和路由。把 pinia 装回来（或者让卡片重新读 store）会让这里当场红。
 */
import type { Component } from 'vue'
import type { FeedbackCard as FeedbackCardItem } from '@/cx_types'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import FeedbackCard from './FeedbackCard.vue'

import { setLocale } from '@/i18n'

const blank = defineComponent({ setup: () => () => h('div') })

const vuetify = createVuetify({ components, directives })

/** 一行反馈。默认这一条是公开、没办完、还没人支持的那一种。 */
function item(extra: Partial<FeedbackCardItem> = {}): FeedbackCardItem {
  return {
    id: 'c-1',
    display_id: 'FB-1042',
    kind: 'bug',
    title: '导出一个月的数据要等四十秒',
    summary: '每次导出都要重跑一遍全量聚合。',
    status: 'received',
    priority: 'normal',
    visibility: 'public',
    security: false,
    author_handle: 'andylizf',
    author_is_agent: false,
    author_avatar_id: null,
    submitted_by_handle: null,
    assignee_handle: null,
    tags: [],
    supports: 0,
    comments: 0,
    supported: false,
    last_activity_at: null,
    created_at: '2026-09-20T00:00:00Z',
    ...extra,
  }
}

/** 挂一行。**不装 pinia**：卡片只收 props、只发事件（`support`），请求由页面发 ——
 *  它要是又偷偷读起 store，这里会当场抛「getActivePinia() was called but there was
 *  no active Pinia」，而不是悄悄绿过去。 */
function mount(card: FeedbackCardItem = item()) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/feedback/:id', name: 'FeedbackDetail', component: blank },
      { path: '/:any(.*)*', component: blank },
    ],
  })
  return render(FeedbackCard as Component, {
    props: { item: card },
    global: { plugins: [vuetify, router] },
  })
}

beforeEach(() => setLocale('zh-CN'))

describe('反馈列表里的一行', () => {
  it('正文那一块点进去是这一条的详情页', () => {
    const { container, getByText } = mount()

    const link = getByText('导出一个月的数据要等四十秒').closest('a')
    expect(link).not.toBeNull()
    expect(link?.getAttribute('href')).toBe('/feedback/c-1')
    // 摘要跟着一起在链接里：点标题还是点摘要，去的都是同一条。
    expect(link?.textContent).toContain('每次导出都要重跑一遍全量聚合。')
    expect(container.querySelector('a')).toBe(link)
  })

  it('支持按钮在链接外面 —— 链接里不能套按钮', () => {
    const { container, getByRole } = mount()

    const link = container.querySelector('a')
    expect(link?.querySelector('button')).toBeNull()
    expect(getByRole('button', { name: /支持/ })).toBeTruthy()
  })

  it('不能公开的条目没有支持按钮，但链接还在', () => {
    const { container, queryByRole } = mount(item({ visibility: 'private' }))

    expect(queryByRole('button', { name: /支持/ })).toBeNull()
    expect(container.querySelector('a')?.getAttribute('href')).toBe('/feedback/c-1')
  })

  it('点支持只报一个带 id 的事件，不自己改数字、不自己发请求', async () => {
    let fired: unknown = null
    const onSupport = vi.fn((id: string) => {
      fired = id
    })
    const { getByRole, baseElement } = render(FeedbackCard as Component, {
      props: { item: item({ supports: 3 }), onSupport },
      global: {
        plugins: [
          vuetify,
          createRouter({
            history: createMemoryHistory(),
            routes: [
              { path: '/feedback/:id', name: 'FeedbackDetail', component: blank },
              { path: '/:any(.*)*', component: blank },
            ],
          }),
        ],
      },
    })

    await fireEvent.click(getByRole('button', { name: /支持/ }))

    // 报上去的是**这一条**的 id：页面可能同时摆着好几行，得知道按的是哪一条。
    expect(onSupport).toHaveBeenCalledTimes(1)
    expect(fired).toBe('c-1')
    // 数字仍是数据里的那个（3），不是本地 +1 猜出来的 4 —— 服务端回的才是准的。
    expect(baseElement.querySelector('.fb-card__count')?.textContent).toBe('3')
  })

  it('办完了的条目：支持按钮画得出来，但按不动', () => {
    const { getByRole } = mount(item({ status: 'deployed' }))

    expect(getByRole('button', { name: /支持/ }).hasAttribute('disabled')).toBe(true)
  })
})
