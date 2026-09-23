// 「编辑题目板信息」弹窗底部的危险区（删除题目板）钉三件事：
//   1. 只有创建者（OWNER）看得到它 —— 后端 delete_space 走的是 allow_admin=False
//      那道闸，管理员点下去只会拿到 403，摆一颗必然失败的按钮比不摆更糟；
//   2. 点下去先问一句，人没确认之前一个请求都不发；
//   3. 确认之后 DELETE /spaces/{id} 真的打出去，人 `replace` 到题目板列表 —— 不是
//      push，「返回」不该把人送回一块已经没了的板。
import type { Component } from 'vue'

import { defineComponent, h, nextTick } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const spacesDetail = vi.fn()
const listCategories = vi.fn()
const delSpace = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    detail: (...a: unknown[]) => spacesDetail(...a),
    listCategories: (...a: unknown[]) => listCategories(...a),
    del: (...a: unknown[]) => delSpace(...a),
  },
}))

vi.mock('@/network/api/avatars', () => ({
  AvatarsApi: { createAvatar: vi.fn() },
}))

vi.mock('vuetify-sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import Detail from './Detail.vue'

import DialogContainer from '@/components/common/DialogContainer.vue'
import { dialogs } from '@/plugins/dialog'
import AccountService from '@/services/account'
import { useSpaceStore } from '@/stores/space'

const SPACE_ID = 11
const OWNER_ID = 4

/** 一块题板：创建者是 4 号。 */
const SPACE = {
  id: SPACE_ID,
  name: '数据结构题板',
  intro: '',
  avatarId: null,
  admins: [{ user: { id: OWNER_ID, nickname: '老师' }, role: 'OWNER' }],
  announcements: '[]',
  taskTemplates: '[]',
  classificationTopics: [],
  visibleTaskLimit: null,
}

// 对话框本身住在 App.vue（跨路由活着），这一份把 Detail 和它一起挂起来，好让
// `dialog.confirm(...).wait()` 真的走完「弹出 → 点确定 → resolve」这一趟。
const Page = defineComponent({
  render: () => h('div', [h(RouterView), h(DialogContainer)]),
})

async function mountPage(currentUserId: number | null) {
  AccountService.user = currentUserId === null ? null : ({ id: currentUserId, nickname: '老师' } as never)

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'root', component: { render: () => h('div') } },
      { path: '/spaces', name: 'HomeSpaces', component: { render: () => h('div') } },
      // Detail 走真的那一格路由，而不是被直接 h() 出来：它自己注册了
      // onBeforeRouteUpdate，挂在 router-view 外面会招来一句 Vue Router 警告。
      { path: '/spaces/:spaceId', name: 'SpacesDetail', component: Detail as Component },
    ],
  })
  await router.push(`/spaces/${SPACE_ID}`)
  await router.isReady()

  const pinia = createPinia()
  setActivePinia(pinia)
  const utils = render(Page, {
    global: { plugins: [pinia, createVuetify({ components, directives }), router] },
  })
  await waitFor(() => expect(spacesDetail).toHaveBeenCalled())
  return { ...utils, router, store: useSpaceStore(pinia) }
}

/** 打开「编辑题目板信息」弹窗 —— 危险区住在它里面。 */
async function openEditDialog(store: ReturnType<typeof useSpaceStore>) {
  store.openEditProfile()
  await nextTick()
  await nextTick()
}

function buttonWith(text: string, base: HTMLElement | Document = document) {
  const button = Array.from(base.querySelectorAll('button')).find((b) => b.textContent?.trim() === text)
  expect(button, `没找到写着「${text}」的按钮`).toBeTruthy()
  return button as HTMLButtonElement
}

describe('题目板删除入口', () => {
  beforeEach(() => {
    // Vuetify 的浮层（v-dialog）会去读 `visualViewport`，测试环境里没有这个对象，
    // 于是弹窗根本不渲染 —— 断言会以为「危险区没出现」。给它一个够用的壳。
    if (!('visualViewport' in window)) {
      Object.defineProperty(window, 'visualViewport', {
        configurable: true,
        value: {
          height: 800,
          width: 600,
          offsetTop: 0,
          offsetLeft: 0,
          scale: 1,
          addEventListener: () => {},
          removeEventListener: () => {},
        },
      })
    }
    spacesDetail.mockResolvedValue({ data: { space: SPACE } })
    listCategories.mockResolvedValue({ data: { categories: [] } })
    delSpace.mockResolvedValue({ data: null })
  })

  afterEach(() => {
    // 浮层清单是插件里的模块级状态，跨用例活着：上一个用例没关掉的确认框会跟着
    // 进下一个用例，「确定」按钮于是有两颗，点到的是上一颗。
    dialogs.splice(0, dialogs.length)
    cleanup()
    AccountService.user = null
    vi.clearAllMocks()
  })

  it('创建者在编辑弹窗里看到危险区的那颗红按钮', async () => {
    const { store } = await mountPage(OWNER_ID)
    await openEditDialog(store)

    await waitFor(() => expect(document.body.textContent).toContain('spaces.detail.dangerZone'))
    expect(buttonWith('spaces.detail.deleteSpace').className).toContain('bg-error')
  })

  it('不是创建者的人看不到这颗按钮', async () => {
    const { store } = await mountPage(999)
    await openEditDialog(store)

    await waitFor(() => expect(document.body.textContent).toContain('spaces.detail.editSpaceInfo'))
    expect(document.body.textContent).not.toContain('spaces.detail.dangerZone')
    expect(document.body.textContent).not.toContain('spaces.detail.deleteSpaceHint')
  })

  it('先问一句：没点确定之前一个请求都不发', async () => {
    const { store } = await mountPage(OWNER_ID)
    await openEditDialog(store)
    await waitFor(() => expect(document.body.textContent).toContain('spaces.detail.dangerZone'))

    await fireEvent.click(buttonWith('spaces.detail.deleteSpace'))

    await waitFor(() => expect(document.body.textContent).toContain('spaces.detail.confirmDeleteSpace'))
    expect(delSpace).not.toHaveBeenCalled()
  })

  it('确认之后删掉它，并把人送到题目板列表', async () => {
    const { store, router } = await mountPage(OWNER_ID)
    await openEditDialog(store)
    await waitFor(() => expect(document.body.textContent).toContain('spaces.detail.dangerZone'))

    await fireEvent.click(buttonWith('spaces.detail.deleteSpace'))
    await waitFor(() => expect(document.body.textContent).toContain('spaces.detail.confirmDeleteSpace'))

    // 「确定」只有确认框那一颗：Detail 自己那几个弹窗的按钮文案都是 key，撞不上。
    await fireEvent.click(buttonWith('确定'))
    await waitFor(() => expect(delSpace).toHaveBeenCalledWith(SPACE_ID))

    await waitFor(() => expect(router.currentRoute.value.name).toBe('HomeSpaces'))
    expect(router.currentRoute.value.name).not.toBe('SpacesDetail')
  })
})
