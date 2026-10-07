// 组件预览站的机械验收：目录里**每一条的每一格**都要挂得起来，而且不报错、不报警告。
//
// 装的插件就是那一格自己声明的那几样（`catalog.ts` 的 `needs`），不是演示页那一整套
// —— 所以这一份同时也是「这一件到底要什么」的断言：声明多了最多是多装一样，声明少
// 了这里当场红。比如 `NavLink` 的「没有路由」那一格写的是 `needs: []`，它就得真的
// 一件都不装也画得出来。
//
// 这一份不测长相（长相在浏览器里看）。它测的是本仓库最贵的那条回归：一个组件悄悄
// 又要一样全局依赖，于是「能单独渲染」这句话在别处悄悄变成假的。
import type { Component, Plugin } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// 夹具（页签名、状态标）在模块加载时就取了词，所以语言要在 import 之前定下来：
// 断言按中文写，测试环境默认是英文界面。
vi.hoisted(() => localStorage.setItem('cheese:locale', 'zh-CN'))

// 演示页那一套（`DemoView.spec.ts` 同款）：验收卡自己去接口取数，happy-dom 里模块内
// 的 fetch 不走测试替身，所以读卡的两个函数直接接到演示后端的路由表上。
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  const { answerFor } = await import('./demoBackend')
  return {
    ...actual,
    getAcceptCards: async (topicId: string) => answerFor(`/topics/${topicId}/accept-card`) ?? { data: [], total: 0 },
    getPrChecks: async (topicId: string) => answerFor(`/topics/${topicId}/pr-checks`) ?? { available: false },
  }
})

import { CATALOG, stateProps } from './catalog'
import { installCatalogAnswers } from './catalogFixtures'
import { installDemoBackend } from './demoBackend'
import DemoCatalog from './DemoCatalog.vue'
import { demoRouter } from './demoRouter'

import i18n, { setLocale } from '@/i18n'

/** 要坐在 Vuetify 布局里的那两件（底栏、底部动作面板）本来就长在 layout 里。 */
const LAYOUT = { template: '<v-layout><Case v-bind="$attrs" /><slot /></v-layout>' }

function plugins(needs: string[]): Plugin[] {
  const out: Plugin[] = []
  if (needs.includes('vuetify')) out.push(createVuetify({ components, directives }))
  if (needs.includes('i18n')) out.push(i18n)
  if (needs.includes('router')) out.push(demoRouter())
  if (needs.includes('pinia')) out.push(createPinia())
  return out
}

const Page = DemoCatalog as unknown as Component

describe('组件预览站', () => {
  let original: typeof fetch
  let warned: ReturnType<typeof vi.spyOn>
  let errored: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    setLocale('zh-CN')
    original = globalThis.fetch
    globalThis.fetch = vi.fn() as unknown as typeof fetch
    installDemoBackend()
    installCatalogAnswers()
    // 挂得起来还不算数：挂的过程中说什么也得看。Vue 的「解析不了这个组件」、
    // Vuetify 的「少一个 provide」、vue-router 的「没有这条路由」都从这两个口子出。
    warned = vi.spyOn(console, 'warn').mockImplementation(() => {})
    errored = vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.stubGlobal('visualViewport', {
      width: 1024,
      height: 768,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
    })
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      }
    )
    // The app's page has a doctype (standards mode); happy-dom reports quirks
    // mode, and KaTeX warns about it on its first formula.
    Object.defineProperty(document, 'compatMode', { value: 'CSS1Compat', configurable: true })
  })

  afterEach(() => {
    // 先留下证据再收摊：哪一格说了什么话，比「测试红了」有用得多。
    expect([...warned.mock.calls, ...errored.mock.calls]).toEqual([])
    globalThis.fetch = original
    vi.restoreAllMocks()
  })

  it('没有一条还留着生成脚本的占位（TODO(catalog)）', () => {
    // 骨架（`scripts/catalog-scaffold.mjs`）把要人写的每一句都写成这个记号：目录里的
    // 一张卡要说清它是什么、这一格在讲什么，没写完的骨架不算一张卡。
    const unfinished = CATALOG.filter((entry) =>
      [entry.about, ...entry.states.flatMap((s) => [s.name, s.note])].some((text) => text.includes('TODO(catalog)'))
    ).map((entry) => entry.id)
    expect(unfinished).toEqual([])
  })

  it('每个 id 只出现一次', () => {
    const ids = CATALOG.map((entry) => entry.id)
    expect(ids.filter((id, i) => ids.indexOf(id) !== i)).toEqual([])
  })

  for (const entry of CATALOG) {
    describe(entry.title, () => {
      for (const [index, state] of entry.states.entries()) {
        it(`第 ${index + 1} 格「${state.name}」挂得起来`, async () => {
          const host = entry.layout
            ? ({ ...LAYOUT, components: { Case: entry.component } } as unknown as Component)
            : entry.component
          const { container } = render(host, {
            props: stateProps(entry, state),
            slots: state.slot ? { default: state.slot } : {},
            global: { plugins: plugins(state.needs ?? entry.needs) },
          })
          // 浮层（底部动作面板）画在 body 上，别的地方就在容器里。
          const scope = entry.teleport ? document.body : container
          // 有几件自己去取数（验收卡），所以「画出来了」要等一等，不是同步的事。
          await waitFor(() => {
            if (state.expect) expect(scope.textContent).toContain(state.expect)
            // 没有该看见的那句话的（骨架屏就是这种：它本来一个字都没有），退一步：
            // 至少得画出点东西来，不能是一片空白。
            else expect(scope.innerHTML.trim()).not.toBe('')
          })
        })
      }
    })
  }

  describe('页面本身', () => {
    function mount(path: string) {
      return render(Page, {
        props: { path },
        global: { plugins: [createVuetify({ components, directives }), i18n, demoRouter(), createPinia()] },
      })
    }

    it('目录列出每一个组件，每一张卡指到它自己那一页', () => {
      const view = mount('/demo/catalog')
      const hrefs = Array.from(view.container.querySelectorAll('a[href^="/demo/catalog/"]')).map((a) =>
        a.getAttribute('href')
      )
      for (const entry of CATALOG) {
        expect(hrefs).toContain(`/demo/catalog/${entry.id}`)
        expect(view.container.textContent).toContain(entry.title)
      }
    })

    it('一个组件那一页画出它每一格的状态', () => {
      const entry = CATALOG.find((e) => e.id === 'user-ref')!
      const view = mount(`/demo/catalog/${entry.id}`)
      const names = Array.from(view.container.querySelectorAll('.catalog-case-name')).map((el) => el.textContent)
      expect(names).toEqual(entry.states.map((s) => s.name))
      expect(view.container.textContent).toContain('@爱丽丝')
    })

    it('目录里没有的 id 说一句话，不是一块空白', () => {
      const view = mount('/demo/catalog/nope')
      expect(view.getByText('目录里没有这一个', { exact: false })).toBeTruthy()
    })
  })
})
