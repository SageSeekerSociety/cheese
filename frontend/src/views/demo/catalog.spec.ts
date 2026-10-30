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
import { fireEvent, render, waitFor } from '@testing-library/vue'
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

// 首页和方案页上那张品牌画布（`BrandScene`）：它那一行场景名是拿画布底下那层**计算
// 出来的背景色**当颜色解析的（`scenes.ts` 里每个场景的 `u_colorBack`），而 happy-dom
// 既没有 WebGL 2，也不算样式表 —— 读出来的背景色是空串，那套着色器就为它报一句
// 「Unsupported color format」，而且是在动态 import 回来之后，落在**下一个**测试的
// 记录里。预览站里不给它场景：组件按它本来就写好的那一支收下（没有 WebGL 2 的机器上
// 也是这一支），那一块只剩自己的底色，控制台里一句都不留。
vi.mock('@/components/account/brandScene/scenes', async () => {
  const actual = await vi.importActual<typeof import('@/components/account/brandScene/scenes')>(
    '@/components/account/brandScene/scenes'
  )
  return { ...actual, SCENES: {} as typeof actual.SCENES }
})

// 文档方格（`AttachmentDocThumb`）为了画封面会去动态 import pdf.js。那一次 import 是
// 整份压缩过的库，装它要几百毫秒的主线程时间 —— 预览站里一次字节都取不回来（没有注入
// 取字节的那一份），画封面这条路根本走不到，落下的只是「方格摆类型图标」那一格。给一个
// 空壳之后，后面按秒数判定的那几格（目录页那句「找到几个」要等手停下来）不再被拖过线。
vi.mock('pdfjs-dist/legacy/build/pdf.mjs', () => ({ GlobalWorkerOptions: {} }))

// 提问页那一块正文（`@/components/common/Editor/Editor.vue`，里面建的是真的 editorjs）：
// 它的初始化是异步的，而预览站每一格挂完就收走 —— 初始化撞上容器已经不在，editorjs 内部
// 会抛一个没人接的 rejection（手快的人真页面上也会撞到：进 /ask 又立刻离开）。预览站要
// 的是「这一页长什么样」，不是编辑器本身，所以换成一个只把容器画出来的替身；那一格要
// 断言的字（标题、话题、发布按钮）都是提问页自己模板里的，替身不缺什么。
vi.mock('@/components/common/Editor/Editor.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'EditorStub',
      props: { holder: { type: String, default: 'editor' }, config: { type: Object, default: () => ({}) } },
      emits: ['create', 'ready'],
      setup: (props) => () => h('div', { id: props.holder }),
    }),
  }
})

import { CATALOG, catalogGroup, stateProps } from './catalog'
import { installCatalogAnswers } from './catalogFixtures'
import { isTodo, TODO_MARK, todoSites } from './catalogTodo'
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

