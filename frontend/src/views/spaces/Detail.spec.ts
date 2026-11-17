// 空间外壳替侧栏读待审核的题数：只有所有者与管理员读（那个接口对成员不开，读了
// 只会换来一个 403），读到的数就是侧栏「待审核」旁边那个数。
import type { Component } from 'vue'

import { defineComponent, h, markRaw } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

// 告知卡是一只 VDialog（VOverlay），而 happy-dom 没有 visualViewport：不补上浮层挂不起来。
beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.visualViewport) {
    vi.stubGlobal('visualViewport', {
      width: 1280,
      height: 900,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent: () => false,
    })
  }
})
afterAll(() => vi.unstubAllGlobals())

const taskList = vi.fn()
const spacesDetail = vi.fn()
const listCategories = vi.fn()
const readLearningNotice = vi.fn()
const acknowledgeLearningNotice = vi.fn()
const toastError = vi.fn()

/** 这块板的告知卡状态：测试在 mount 之前摆好（composable 在挂载那一刻就去读）。 */
let noticeState: { enabled: boolean; needsAcknowledge: boolean; acknowledged: boolean } = {
  enabled: false,
  needsAcknowledge: false,
  acknowledged: false,
}

vi.mock('@/api/taskBoard', () => ({
  TaskBoardApi: { list: (...a: unknown[]) => taskList(...a) },
}))

// 侧栏「我的草稿」旁边的数，换空间时读一次。
vi.mock('@/api/taskDrafts', () => ({
  TaskDraftsApi: { list: vi.fn().mockResolvedValue([]) },
}))

vi.mock('@/api/spaces', () => ({
  SpacesApi: {
    detail: (...a: unknown[]) => spacesDetail(...a),
    listCategories: (...a: unknown[]) => listCategories(...a),
    // 进板时那张告知卡的状态：默认不弹（这一页只量侧栏那个数）。
    readLearningNotice: (...a: unknown[]) => readLearningNotice(...a),
    acknowledgeLearningNotice: (...a: unknown[]) => acknowledgeLearningNotice(...a),
  },
}))

vi.mock('@/services/account', () => ({
  default: { _user: { value: null as { id: number } | null } },
}))

vi.mock('vuetify-sonner', () => ({
  toast: { success: vi.fn(), error: (...a: unknown[]) => toastError(...a) },
}))

import Detail from './Detail.vue'

import i18n, { setLocale } from '@/i18n'
import AccountService from '@/services/account'
import { useSpaceStore } from '@/stores/space'

const SPACE_ID = 11
const ADMIN_ID = 4
const Blank = defineComponent({ render: () => h('div') }) as Component
const Page = defineComponent({ render: () => h('div', '题目列表') }) as Component

async function mount(userId: number, extra: Record<string, unknown> = {}, child: Component = Blank) {
  AccountService._user.value = { id: userId } as never
  spacesDetail.mockResolvedValue({
    space: {
      id: SPACE_ID,
      name: '数据结构空间',
      admins: [{ user: { id: ADMIN_ID }, role: 'ADMIN' }],
      // What the server says this reader may do: the admin manages, anyone else views.
      viewerPermissions: userId === ADMIN_ID ? ['view', 'edit_settings', 'teach'] : ['view', 'publish'],
      ...extra,
    },
  })
  taskList.mockResolvedValue({ tasks: [], page: { total: 3 } })
  listCategories.mockResolvedValue({ categories: [] })
  readLearningNotice.mockImplementation(() => Promise.resolve(noticeState))
  acknowledgeLearningNotice.mockResolvedValue(undefined)

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId', component: Detail, children: [{ path: '', component: child }] },
      { path: '/space-applications/:spaceId', name: 'HomeSpaceApplication', component: Blank },
    ],
  })
  await router.push(`/spaces/${SPACE_ID}`)
  await router.isReady()

  const pinia = createPinia()
  setActivePinia(pinia)
  const view = render(
    { render: () => h(Detail) },
    { global: { plugins: [router, pinia, i18n, createVuetify({ components, directives })] } }
  )
  // markRaw: the store is reactive, and a router put into it would have currentRoute unwrapped.
  return Object.assign(useSpaceStore(), { view, router: markRaw(router) })
}

