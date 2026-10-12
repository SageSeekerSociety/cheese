// 外壳把站自己那两页接进来了：地址说 `/demo/catalog/motion` 就画动效台，说
// `/demo/catalog/tokens` 就画 token 页 —— 不是「目录里没有这一个」。
//
// 和 `catalog.spec.ts` 里那一组一样照真页面挂（Vuetify + i18n + demoRouter + pinia），
// 因为这两页也坐在那个壳里，也要用工具条那排入口。
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.hoisted(() => localStorage.setItem('cheese:locale', 'zh-CN'))

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  const { answerFor } = await import('./demoBackend')
  return {
    ...actual,
    getAcceptCards: async (topicId: string) => answerFor(`/topics/${topicId}/accept-card`) ?? { data: [], total: 0 },
    getPrChecks: async (topicId: string) => answerFor(`/topics/${topicId}/pr-checks`) ?? { available: false },
  }
})

vi.mock('@/components/account/brandScene/scenes', async () => {
  const actual = await vi.importActual<typeof import('@/components/account/brandScene/scenes')>(
    '@/components/account/brandScene/scenes'
  )
  return { ...actual, SCENES: {} as typeof actual.SCENES }
})

vi.mock('pdfjs-dist/legacy/build/pdf.mjs', () => ({ GlobalWorkerOptions: {} }))

import DemoCatalog from './DemoCatalog.vue'
import { STUDIO_PAGES } from './demoCatalogStudio'
import { demoRouter } from './demoRouter'

import i18n from '@/i18n'

const Page = DemoCatalog as unknown as Component

/** 页面上的文字：换行和缩进都压成一个空格，好比对。 */
function textOf(scope: ParentNode): string {
  return (scope.textContent ?? '').replace(/\s+/g, ' ')
}

function mount(path: string) {
  return render(Page, {
    props: { path },
    global: { plugins: [createVuetify({ components, directives }), i18n, demoRouter(), createPinia()] },
  })
}

beforeEach(() => {
  // 两页里那些小样挂上以后再翻到终态，happy-dom 里没有 rAF 的调度就跑不动 —— 给一个
  // 下一帧就回调的替身，等的是同一件事。
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    setTimeout(() => cb(0), 0)
    return 1
  })
})

describe('外壳里的站自己那两页', () => {
  it('地址说动效台就画动效台，不说「目录里没有这一个」', () => {
    const view = mount('/demo/catalog/motion')
    expect(view.container.querySelector('.motion')).not.toBeNull()
    expect(textOf(view.container)).toContain('动效台')
    expect(textOf(view.container)).not.toContain('目录里没有这一个')
  })

  it('地址说 token 页就画 token 页', () => {
    const view = mount('/demo/catalog/tokens')
    expect(view.container.querySelector('.tokens')).not.toBeNull()
    expect(textOf(view.container)).toContain('设计 token')
    expect(textOf(view.container)).not.toContain('目录里没有这一个')
  })

  it('顶栏那排入口指到这两页，站着的那一页自己标出来', () => {
    const view = mount('/demo/catalog/motion')
    const links = Array.from(view.container.querySelectorAll('.studio-toplink'))
    expect(links.map((a) => a.getAttribute('href'))).toEqual(STUDIO_PAGES.map((page) => `/demo/catalog/${page.id}`))
    const mine = links.find((a) => a.getAttribute('href') === '/demo/catalog/motion')!
    expect(mine.getAttribute('aria-current')).toBe('page')
    expect(
      links.find((a) => a.getAttribute('href') === '/demo/catalog/tokens')!.getAttribute('aria-current')
    ).toBeNull()
  })

  it('左栏也列着这两页：窄屏下顶栏收掉了，这里是唯一的入口', () => {
    const view = mount('/demo/catalog/tokens')
    const pages = Array.from(view.container.querySelectorAll('.rail-link-page'))
    expect(pages.map((a) => a.getAttribute('href'))).toEqual(STUDIO_PAGES.map((page) => `/demo/catalog/${page.id}`))
    expect(
      view.container.querySelector('.rail-link-page[href="/demo/catalog/tokens"]')!.getAttribute('aria-current')
    ).toBe('page')
  })

  it('真没有的 id 还是那句话：这两页不是把「找不到」那一支顶掉了', () => {
    const view = mount('/demo/catalog/nope')
    expect(textOf(view.container)).toContain('目录里没有这一个')
  })

  it('组件那一页照旧：拆页面没有动过它', () => {
    const view = mount('/demo/catalog/user-ref')
    expect(view.container.querySelectorAll('.catalog-case-name').length).toBeGreaterThan(0)
    expect(textOf(view.container)).toContain('@爱丽丝')
  })
})
