import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import { render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'

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
// 这一页的数据源是缓存的，每个用例先摆好它看到的那一份，再渲染。
const state = vi.hoisted(() => ({ payload: {} as Record<string, unknown> }))
vi.mock('@/composables/useCachedResource', () => ({
  useCachedResource: () => ({
    data: ref({
      projectName: '项目',
      rootTopicId: 'root',
      weeklies: [],
      memoryEntries: [],
      ...state.payload,
    }),
    loading: ref(false),
    error: ref(null),
  }),
}))
vi.mock('../me', () => ({ myHandle: () => 'writer', myId: () => 'writer' }))
// 章程是项目房间的文档面板：评论和节点照旧从接口读，这里给空的。
vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    getDocNodes: async () => ({ data: [] }),
  }
})
vi.mock('@tiptap/extension-drag-handle-vue-3', () => ({ DragHandle: { render: () => null } }))
vi.mock('../composables/useDocCollab', async () => ({
  useDocCollab: (await import('../test/fakeDocCollab')).useFakeDocCollab,
}))

import { remoteEdit, seedRoom } from '../test/fakeDocCollab'

import ProjectDocsView from './ProjectDocsView.vue'

// These assertions read the Chinese copy; the English rendering is checked in its own case.
beforeEach(() => setLocale('zh-CN'))

describe('周报集', () => {
  it('按窗口列出一份真周报，并指得回它写在哪间房', async () => {
    state.payload = {
      weeklies: [
        {
          id: 'w1',
          topic_id: 'room-1',
          kind: 'weekly',
          content: '本周交付了产物页预览。',
          created_at: '2026-09-07T02:00:00Z',
          meta: { since: '2026-08-31T00:00:00+00:00', until: '2026-09-06T23:59:59+00:00' },
        },
      ],
    }
    const view = render(ProjectDocsView, {
      props: { projectId: 'p', kind: 'weeklies' },
      global: { plugins: [createVuetify()] },
    })
    // 窗口是这一行的身份：并排摆着的几份周报，是它把它们分开的。
    expect(view.getByText('8月31日 – 9月6日')).toBeTruthy()
    expect(view.getByText('本周交付了产物页预览。')).toBeTruthy()
    expect(view.getByText('来自话题')).toBeTruthy()
    view.unmount()
  })

  it('空态说清怎么让芝士写，而不是许一个没人实现的周期', async () => {
    state.payload = {}
    const view = render(ProjectDocsView, {
      props: { projectId: 'p', kind: 'weeklies' },
      global: { plugins: [createVuetify()] },
    })
    expect(view.queryByText('周报由芝士定期产出')).toBeNull()
    expect(view.getByText(/在项目房间里 @ 芝士/)).toBeTruthy()
    expect(view.getByText('暂无周报')).toBeTruthy()
    view.unmount()
  })
})

describe('in English', () => {
  it('names the weekly window in English', async () => {
    setLocale('en')
    state.payload = {
      weeklies: [
        {
          id: 'w1',
          topic_id: 'room-1',
          kind: 'weekly',
          content: 'Shipped the artifact preview.',
          created_at: '2026-09-07T02:00:00Z',
          meta: { since: '2026-08-31T00:00:00+00:00', until: '2026-09-06T23:59:59+00:00' },
        },
      ],
    }
    const view = render(ProjectDocsView, {
      props: { projectId: 'p', kind: 'weeklies' },
      global: { plugins: [createVuetify()] },
    })
    expect(view.getByText('Aug 31 – Sep 6')).toBeTruthy()
    expect(view.getByText('From topic')).toBeTruthy()
    expect(view.getByText('Weekly reports')).toBeTruthy()
    view.unmount()
  })
})

describe('章程', () => {
  it('打开的是项目房间那一篇协同文档，谁在房间里改的都在这儿', async () => {
    seedRoom('root', '我们给高中生做算法课。')
    state.payload = { rootTopic: { id: 'root', kind: 'root', title: '项目', project_id: 'p' } }
    const view = render(ProjectDocsView, {
      props: { projectId: 'p', kind: 'charter' },
      global: { plugins: [createVuetify()] },
    })
    await waitFor(() => expect(view.container.querySelector('.doc-prose')?.textContent).toContain('算法课'))

    remoteEdit('root', '我们给高中生做算法课。\n\n每周二上课。')

    await waitFor(() => expect(view.container.querySelector('.doc-prose')?.textContent).toContain('每周二上课'))
    view.unmount()
  })
})
