/** 资料库读失败：整块换成失败块（§3.10），给服务端原话和一条重试；401/403 说没权限、
 *  不给重试。以前这一页失败时什么都不画（错误只记在 `loadError` 里，没人渲染它）。
 */
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listProjectLibrary = vi.fn()

// 带上真实的 ApiError：401/403 的判据是 `e instanceof ApiError`。
vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    listProjectLibrary: (...a: unknown[]) => listProjectLibrary(...a),
    deleteLibraryFile: vi.fn(),
    downloadFile: vi.fn(),
    libraryFileRawUrl: (projectId: string, path: string) => `/api/projects/${projectId}/library/raw?path=${path}`,
  }
})

vi.mock('../lib/libraryApi', () => ({
  uploadLibraryFile: vi.fn(),
  replaceLibraryFile: vi.fn(),
  libraryFileBytes: vi.fn(),
}))

import { ApiError } from '../api'
import { setLocale, t } from '../i18n'

import ProjectLibraryView from './ProjectLibraryView.vue'

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
    })
  }
})

beforeEach(() => {
  listProjectLibrary.mockReset()
})

const Blank = defineComponent({ render: () => h('div') })

async function mount() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/projects/:projectId/library', name: 'project-library', component: ProjectLibraryView, props: true },
      { path: '/projects/:projectId/topics/:topicId', name: 'workspace-topic', component: Blank },
    ],
  })
  await router.push('/projects/p1/library')
  await router.isReady()
  const Host = defineComponent({ setup: () => () => h(components.VApp, null, () => h(RouterView)) })
  return render(Host, { global: { plugins: [createVuetify({ components, directives }), router, createPinia()] } })
}

describe('资料库读失败', () => {
  it('整块换成失败块：服务端原话 + 重试，不显示「暂无资料」', async () => {
    listProjectLibrary.mockRejectedValue(new Error('HTTP 503 for /library'))
    mount()
    expect(await screen.findByText(t('work.library.loadError'))).toBeTruthy()
    expect(screen.getByText('HTTP 503 for /library')).toBeTruthy()
    expect(screen.queryByText(t('work.library.empty')), '失败不能显示成「暂无资料」').toBeNull()
    expect(screen.getByRole('button', { name: t('global.loadError.retry') })).toBeTruthy()
  })

  it('401/403：说没权限，不给重试', async () => {
    listProjectLibrary.mockRejectedValue(new ApiError(403, 'forbidden'))
    mount()
    expect(await screen.findByText(t('global.loadError.forbidden'))).toBeTruthy()
    expect(screen.queryByRole('button', { name: t('global.loadError.retry') }), '没权限不该给重试').toBeNull()
  })

  it('读成功时不画失败块', async () => {
    listProjectLibrary.mockResolvedValue({ data: [], total: 0 })
    mount()
    await waitFor(() => expect(screen.queryByText(t('work.library.loadError'))).toBeNull())
    expect(await screen.findByText(t('work.library.empty'))).toBeTruthy()
  })
})
