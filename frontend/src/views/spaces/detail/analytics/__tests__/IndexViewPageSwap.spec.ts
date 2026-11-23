/* eslint-disable vue/one-component-per-file -- 测试里要摆下两种根形状不同的页面桩 */
// 数据页正文那一格带淡入淡出（`out-in`：旧的一格淡出完，新的一格才进来）。「学情分析」
// 的根是几个并列节点（画面加两个弹窗），离开它时淡出那一步若等不到结束，新的一格永远
// 进不来，整块正文一片空白。这里用真的 `<transition>` 换页，钉住换过去之后新的一格在。
import type { Component } from 'vue'

import { defineComponent, h, nextTick } from 'vue'
import { cleanup, render, screen } from '@testing-library/vue'
import { afterEach, describe, expect, it } from 'vitest'

import IndexView from '../IndexView.vue'

import i18n, { setLocale } from '@/i18n'

/** 和「学情分析」一样：根上是几个并列节点，不是一个元素。 */
const SeveralRoots = defineComponent({
  name: 'SeveralRoots',
  render: () => [h('div', { 'data-testid': 'learning' }, '学情分析'), h('div', '弹窗')],
}) as Component

const OneRoot = defineComponent({
  name: 'OneRoot',
  render: () => h('div', { 'data-testid': 'overview' }, '总览'),
}) as Component

const settle = async () => {
  for (let i = 0; i < 5; i += 1) {
    await nextTick()
    await new Promise((resolve) => setTimeout(resolve, 30))
  }
}

/** 和容器一样：正文那一格，加上按路由名换的那个键（这里用组件名代替路由名）。 */
const pageProps = (page: Component) => ({ page, pageKey: (page as { name?: string }).name })

const mountWith = (page: Component) => {
  setLocale('zh-CN')
  return render(IndexView, {
    props: { ...pageProps(page), categoryItems: [], filters: {} as never },
    global: {
      plugins: [i18n],
      stubs: { transition: false, PageHeader: true, AnalyticsNavigationTabs: true, AnalyticsFilterBar: true },
    },
  })
}

const shown = (id: string) => screen.queryByTestId(id) !== null

describe('数据页换页', () => {
  afterEach(cleanup)

  it('从「学情分析」换到别的一格，新的一格会显示出来', async () => {
    const view = mountWith(SeveralRoots)
    await settle()
    expect(shown('learning')).toBe(true)

    await view.rerender(pageProps(OneRoot))
    await settle()
    expect(shown('learning')).toBe(false)
    expect(shown('overview')).toBe(true)
  })

  it('换到别的一格再回「学情分析」，它也会显示出来', async () => {
    const view = mountWith(OneRoot)
    await settle()
    await view.rerender(pageProps(SeveralRoots))
    await settle()
    await view.rerender(pageProps(OneRoot))
    await settle()
    await view.rerender(pageProps(SeveralRoots))
    await settle()
    expect(shown('learning')).toBe(true)
  })
})
