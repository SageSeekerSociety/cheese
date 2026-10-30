import { createVuetify } from 'vuetify'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'

// 确认框回什么由这一格决定：`wait` 解出真就是人点了确定。
const dialogMock = vi.hoisted(() => ({ confirm: vi.fn() }))

// 文档的链接那一侧（#3827）：打开文档时问一次被引用、打 # 时问一次能引用的文档。这里不关心。
vi.mock('@/api/docLinks', () => ({ getDocumentBacklinks: async () => ({ links_to: [], linked_from: [] }) }))
vi.mock('@/lib/linkableDocuments', () => ({ linkableDocuments: async () => [] }))
vi.mock('@/plugins/dialog', async () => ({
  ...(await vi.importActual<typeof import('@/plugins/dialog')>('@/plugins/dialog')),
  useDialog: () => ({ confirm: dialogMock.confirm }),
}))

// The document's version history: the last edit is read on open; none here.
vi.mock('../api/docThreads', () => ({
  listDocThreads: async () => ({ data: [], total: 0 }),
  writeDocThread: async () => ({}),
}))
vi.mock('../api/docHistory', () => ({
  getDocVersions: async () => ({ versions: [], cursor: null }),
  restoreDocVersion: async () => ({}),
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('../me', () => ({ myHandle: () => 'writer', myId: () => 'writer' }))
// 项目目标是那一篇文档的面板：评论和节点照旧从接口读，这里给空的。
vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    getDocNodes: async () => ({ data: [] }),
    deleteMemory: vi.fn(),
    restoreMemoryChange: vi.fn(),
    // 删除、恢复之后面板重读记忆和改动记录；这几个用例看的是点下去之后发了什么。
    listMemory: vi.fn(async () => ({ data: [] })),
    listMemoryChanges: vi.fn(async () => ({ data: [] })),
    getProject: vi.fn(),
    getTopic: vi.fn(),
  }
})
// 项目目标开的是那一份文档，面板会读它叫什么。
vi.mock('../api/projectDocuments', async () => ({
  ...(await vi.importActual<typeof import('../api/projectDocuments')>('../api/projectDocuments')),
  getDocumentAbout: async (id: string) => ({ id, title: '项目目标' }),
  getProjectOverview: vi.fn(),
}))
vi.mock('@tiptap/extension-drag-handle-vue-3', () => ({ DragHandle: { render: () => null } }))
vi.mock('../api/docCollab', async () => ({
  ...(await vi.importActual<typeof import('../api/docCollab')>('../api/docCollab')),
  // 测试里房间的文档就用房间的 id 来认：fakeDocCollab 按它预置文档。
  getRoomDocument: async (topicId: string) => ({ id: topicId }),
}))
vi.mock('../composables/useDocCollab', async () => ({
  useDocCollab: (await import('../test/fakeDocCollab')).useFakeDocCollab,
}))

import { deleteMemory, getProject, getTopic, restoreMemoryChange } from '../api'
import { getProjectOverview } from '../api/projectDocuments'
import { remoteEdit, seedRoom } from '../test/fakeDocCollab'

import ProjectDocsView from './ProjectDocsView.vue'

import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'
import { useWorkspaceStore } from '@/stores/workspace'
import { seedProject, seedProjects } from '@/test/seedQueries'

// 这一页读的是缓存里这个项目、这一面的那一份：每个用例先摆好它看到的，再渲染。
function show(kind: 'charter' | 'weeklies' | 'memory', payload: Record<string, unknown> = {}) {
  queryClient.setQueryData(keys.projectDocs('p', kind), {
    rootTopicId: 'root',
    rootTopic: null,
    overviewId: null,
    weeklies: [],
    memoryEntries: [],
    ...payload,
  })
  return render(ProjectDocsView, {
    props: { projectId: 'p', kind },
    global: { plugins: [createVuetify()] },
  })
}

// These assertions read the Chinese copy; the English rendering is checked in its own case.
beforeEach(() => {
  setLocale('zh-CN')
  vi.mocked(deleteMemory).mockReset()
  vi.mocked(restoreMemoryChange).mockReset()
  dialogMock.confirm.mockReset()
  // 默认「点了确定」：取消那一格在下面的用例里单独摆。
  dialogMock.confirm.mockImplementation(() => ({ wait: async () => true }))
})

