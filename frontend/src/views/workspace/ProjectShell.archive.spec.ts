/** 归档项目：谁是所有者要单独问后端，而问的时机不能跟着挂载走。
 *
 * 归档了的项目不在项目清单里，`openedProject` 得单独问一句才知道所有者是谁、才画
 * 得出「取消归档」。拆分前这句问挂在 ProjectAccessNotice 的 onMounted 上——它是
 * accessDenied 变成 'archived' 之后才挂载的，时机刚好；搬进容器后挂在容器的
 * onMounted 上就错了：容器一开始就挂载，那会儿 openProject 的回信还没到，
 * accessDenied 还是 null，问就问丢了，所有者于是只看得到「离开」。
 *
 * 这里断整条链：状态变档才问、问到了所有者才画得出按钮、点按钮才调后端。
 */
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'

// 断言按中文写；测试环境默认是英文界面。
beforeEach(() => setLocale('zh-CN'))

const state = vi.hoisted(() => ({
  accessDenied: null as 'unauthenticated' | 'forbidden' | 'archived' | null,
  openedProject: null as { owner_handle: string; name: string } | null,
  projectName: null as string | null,
  error: null as string | null,
  activeTopicId: null as string | null,
  activeDmPeer: null as string | null,
  openProject: vi.fn(),
  loadOpenedProject: vi.fn(),
  unarchiveOpenProject: vi.fn(),
  refreshUnread: vi.fn(),
  refreshTopics: vi.fn(),
  rememberTopic: vi.fn(),
}))

// 库要响应式：测试里改的是同一个 reactive 代理，容器的 watch 才看得见这次变档。
vi.mock('@/stores/workspace', async () => {
  const { reactive } = await import('vue')
  return { useWorkspaceStore: () => reactive(state) }
})
vi.mock('@/stores/title', () => ({
  usePageTitleStore: () => ({ setDynamicTitle: vi.fn(), clearDynamicTitle: vi.fn() }),
}))
vi.mock('@/me', () => ({ myHandle: () => 'me' }))

import ProjectShell from './ProjectShell.vue'

import { useWorkspaceStore } from '@/stores/workspace'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  state.accessDenied = null
  state.openedProject = null
  state.projectName = null
  state.error = null
})

function shell() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<div />' } }],
  })
  return render(ProjectShell, {
    props: { projectId: 'p' },
    global: {
      plugins: [router, createVuetify({ components, directives })],
      stubs: { RouterView: true, VSnackbar: true },
    },
  })
}

it('归档的回信到了才问谁是所有者', async () => {
  shell()
  // 挂载那一下 openProject 的回信还没到，这时候问只会问在 null 上。
  expect(state.loadOpenedProject).not.toHaveBeenCalled()
  useWorkspaceStore().accessDenied = 'archived'
  await waitFor(() => expect(state.loadOpenedProject).toHaveBeenCalledTimes(1))
})

it('问到了所有者，才画得出「取消归档」，点了才调后端', async () => {
  const { getByRole } = shell()
  const store = useWorkspaceStore()
  store.accessDenied = 'archived'
  await waitFor(() => expect(state.loadOpenedProject).toHaveBeenCalledTimes(1))
  // 后端答了：所有者是当前用户。
  store.openedProject = { owner_handle: 'me', name: '项目' }
  const button = await waitFor(() => getByRole('button', { name: '取消归档' }))
  await fireEvent.click(button)
  expect(state.unarchiveOpenProject).toHaveBeenCalledTimes(1)
})