/** 一格的参数序列化成字：取数那几包里有 ref，ref 互相指着，见过的对象不再展开。 */
function serialized(value: unknown): string {
  const seen = new WeakSet<object>()
  return (
    JSON.stringify(value, (_key, v: unknown) => {
      if (typeof v !== 'object' || v === null) return v
      if (seen.has(v)) return undefined
      seen.add(v)
      return v instanceof Map || v instanceof Set ? [...v] : v
    }) ?? ''
  )
}

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
    // Vuetify rounds the opened invite menu's position to physical pixels.
    vi.stubGlobal('devicePixelRatio', 1)
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
    // 首页那段故事跟着滚动走，用的是 IntersectionObserver，而 happy-dom 没有它
    // （`Landing.spec.ts` 同样补一个）。补的是浏览器本来就有的东西，不是这一件的
    // 依赖 —— 补上之后它照样只装自己声明的那几样插件。
    vi.stubGlobal(
      'IntersectionObserver',
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

  it('没有一条还留着生成脚本的占位（TODO(catalog) 或 todo(...)）', () => {
    // 骨架（`scripts/catalog-scaffold.mjs`）把要人写的每一句、要人换成真形状的每一个
    // 参数都带上记号（见 `catalogTodo.ts`）：目录里的一张卡要说清它是什么、这一格在讲
    // 什么，喂的是产品里的形状。没写完的骨架不算一张卡，只补了句子也不算。
    const unfinished = CATALOG.filter((entry) => {
      const values = [entry.args ?? {}, ...entry.states.map((s) => stateProps(entry, s))]
      const texts = [entry.about, ...entry.states.flatMap((s) => [s.name, s.note]), ...values.map(serialized)]
      return texts.some((text) => text.includes(TODO_MARK)) || values.some((v) => Object.values(v).some(isTodo))
    }).map((entry) => entry.id)
    expect(unfinished).toEqual([])
    // 数、布尔、null 认不出是哪一条的，但 `todo(...)` 调过就记了一处。
    expect(todoSites()).toEqual([])
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
          // 这一格要点的那个开关（比如「看原文」）：不点就看不到它要展出的状态。
          if (state.click) {
            const trigger = await waitFor(() => {
              const found = scope.querySelector<HTMLElement>(`[data-testid="${state.click}"]`)
              expect(found).not.toBeNull()
              return found!
            })
            await fireEvent.click(trigger)
          }
          // 有几件自己去取数（验收卡），所以「画出来了」要等一等，不是同步的事。
          await waitFor(() => {
            if (state.expect) expect(scope.textContent).toContain(state.expect)
            // 字分不出这一格的，再看一个只有这一格才有的形状。
            if (state.expectSelector) expect(scope.querySelector(state.expectSelector)).not.toBeNull()
            // 两样都没给的，退一步：至少得画出点东西来，不能是一片空白。
            if (!state.expect && !state.expectSelector) expect(scope.innerHTML.trim()).not.toBe('')
          })
        })
      }
    })
  }

  // 真页面（`DemoCatalog.vue`）挂每一件时，装的**始终是** `demoRouter` + Vuetify + i18n +
  // pinia —— 不管这一条自己声明要哪几样。上面那一组按每一条声明的 `needs` 装：声明里
  // 漏了 router，画面里那处按 name 指路的去处就根本没被点到，spec 静静地绿着，可真页面
  // 上它是断的（`router-link`、带 `to` 的 `BaseButton` 碰到没注册的 name 会抛，整段渲染
  // 停在那儿）。这一组照真页面那样挂，专门守这件事：哪一件声明漏了 router 也护得住。
  describe('照真页面那样挂（始终装 demoRouter）', () => {
    for (const entry of CATALOG) {
      for (const [index, state] of entry.states.entries()) {
        it(`${entry.id} 第 ${index + 1} 格「${state.name}」挂得起来`, async () => {
          const host = entry.layout
            ? ({ ...LAYOUT, components: { Case: entry.component } } as unknown as Component)
            : entry.component
          const { container } = render(host, {
            props: stateProps(entry, state),
            slots: state.slot ? { default: state.slot } : {},
            global: {
              plugins: [createVuetify({ components, directives }), i18n, demoRouter(), createPinia()],
            },
          })
          const scope = entry.teleport ? document.body : container
          // 只等它画出来，不看具体内容 —— afterEach 那一条已经守住了「这一挂的过程中
          // 一句 warn/error 都没有」。按 name 指路却没注册的那一处会在这里当场抛出来。
          await waitFor(() => expect(scope.innerHTML.trim()).not.toBe(''))
        })
      }
    }

    // 上面那一组是**静态**挂：「成员页」页头那颗「邀请成员」是个懒挂的 `v-menu`，菜单
    // 内容要等点开才渲染 —— 里面那两条 `:to`（邀请码 / 邮箱域名组）在没点开时根本没进
    // 过渲染，名字没在 `demoRouter` 里注册也看不出来。这一条真把菜单点开，守的正是静态
    // 挂护不住的那一处：名字缺了，点开这一刻 `router-link` 会当场抛，菜单画不出来。
    it('members-view：点开邀请菜单，两条按 name 指路的去处也接得上（懒挂，静态挂看不到）', async () => {
      const entry = CATALOG.find((e) => e.id === 'members-view')
      expect(entry).toBeTruthy()
      render(entry!.component, {
        props: stateProps(entry!, entry!.states[0]),
        global: {
          plugins: [createVuetify({ components, directives }), i18n, demoRouter(), createPinia()],
        },
      })
      // 菜单还关着，那两条去处没渲染出来。
      expect(document.body.textContent).not.toContain('邀请码')
      const activator = Array.from(document.querySelectorAll('button')).find((b) => b.textContent?.includes('邀请成员'))
      expect(activator, '没找到页头那颗「邀请成员」').toBeTruthy()
      await fireEvent.click(activator!)
      await waitFor(() => expect(document.body.textContent).toContain('邀请码'))
      const links = Array.from(document.querySelectorAll('a'))
      const codes = links.find((a) => a.textContent?.includes('邀请码'))
      const domains = links.find((a) => a.textContent?.includes('邮箱域名组'))
      // 路径照 `router/spaces.ts`：名字在表里、href 也对得上真页面。
      expect(codes?.getAttribute('href')).toBe('/spaces/8/manage/settings/invite-codes')
      expect(domains?.getAttribute('href')).toBe('/spaces/8/manage/settings/domain-groups')
    })

    // 上面那一组是**静态**挂：只等画面出来、看它写了什么字。可有的组件全部行为就是
    // 「按下去报一声」—— 「导出」那颗按钮现在只剩这一件事（拉 CSV 在页面里做）。静态挂
    // 护不住它：把 `@click` 那一行删掉，按钮照样画得出来、字照样对，上面那一组照样绿，
    // 而它在真页面上已经不响了。这一条真按一下，守的就是那一下。
    it('analytics-export-button：按一下报出 `export`（静态挂看不到那一处）', async () => {
      const entry = CATALOG.find((e) => e.id === 'analytics-export-button')
      expect(entry).toBeTruthy()
      const { container, emitted } = render(entry!.component, {
        props: stateProps(entry!, entry!.states[0]),
        global: {
          plugins: [createVuetify({ components, directives }), i18n, demoRouter(), createPinia()],
        },
      })
      const button = Array.from(container.querySelectorAll('button')).find((b) =>
        b.textContent?.includes('导出题目清单')
      )
      expect(button, '没找到那颗「导出」').toBeTruthy()
      await fireEvent.click(button!)
      expect(emitted(), '按了这一下，按钮没把「导出」报出来').toHaveProperty('export')
      expect(emitted()['export']).toHaveLength(1)
    })
  })

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

    /** 目录页上的卡：每一张指到哪一条。 */
    function cardIds(scope: ParentNode): string[] {
      return Array.from(scope.querySelectorAll('a.catalog-card')).map((a) =>
        a.getAttribute('href')!.replace('/demo/catalog/', '')
      )
    }

    it('卡按源码目录分组：每一组的卡都在那个目录下，组按目录名排', () => {
      const view = mount('/demo/catalog')
      const sections = Array.from(view.container.querySelectorAll('section.catalog-group'))
      const names = sections.map((section) => section.querySelector('.catalog-group-name code')!.textContent)
      expect(names).toEqual([...new Set(CATALOG.map(catalogGroup))].sort((a, b) => a.localeCompare(b)))
      for (const [i, section] of sections.entries()) {
        const groups = cardIds(section).map((id) => catalogGroup(CATALOG.find((e) => e.id === id)!))
        expect(new Set(groups)).toEqual(new Set([names[i]]))
      }
      expect(cardIds(view.container).sort()).toEqual(CATALOG.map((e) => e.id).sort())
    })

    it('搜索只留下名字、说明或路径里对得上的卡，并说一声找到几个', async () => {
      const view = mount('/demo/catalog')
      await fireEvent.update(view.getByRole('searchbox', { name: '按名字、说明或路径找组件' }), 'panels/Changes')
      const want = CATALOG.filter((e) =>
        [e.title, e.about, e.file].some((text) => text.toLowerCase().includes('panels/changes'))
      ).map((e) => e.id)
      expect(want.length).toBeGreaterThan(1)
      expect(want.length).toBeLessThan(CATALOG.length)
      expect(cardIds(view.container).sort()).toEqual(want.sort())
      // 那一句等手停下来才换（防抖），卡片是即时的。
      await waitFor(() => expect(view.getByRole('status').textContent).toBe(`找到 ${want.length} 个组件`))
    })

    it('什么都没对上时说一句话，不是一块空白', async () => {
      const view = mount('/demo/catalog')
      await fireEvent.update(view.getByRole('searchbox'), '不会有组件叫这个名字')
      expect(cardIds(view.container)).toEqual([])
      await waitFor(() => expect(view.getByRole('status').textContent).toBe('没有对得上的组件。'))
    })

    it('一个组件那一页画出它每一格的状态', () => {
      const entry = CATALOG.find((e) => e.id === 'user-ref')!
      const view = mount(`/demo/catalog/${entry.id}`)
      const names = Array.from(view.container.querySelectorAll('.catalog-case-name')).map((el) => el.textContent)
      expect(names).toEqual(entry.states.map((s) => s.name))
      expect(view.container.textContent).toContain('爱丽丝')
    })

    it('目录里没有的 id 说一句话，不是一块空白', () => {
      const view = mount('/demo/catalog/nope')
      expect(view.getByText('目录里没有这一个', { exact: false })).toBeTruthy()
    })
  })
})
