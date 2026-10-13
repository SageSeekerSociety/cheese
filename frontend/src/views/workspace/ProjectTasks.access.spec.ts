// 全部任务读不到时：登录没了、人已经不在项目里（401/403）交给整页那一屏，那一屏给的是
// 去登录的路；页面上不再画一个永远重试不好的「无法加载任务」。别的失败照旧说出来。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const listProjectTasks = vi.hoisted(() => vi.fn())
vi.mock('@/api/tasks', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/tasks')>()),
  listProjectTasks,
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

import ProjectTasks from './ProjectTasks.vue'

import { ApiError } from '@/api/http'
import { setLocale, t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value: 1 })

beforeEach(() => {
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  setLocale('zh-CN')
  setActivePinia(createPinia())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const mount = () =>
  render(ProjectTasks, {
    props: { projectId: 'p1' },
    global: { plugins: [createVuetify({ components, directives })] },
  })

it('答 401 时交给整页那一屏，页面上不说「无法加载任务」', async () => {
  listProjectTasks.mockRejectedValue(new ApiError(401, 'Sign in to open this project'))
  mount()
  await waitFor(() => expect(useWorkspaceStore().accessDenied).toBe('unauthenticated'))
  expect(screen.queryByText(t('work.channelTasks.loadFailed'))).toBeNull()
})

it('断网这类失败照旧说出来，给重试', async () => {
  listProjectTasks.mockRejectedValue(new Error('network'))
  mount()
  expect(await screen.findByText(t('work.channelTasks.loadFailed'))).toBeTruthy()
})
