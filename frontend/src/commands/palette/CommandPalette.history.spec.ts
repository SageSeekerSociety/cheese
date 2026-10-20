// @vitest-environment jsdom
// 面板开着的时候，返回手势 / 返回键关掉的是面板这一层，不是它底下那一页。少了这一
// 层，手机上从话题列表叫出搜索、按返回，换掉的是底下那一页；面板还开着，里面的
// 「操作」跟着变成新页面的「切到…」，人看不出返回到底做了什么。
//
// 用面板里的办法关掉（左上角、Esc、点空白）时，历史里不能多留一格：不然下一次返
// 回原地不动，人以为按坏了。jsdom 的 history 真的会退、会发 popstate，happy-dom
// 的不会，所以这个文件单独用 jsdom（同 ImageLightbox.history.spec.ts）。
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest'

import CommandPalette from './CommandPalette.vue'
import { paletteOpen } from './state'

import { setLocale } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'
import { seedProject, seedProjects } from '@/test/seedQueries'

// 面板空着的时候会把别的项目里的频道名也列出来：这份数据走网络，这里没有后端。
vi.mock('@/api', async (original) => ({
  ...(await original<object>()),
  listTopicNames: vi.fn(async () => []),
}))

const Blank = defineComponent({ render: () => h('div') })

/** 打开面板之前人站着的那一格历史。 */
const PAGE = { page: 'room' }

async function mount() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank },
      {
        path: '/projects/:projectId',
        component: Blank,
        children: [{ path: '', name: 'workspace-project', component: Blank }],
      },
    ],
  })
  await router.push('/projects/p1')
  await router.isReady()

  const pinia = createPinia()
  setActivePinia(pinia)
  seedProjects([{ id: 'p1', name: '知是' }] as never)
  seedProject('p1', { topics: [], members: [], unread: {}, privateUnread: {}, notifyLevels: {} })
  useWorkspaceStore().openProject('p1')

  const Host = defineComponent({ setup: () => () => h(components.VApp, null, () => h(CommandPalette)) })
  render(Host, { global: { plugins: [createVuetify({ components, directives }), router, pinia] } })
}

const field = () => screen.queryByRole('combobox') as HTMLInputElement | null

async function open() {
  paletteOpen.value = true
  await waitFor(() => expect(field()).not.toBeNull())
  await waitFor(() => expect((history.state as Record<string, unknown>).commandPalette).toBe(true))
}

/** 等到历史真的退完：popstate 发出来时 state 已经换好了。 */
function nextPopState() {
  return new Promise<void>((resolve) => window.addEventListener('popstate', () => resolve(), { once: true }))
}

beforeAll(() => {
  // Vuetify 的 VApp 量尺寸用。jsdom 没有它，happy-dom 才有。
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
})
afterAll(() => vi.unstubAllGlobals())
beforeEach(() => {
  setLocale('zh-CN')
  localStorage.clear()
  paletteOpen.value = false
  history.replaceState(PAGE, '')
})
afterEach(() => {
  cleanup()
  document.body.innerHTML = ''
})

it('返回键关掉面板，底下那一页不动', async () => {
  await mount()
  await open()

  const popped = nextPopState()
  history.back()
  await popped

  expect(paletteOpen.value).toBe(false)
  expect(field()).toBeNull()
  expect(history.state).toEqual(PAGE)
})

it('从面板里关掉时，历史里不多留一格', async () => {
  await mount()
  await open()

  const popped = nextPopState()
  await fireEvent.keyDown(field()!, { key: 'Escape' })
  await popped

  expect(paletteOpen.value).toBe(false)
  expect(history.state).toEqual(PAGE)
})
