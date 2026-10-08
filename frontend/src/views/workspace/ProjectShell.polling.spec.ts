import { cleanup, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'

const store = vi.hoisted(() => ({ openProject: vi.fn(), refreshUnread: vi.fn(), refreshTopics: vi.fn(), error: null }))
// 这一份只管轮询，所以路由是个哑的：跳转计数接管它那两个函数，空闲预热问到几个页名时
// 一个都找不到（`getRoutes` 空表），两件事都安静地什么都不做。
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), getRoutes: () => [] }))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => store }))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: {} }), useRouter: () => router }))
import ProjectShell from './ProjectShell.vue'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
})
it('pauses hidden tab polling and refreshes when the reader returns', async () => {
  vi.useFakeTimers()
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
  const view = render(ProjectShell, {
    props: { projectId: 'p' },
    global: { plugins: [createPinia()], stubs: { RouterView: true, VSnackbar: true } },
  })
  await vi.advanceTimersByTimeAsync(90_000)
  expect(store.refreshTopics).not.toHaveBeenCalled()
  visibility.mockReturnValue('visible')
  document.dispatchEvent(new Event('visibilitychange'))
  expect(store.refreshTopics).toHaveBeenCalledTimes(1)
  expect(store.refreshUnread).toHaveBeenCalledTimes(1)
  view.unmount()
  document.dispatchEvent(new Event('visibilitychange'))
  await vi.advanceTimersByTimeAsync(30_000)
  expect(store.refreshTopics).toHaveBeenCalledTimes(1)
})