describe('空间外壳读待审核的题数', () => {
  afterEach(() => {
    cleanup()
    AccountService._user.value = null
    vi.clearAllMocks()
    noticeState = { enabled: false, needsAcknowledge: false, acknowledged: false }
  })

  it('成员不读', async () => {
    const store = await mount(99)

    await waitFor(() => expect(store.currentSpace?.id).toBe(SPACE_ID))
    expect(taskList).not.toHaveBeenCalled()
    expect(store.pendingAuditCount).toBe(0)
  })

  it('管理员读，读到的数给侧栏', async () => {
    const store = await mount(ADMIN_ID)

    await waitFor(() => expect(store.pendingAuditCount).toBe(3))
    expect(taskList).toHaveBeenCalledWith(expect.objectContaining({ space: SPACE_ID, approved: 'NONE' }))
  })
})

// 没过审的板：后端除了详情一律 404。外壳要先认出来，换到这份申请上，而不是
// 把页面挂上去、让每一页各自取数各自弹一条红色的「获取失败」。
describe('所有者打开一块还没过审的空间', () => {
  afterEach(() => {
    cleanup()
    AccountService._user.value = null
    vi.clearAllMocks()
  })

  it.each(['PENDING', 'REJECTED'])('%s：换到这份申请上，不挂页面、不读分类、不弹错', async (reviewStatus) => {
    setLocale('zh-CN')
    const { view, router } = await mount(ADMIN_ID, { reviewStatus }, Page)

    await waitFor(() => expect(router.currentRoute.value.name).toBe('HomeSpaceApplication'))
    expect(router.currentRoute.value.params.spaceId).toBe(String(SPACE_ID))
    expect(view.queryByText('题目列表')).toBeNull()
    expect(listCategories).not.toHaveBeenCalled()
    expect(taskList).not.toHaveBeenCalled()
    expect(toastError).not.toHaveBeenCalled()
  })

  it('过审了：照常挂页面、读分类', async () => {
    setLocale('zh-CN')
    const { view } = await mount(ADMIN_ID, { reviewStatus: 'APPROVED' }, Page)

    await view.findByText('题目列表')
    await waitFor(() => expect(listCategories).toHaveBeenCalled())
  })
})

// 进这块板时那张告知卡：服务端说这块板开着学情分析、我还没点过，就弹一次；按钮只有
// 「我已阅读并知悉」，点了才记下、才消失。
describe('成员进块时的告知卡', () => {
  afterEach(() => {
    cleanup()
    document.body.innerHTML = ''
    AccountService._user.value = null
    vi.clearAllMocks()
    noticeState = { enabled: false, needsAcknowledge: false, acknowledged: false }
  })

  it('还没点过：弹出来，点了记下', async () => {
    setLocale('zh-CN')
    noticeState = { enabled: true, needsAcknowledge: true, acknowledged: false }
    await mount(99)

    // 路由那一支和直接挂的这一份都画了同一张卡（`mount` 两处都挂），取其中一张即可。
    const [card] = await screen.findAllByTestId('learning-notice')
    expect(within(card).getByText('这个空间开启了学情分析')).toBeTruthy()
    await fireEvent.click(within(card).getByText('我已阅读并知悉'))
    await waitFor(() => expect(acknowledgeLearningNotice).toHaveBeenCalledWith(SPACE_ID))
  })

  it('点过了（或者没开学情分析）：不弹', async () => {
    setLocale('zh-CN')
    noticeState = { enabled: true, needsAcknowledge: false, acknowledged: true }
    await mount(99)

    await waitFor(() => expect(readLearningNotice).toHaveBeenCalled())
    expect(screen.queryAllByTestId('learning-notice')).toHaveLength(0)
  })
})