describe('周报集', () => {
  it('按窗口列出一份真周报，并指得回它写在哪间房', async () => {
    const view = show('weeklies', {
      weeklies: [
        {
          id: 'w1',
          conversation_id: 'room-1',
          kind: 'weekly',
          content: '本周交付了产物页预览。',
          created_at: '2026-09-07T02:00:00Z',
          meta: { since: '2026-08-31T00:00:00+00:00', until: '2026-09-06T23:59:59+00:00' },
        },
      ],
    })
    // 窗口是这一行的身份：并排摆着的几份周报，是它把它们分开的。
    expect(view.getByText('8月31日 – 9月6日')).toBeTruthy()
    expect(await view.findByText('本周交付了产物页预览。')).toBeTruthy()
    expect(view.getByText('来自频道')).toBeTruthy()
    view.unmount()
  })

  it('空态说清怎么让芝士写，而不是许一个没人实现的周期', async () => {
    const view = show('weeklies')
    expect(view.queryByText('周报由芝士定期产出')).toBeNull()
    expect(view.getByText(/在「综合」里 @ 芝士/)).toBeTruthy()
    expect(view.getByText('暂无周报')).toBeTruthy()
    view.unmount()
  })
})

describe('in English', () => {
  it('names the weekly window in English', async () => {
    setLocale('en')
    const view = show('weeklies', {
      weeklies: [
        {
          id: 'w1',
          conversation_id: 'room-1',
          kind: 'weekly',
          content: 'Shipped the artifact preview.',
          created_at: '2026-09-07T02:00:00Z',
          meta: { since: '2026-08-31T00:00:00+00:00', until: '2026-09-06T23:59:59+00:00' },
        },
      ],
    })
    expect(view.getByText('Aug 31 – Sep 6')).toBeTruthy()
    expect(view.getByText('From channel')).toBeTruthy()
    expect(view.getByText('Weekly reports')).toBeTruthy()
    view.unmount()
  })
})

describe('项目目标', () => {
  it('打开的是项目目标那一篇协同文档，谁在别处改的都在这儿', async () => {
    seedRoom('overview', '我们给高中生做算法课。')
    const view = show('charter', {
      rootTopic: { id: 'root', kind: 'root', title: '项目', project_id: 'p' },
      overviewId: 'overview',
    })
    await waitFor(() => expect(view.container.querySelector('.doc-prose')?.textContent).toContain('算法课'))

    remoteEdit('overview', '我们给高中生做算法课。\n\n每周二上课。')

    await waitFor(() => expect(view.container.querySelector('.doc-prose')?.textContent).toContain('每周二上课'))
    view.unmount()
  })

  // 「总览是哪一份」只要项目 id：冷开这一面时它和项目本身一起去问，不排在项目后面。
  it('冷开时总览和项目一起去问，不等项目回来', async () => {
    vi.mocked(getProject).mockReturnValue(new Promise(() => {}))
    vi.mocked(getProjectOverview).mockReturnValue(new Promise(() => {}))
    render(ProjectDocsView, {
      props: { projectId: 'cold', kind: 'charter' },
      global: { plugins: [createVuetify()] },
    })
    await waitFor(() => expect(getProjectOverview).toHaveBeenCalledWith('cold'))
  })

  it('项目话题清单里已经有根房间时直接复用，不再单独请求一次根房间', async () => {
    seedRoom('overview-cached', '复用已缓存的根房间。')
    vi.mocked(getTopic).mockReset()
    vi.mocked(getProject).mockResolvedValue({
      id: 'p-cached',
      name: '项目',
      root_topic_id: 'root-cached',
    } as never)
    vi.mocked(getProjectOverview).mockResolvedValue({ id: 'overview-cached' })
    queryClient.setQueryData(keys.projectTopics('p-cached'), {
      data: [{ id: 'root-cached', kind: 'root', title: '综合', project_id: 'p-cached' }],
      total: 1,
    })

    const view = render(ProjectDocsView, {
      props: { projectId: 'p-cached', kind: 'charter' },
      global: { plugins: [createVuetify()] },
    })

    await waitFor(() => expect(view.container.querySelector('.doc-prose')?.textContent).toContain('复用已缓存的根房间'))
    expect(getTopic).not.toHaveBeenCalled()
    view.unmount()
  })
})

describe('称呼项目的 AI 队友用它自己的名字', () => {
  it('队友改名叫小杉，项目目标的提示里称呼的是小杉', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    seedProjects([])
    seedProject('p', {
      topics: [],
      members: [{ user_handle: 'cheese-742841cae293', name: '小杉', agent: true, project_default: true }],
      unread: {},
      privateUnread: {},
      notifyLevels: {},
    })
    const store = useWorkspaceStore()
    store.openProject('p')
    await waitFor(() => expect(store.agentName).toBe('小杉'))
    queryClient.setQueryData(keys.projectDocs('p', 'charter'), {
      rootTopicId: 'root',
      rootTopic: null,
      overviewId: null,
      weeklies: [],
      memoryEntries: [],
    })

    const view = render(ProjectDocsView, {
      props: { projectId: 'p', kind: 'charter' },
      global: { plugins: [createVuetify(), pinia] },
    })

    expect(view.container.textContent).toContain('小杉')
    expect(view.container.textContent).not.toContain('芝士')
    view.unmount()
  })
})

