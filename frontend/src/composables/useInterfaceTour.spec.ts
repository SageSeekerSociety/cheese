/** 帮助菜单的「界面导览」：在项目里点它要先带人去「综合」（导览只在那一页画得出来），
 *  不在项目里、或者项目没有能用的「综合」时要说话，不能点了什么都没发生。
 */
import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { fireEvent, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const listTopics = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listTopics: (...a: unknown[]) => listTopics(...a),
    listProjectMembers: vi.fn().mockResolvedValue({ data: [] }),
    listProjects: vi.fn().mockResolvedValue({ data: [] }),
    // 「综合」归档的项目：store 会把「打不开」记成 archived，再去读一行项目本身。
    getProject: vi.fn().mockResolvedValue({ id: 'p1', name: '项目一' }),
    getTopicNotifyLevels: vi.fn().mockResolvedValue({}),
    getTopicUnread: vi.fn().mockResolvedValue({}),
    getPrivateUnread: vi.fn().mockResolvedValue({}),
  }
})

vi.mock('@/api/addresses', () => ({
  resolveProject: vi.fn().mockResolvedValue(null),
  resolveNumber: vi.fn().mockRejectedValue(new Error('not here')),
  addressOf: vi.fn().mockRejectedValue(new Error('not here')),
}))

vi.mock('vuetify-sonner', () => ({ toast: vi.fn() }))

import { useInterfaceTour } from './useInterfaceTour'
import { __resetProjectTourForTests, useProjectTour } from './useProjectTour'

import i18n, { setLocale } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

const Harness = defineComponent({
  setup() {
    const tour = useInterfaceTour()
    return { tour }
  },
  template: '<button type="button" @click="tour.start()">tour</button>',
})

async function flush() {
  for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0))
}

async function mountAt(path: string, topics: unknown[]) {
  listTopics.mockResolvedValue({ data: topics })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'HomeDefault', component: { template: '<div />' } },
      { path: '/projects/:projectId/overview', name: 'workspace-overview', component: { template: '<div />' } },
      {
        path: '/projects/:projectId/channels/:topicId',
        name: 'workspace-topic',
        component: { template: '<div />' },
      },
    ],
  })
  await router.push(path)
  await router.isReady()
  const view = render(Harness, { global: { plugins: [router, createPinia(), i18n] } })
  // 打开项目、等它的频道清单到货：菜单点下去那一刻，侧栏早就在读这份清单了。
  const store = useWorkspaceStore()
  if (path.startsWith('/projects/')) {
    store.openProject('p1')
    await flush()
  }
  return { view, router, store }
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.clearAllMocks()
  __resetProjectTourForTests()
})

describe('帮助菜单里的界面导览', () => {
  it('在项目里点：先打开「综合」，再开始导览', async () => {
    const { view, router } = await mountAt('/projects/p1/overview', [
      { id: 'general', kind: 'root', status: 'active' },
      { id: 'c2', kind: 'topic', status: 'active' },
    ])
    await fireEvent.click(view.getByRole('button', { name: 'tour' }))
    await flush()

    expect(router.currentRoute.value.name).toBe('workspace-topic')
    expect(router.currentRoute.value.params.topicId).toBe('general')
    const guide = useProjectTour()
    expect(guide.active.value).toBe(true)
    expect(guide.stop.value).toBe(0)
    expect(toast).not.toHaveBeenCalled()
  })

  it('不在项目里点：说一句该去哪儿点，不开导览', async () => {
    const { view, router } = await mountAt('/', [])
    await fireEvent.click(view.getByRole('button', { name: 'tour' }))
    await flush()

    expect(router.currentRoute.value.name).toBe('HomeDefault')
    expect(toast).toHaveBeenCalledWith('导览从项目的「综合」频道开始：进那里再点一次。')
    expect(useProjectTour().active.value).toBe(false)
  })

  it('项目的「综合」归档了：同样说一句，不把人带进只读的频道', async () => {
    const { view, router } = await mountAt('/projects/p1/overview', [
      { id: 'general', kind: 'root', status: 'archived' },
    ])
    await fireEvent.click(view.getByRole('button', { name: 'tour' }))
    await flush()

    expect(router.currentRoute.value.name).toBe('workspace-overview')
    expect(toast).toHaveBeenCalled()
    expect(useProjectTour().active.value).toBe(false)
  })
})
