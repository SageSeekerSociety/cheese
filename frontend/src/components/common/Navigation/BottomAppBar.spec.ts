// 底栏渲染的是「手机那份清单」，一格不多一格不少。
//
// 之前底栏自己拿 visibleOnMobile 过滤全量清单，于是每多一个项目就多一格
// （五个项目 = 七格，每格约 53px），而「＋新建项目」被同一个开关关掉之后
// 手机上就建不了项目了。所以这里测两件事：格子来自清单本身，以及**没有地址、
// 只有动作**的那种格子点得动——工作区那一格在你一个项目都没有时就是它。
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import type { Project } from '@/cx_types'
import type { NavItem } from './types'

import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import BottomAppBar from './BottomAppBar.vue'
import { tabItems } from './destinations'

import { setLocale } from '@/i18n'
import { DEFAULT_SHELL } from '@/lib/shell'

const here = dirname(fileURLToPath(import.meta.url))

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/projects/:projectId', name: 'project', component: { template: '<div />' } },
    { path: '/:pathMatch(.*)*', name: 'catch-all', component: { template: '<div />' } },
  ],
})

function mount(items: NavItem[]) {
  const vuetify = createVuetify({ components, directives })
  // v-bottom-navigation 要坐在一个 layout 里，和它在 App.vue 里的位置一样。
  const Host = {
    components: { BottomAppBar },
    props: ['items'],
    template: '<v-layout><BottomAppBar :items="items" /></v-layout>',
  }
  return render(Host, { props: { items }, global: { plugins: [vuetify, router] } })
}

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

beforeEach(() => setLocale('zh-CN'))

const tab = (title: string, extra: Partial<NavItem> = {}): NavItem => ({
  key: title,
  type: 'item',
  title,
  icon: 'mdi-circle',
  ...extra,
})

/** 亮着的格子（Vuetify 给当前格的类），按渲染顺序。 */
const litTabs = (container: Element) =>
  Array.from(container.querySelectorAll('.v-btn--active'), (el) => el.textContent?.trim())

describe('BottomAppBar', () => {
  it('清单里有几格就渲染几格', async () => {
    const { findByText, queryByText } = mount([
      tab('空间', { to: '/spaces' }),
      tab('工作区', { to: '/projects/p1' }),
      tab('待办', { to: '/inbox' }),
    ])
    for (const label of ['空间', '工作区', '待办']) expect(await findByText(label)).toBeTruthy()
    expect(queryByText('新建项目')).toBeNull()
  })

  it('待办那一格带着件数，没有待办时不画', async () => {
    const { findByRole, rerender } = mount([tab('待办', { to: '/inbox', badge: 3 })])
    const inbox = await findByRole('link')
    expect(inbox.textContent).toContain('3')
    expect(inbox.getAttribute('aria-label') ?? '').toContain('3')
    await rerender({ items: [tab('待办', { to: '/inbox', badge: 0 })] })
    expect((await findByRole('link')).textContent?.trim()).toBe('待办')
  })

  it('只有动作、没有地址的一格点得动', async () => {
    const createProject = vi.fn()
    const { findByText } = mount([tab('工作区', { action: createProject })])
    await fireEvent.click(await findByText('工作区'))
    expect(createProject).toHaveBeenCalledOnce()
  })

  it('一格底下不止一条顶层路由时，它照样亮着', async () => {
    // 「空间」那一格装的是首页整层（空间 + 小队），而 /teams 不在 /spaces 底下。
    // 只按 router-link 的规则算，人在小队里翻的时候底栏整排都是灰的——看起来
    // 像已经走出了这个 app 的导航。
    await router.push('/teams/explore')
    const { container } = mount([
      tab('空间', { to: '/spaces', match: (path) => path.startsWith('/teams') }),
      tab('待办', { to: '/inbox' }),
    ])
    expect(litTabs(container)).toEqual(['空间'])
  })

  it('地址里写的是项目的短名时，中间那格亮着', async () => {
    // 底栏的当前格是 Vuetify 照地址算出来的，而这个应用的地址栏里项目写的是短名
    // （`canonicalAddress` 落地时换的）。工作区那一格写的要是内部编号，就永远算不上
    // 当前格，图标一直不变黄——首页和待办一点就成当前格，所以那两格会黄。桌面那排
    // 项目头像在 #3029 踩过同一个坑，这里是漏掉的那一处。
    await router.push('/projects/demo')
    const { container } = mount(
      tabItems(
        {
          projects: [{ id: 'p-uuid', slug: 'demo', name: '演示' } as Project],
          workspaceProjectId: 'p-uuid',
          createProject: () => {},
        },
        DEFAULT_SHELL
      )
    )
    expect(litTabs(container)).toEqual(['工作区'])
  })

  it('每一格的遮罩都压掉，不只是选中那一格', () => {
    // Vuetify 给按钮的遮罩不止选中那一层：hover 一层、聚焦一层（--v-focus-opacity
    // 是 0.12）。手机上点过一格之后它还带着焦点，只压选中那一格的话，点过而没成
    // 为当前格的那一格就留成一块灰底。jsdom 不套用组件里的 scoped 样式，所以这里
    // 盯住规则本身：这一条不许收窄到 --active 上。
    const src = readFileSync(join(here, 'BottomAppBar.vue'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
    const at = src.indexOf('.v-btn__overlay')
    expect(at, '底栏要压掉 Vuetify 给按钮的遮罩').toBeGreaterThan(-1)
    const selector = src.slice(src.lastIndexOf('}', at) + 1, src.indexOf('{', at))
    const body = src.slice(src.indexOf('{', at) + 1, src.indexOf('}', at))
    expect(selector).toContain(':deep(.v-btn > .v-btn__overlay)')
    expect(selector, '只压选中那一格会漏掉点过的那一格和另外两格').not.toContain('--active')
    expect(body).toContain('opacity: 0')
  })
})
