// 设置「基本信息」这一栏。底部的删除空间钉三件事：
//   1. 只有创建者（OWNER）看得到它 —— 后端 delete_space 走的是 allow_admin=False
//      那道闸，管理员点下去只会拿到 403，摆一颗必然失败的按钮比不摆更糟；
//   2. 点下去先问一句，人没确认之前一个请求都不发；
//   3. 确认之后 DELETE /spaces/{id} 真的打出去，人 `replace` 到题目板列表 —— 不是
//      push，「返回」不该把人送回一块已经没了的板。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
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
const updateSpace = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    detail: (...a: unknown[]) => spacesDetail(...a),
    listCategories: (...a: unknown[]) => listCategories(...a),
    del: (...a: unknown[]) => delSpace(...a),
    update: (...a: unknown[]) => updateSpace(...a),
  },
}))

// 空间外壳替管理员读一遍待审核的题数，这里不关心它的结果。
vi.mock('@/network/api/tasks', () => ({
  TasksApi: { list: vi.fn().mockResolvedValue({ data: { tasks: [], page: { total: 0 } } }) },
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

import Detail from '../../Detail.vue'

import BasicInfo from './BasicInfo.vue'

import DialogContainer from '@/components/common/DialogContainer.vue'
import { setLocale } from '@/i18n'
import { dialogs } from '@/plugins/dialog'
import AccountService from '@/services/account'
import { useSpaceStore } from '@/stores/space'

// The confirm dialog's buttons are read by their Chinese labels below.
setLocale('zh-CN')

const SPACE_ID = 11
const OWNER_ID = 4

/** 一块题板：创建者是 4 号。 */
const SPACE = {
  id: SPACE_ID,
  name: '数据结构题板',
  intro: '',
  avatarId: null,
  admins: [{ user: { id: OWNER_ID, nickname: '管理员' }, role: 'OWNER' }],
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
  AccountService.user = currentUserId === null ? null : ({ id: currentUserId, nickname: '管理员' } as never)

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'root', component: { render: () => h('div') } },
      { path: '/spaces', name: 'HomeSpaces', component: { render: () => h('div') } },
      // 空间外壳（Detail）读回空间，这一栏挂在它下面，和真的路由树一样。
      {
        path: '/spaces/:spaceId',
        name: 'SpacesDetail',
        component: Detail as Component,
        children: [{ path: 'manage/settings', name: 'SpacesDetailSettingsBasic', component: BasicInfo as Component }],
      },
    ],
  })
  await router.push(`/spaces/${SPACE_ID}/manage/settings`)
  await router.isReady()

  const pinia = createPinia()
  setActivePinia(pinia)
  const utils = render(Page, {
    global: { plugins: [pinia, createVuetify({ components, directives }), router] },
  })
  await waitFor(() => expect(spacesDetail).toHaveBeenCalled())
  return { ...utils, router, store: useSpaceStore(pinia) }
}

function buttonWith(text: string, base: HTMLElement | Document = document) {
  const button = Array.from(base.querySelectorAll('button')).find((b) => b.textContent?.trim() === text)
  expect(button, `没找到写着「${text}」的按钮`).toBeTruthy()
  return button as HTMLButtonElement
}

// 确认框里的按钮：浮层挂在 body 末尾，页面里那张「删除空间」的卡片先出现在 DOM 里，
// `buttonWith` 取到的是那张卡。确认框那颗得单独在 `.v-dialog` 里按同一句文案找。
function dialogButtonWith(text: string) {
  const button = Array.from(document.querySelectorAll('.v-dialog button')).find((b) => b.textContent?.trim() === text)
  expect(button, `确认框里没找到写着「${text}」的按钮`).toBeTruthy()
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

  it('创建者看得到删除空间的按钮', async () => {
    await mountPage(OWNER_ID)

    await waitFor(() => expect(buttonWith('spaces.detail.deleteSpace')).toBeTruthy())
  })

  it('不是创建者的人看不到这颗按钮', async () => {
    await mountPage(999)

    await waitFor(() => expect(document.body.textContent).toContain('spaces.settings.basic.save'))
    expect(document.body.textContent).not.toContain('spaces.detail.deleteSpace')
  })

  it('先问一句：没点确定之前一个请求都不发', async () => {
    await mountPage(OWNER_ID)
    await fireEvent.click(await waitFor(() => buttonWith('spaces.detail.deleteSpace')))

    await waitFor(() => expect(document.body.textContent).toContain('spaces.detail.confirmDeleteSpace'))
    expect(delSpace).not.toHaveBeenCalled()
  })

  it('确认之后删掉它，并把人送到题目板列表', async () => {
    const { router } = await mountPage(OWNER_ID)
    await fireEvent.click(await waitFor(() => buttonWith('spaces.detail.deleteSpace')))
    await waitFor(() => expect(document.body.textContent).toContain('spaces.detail.confirmDeleteSpace'))

    // 确认键写着「删除空间」（动词），和页面里那张卡同句，所以按确认框那一层来找。
    await fireEvent.click(dialogButtonWith('spaces.detail.deleteSpace'))
    await waitFor(() => expect(delSpace).toHaveBeenCalledWith(SPACE_ID))

    await waitFor(() => expect(router.currentRoute.value.name).toBe('HomeSpaces'))
    expect(router.currentRoute.value.name).not.toBe('SpacesDetail')
  })
})

// 保存：改了什么就把什么发出去；没存上时，填好的内容还留在表单里。
describe('基本信息的保存', () => {
  beforeEach(() => {
    spacesDetail.mockResolvedValue({ data: { space: SPACE } })
    listCategories.mockResolvedValue({ data: { categories: [] } })
  })

  afterEach(() => {
    cleanup()
    AccountService.user = null
    vi.clearAllMocks()
  })

  async function rename(getByLabelText: (text: string) => HTMLElement, value: string) {
    const input = getByLabelText('spaces.settings.basic.name') as HTMLInputElement
    await waitFor(() => expect(input.value).toBe(SPACE.name))
    await fireEvent.update(input, value)
    await fireEvent.click(buttonWith('spaces.settings.basic.save'))
    return input
  }

  it('改过的名字发给服务端', async () => {
    updateSpace.mockResolvedValue({ data: { space: SPACE } })
    const { getByLabelText } = await mountPage(OWNER_ID)

    await rename(getByLabelText, '算法题板')

    await waitFor(() =>
      expect(updateSpace).toHaveBeenCalledWith(SPACE_ID, expect.objectContaining({ name: '算法题板' }))
    )
  })

  it('没存上时，改过的名字还在', async () => {
    updateSpace.mockRejectedValue(new Error('boom'))
    const { getByLabelText } = await mountPage(OWNER_ID)

    const input = await rename(getByLabelText, '算法题板')

    await waitFor(() => expect(updateSpace).toHaveBeenCalled())
    expect(input.value).toBe('算法题板')
  })
})