describe('记忆', () => {
  // 树怎么分作用域见 `docs/manual/dev/memory.md`：`private` 是这个人自己的那一份，
  // 其余是项目共享的。这一页按它贴标签，所以两者的名字要对得上。
  const one = {
    id: 'm1',
    scope: 'private',
    content: '喜欢用 pnpm',
    created_at: '2026-09-25T00:00:00Z',
    updated_at: '2026-09-25T00:00:00Z',
  }
  const shared = {
    id: 'm2',
    scope: 'team',
    content: '部署脚本在 deploy/deploy.sh',
    created_at: '2026-09-24T00:00:00Z',
    updated_at: '2026-09-24T00:00:00Z',
  }

  function mountMemory() {
    return show('memory', { memoryEntries: [one, shared] })
  }

  it('一条记忆一份作用域的名字：自己的那份叫个人记忆，其余叫项目记忆', async () => {
    const view = mountMemory()
    await waitFor(() => expect(view.getByText('喜欢用 pnpm')).toBeTruthy())

    expect(view.getByText(/个人记忆/)).toBeTruthy()
    expect(view.getByText(/项目记忆/)).toBeTruthy()
    view.unmount()
  })

  it('删一条记忆要先确认，点了确定才删', async () => {
    const view = mountMemory()
    await waitFor(() => expect(view.getByText('喜欢用 pnpm')).toBeTruthy())

    await fireEvent.click(view.container.querySelector('.memory-card__del') as Element)

    // 行里的入口是灰的，这一下确认才是红的；它说得出删掉之后去哪里找回来。
    expect(dialogMock.confirm).toHaveBeenCalledWith('删除后芝士不再记得它，30 天内可在「最近改动」里恢复。', {
      title: '删除这条记忆？',
      confirmLabel: '删除这条记忆',
      danger: true,
    })
    await waitFor(() => expect(deleteMemory).toHaveBeenCalledWith('m1'))
    await waitFor(() => expect(view.queryByText('喜欢用 pnpm')).toBeNull())
    view.unmount()
  })

  it('不能配置 AI 队友的人删得了自己的记忆，删不了项目记忆', async () => {
    const view = show('memory', { memoryEntries: [one, shared], changesSharedMemory: false })
    await waitFor(() => expect(view.getByText('喜欢用 pnpm')).toBeTruthy())

    expect(view.container.querySelectorAll('.memory-card__del')).toHaveLength(1)
    view.unmount()

    const manager = show('memory', { memoryEntries: [one, shared], changesSharedMemory: true })
    await waitFor(() => expect(manager.getByText('喜欢用 pnpm')).toBeTruthy())
    expect(manager.container.querySelectorAll('.memory-card__del')).toHaveLength(2)
    manager.unmount()
  })

  it('说取消就不删，那条记忆还在', async () => {
    dialogMock.confirm.mockImplementation(() => ({ wait: async () => false }))
    const view = mountMemory()
    await waitFor(() => expect(view.getByText('喜欢用 pnpm')).toBeTruthy())

    await fireEvent.click(view.container.querySelector('.memory-card__del') as Element)

    expect(dialogMock.confirm).toHaveBeenCalledTimes(1)
    expect(deleteMemory).not.toHaveBeenCalled()
    expect(view.getByText('喜欢用 pnpm')).toBeTruthy()
    view.unmount()
  })

  const deletion = {
    id: 'c-deleted',
    scope: 'team',
    owner_handle: '',
    path: 'deploy-script.md',
    writer: 'dream_team',
    changed_by: 'cheese',
    created_at: '2026-09-26T00:00:00Z',
    created: false,
    deleted: true,
    diff: '--- deploy-script.md\n+++ deploy-script.md\n@@ -1 +0,0 @@\n-部署脚本在 deploy/deploy.sh\n',
  }
  const creation = {
    ...deletion,
    id: 'c-created',
    path: 'prefers-pnpm.md',
    scope: 'private',
    owner_handle: 'writer',
    writer: 'turn',
    created: true,
    deleted: false,
  }

  it('最近改动里一条被删掉的记忆能恢复，恢复的是那一条', async () => {
    const view = show('memory', { memoryChanges: [deletion, creation], changesSharedMemory: true })
    await waitFor(() => expect(view.getByText('deploy-script.md')).toBeTruthy())

    // 一次新建没有「改之前」，只有那条删除带恢复按钮。
    const buttons = view.getAllByRole('button', { name: /恢复到改动前/ })
    expect(buttons).toHaveLength(1)
    await fireEvent.click(buttons[0] as Element)

    await waitFor(() => expect(restoreMemoryChange).toHaveBeenCalledWith('c-deleted'))
    view.unmount()
  })

  it('改不动项目记忆的人也恢复不了项目记忆的改动', async () => {
    const view = show('memory', { memoryChanges: [deletion], changesSharedMemory: false })
    await waitFor(() => expect(view.getByText('deploy-script.md')).toBeTruthy())

    expect(view.queryByRole('button', { name: /恢复到改动前/ })).toBeNull()
    view.unmount()
  })
})
