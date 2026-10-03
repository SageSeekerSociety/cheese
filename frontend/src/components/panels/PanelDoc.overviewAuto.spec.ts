/** ②③ 只长在总览房间的文档下面。
 *
 * 三块总览是项目级的：只有根话题的文档是「整个项目」那一份，别的房间的文档写的
 * 是它自己。在那些房间里再挂一份项目全局，读的人会以为这两块说的是这个房间。
 */
import type { Component } from 'vue'
import type { Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getDocNodes = vi.fn()
const getOverviewAuto = vi.fn()

// The document's version history: the last edit is read on open; none here.
vi.mock('../../api/docThreads', () => ({
  listDocThreads: async () => ({ data: [], total: 0 }),
  writeDocThread: async () => ({}),
}))
vi.mock('../../api/docHistory', () => ({
  getDocVersions: async () => ({ versions: [], cursor: null }),
  restoreDocVersion: async () => ({}),
}))
vi.mock('../../composables/useDocCollab', async () => ({
  useDocCollab: (await import('../../test/fakeDocCollab')).useFakeDocCollab,
}))
vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getDocNodes: (...a: unknown[]) => getDocNodes(...a),
    getOverviewAuto: (...a: unknown[]) => getOverviewAuto(...a),
  }
})

import { seedRoom } from '../../test/fakeDocCollab'

import PanelDoc from './PanelDoc.vue'

import { setLocale } from '@/i18n'

const Doc = PanelDoc as unknown as Component

const ROOT = {
  id: 'root-1',
  project_id: 'p1',
  parent_id: null,
  title: '项目总览',
  kind: 'root',
  status: 'active',
  created_by: 'u',
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
} as Topic

const ROOM = { ...ROOT, id: 't1', title: '干活的房间', kind: 'topic' } as Topic

const BLOCKS = [
  {
    key: 'active_topics',
    title: '现在在做什么',
    items: [{ kind: 'topic', topic_id: 't2', title: '分页接口', owner: null, status: '还没开活', conclusion: null }],
  },
]

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  setLocale('zh-CN')
  getOverviewAuto.mockReset()
  seedRoom(ROOT.id, '## 项目是什么\n\n给高中生做算法课。')
  seedRoom(ROOM.id, '## 项目是什么\n\n给高中生做算法课。')
  getDocNodes.mockResolvedValue({ data: [], total: 0 })
  getOverviewAuto.mockResolvedValue({ root_topic_id: 'root-1', blocks: BLOCKS })
})

function open(topic: Topic) {
  return render(Doc, {
    props: { topic, activityTick: 0, topicList: [] },
    global: { plugins: [vuetify] },
  })
}

describe('总览房间的文档下面', () => {
  it('根话题的自动汇总紧跟正文，评论放在独立侧栏', async () => {
    const { container, findByText } = open(ROOT)

    await findByText('现在在做什么')
    expect(container.textContent).toContain('给高中生做算法课。')
    expect(container.textContent).toContain('平台自动生成，不可编辑')
    // 自动汇总仍属于正文阅读区；评论侧栏不插进正文的阅读顺序。
    const page = container.querySelector('.doc-page') as HTMLElement
    const editor = page.querySelector('.doc-editor-wrap')!
    const overview = page.querySelector('.overview-auto')!
    expect(editor).not.toBeNull()
    expect(overview).not.toBeNull()
    expect(editor.compareDocumentPosition(overview) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(page.querySelector('.doc-comments')).toBeNull()
    const panel = container.querySelector('.doc-comment-panel')!
    expect(panel.querySelector('.doc-comments')).not.toBeNull()
    expect(panel.contains(page)).toBe(false)
  })

  it('别的房间不挂这一栏 —— 它们的文档说的是房间自己', async () => {
    const { container, findByText } = open(ROOM)

    await findByText('给高中生做算法课。')
    expect(container.querySelector('.overview-auto')).toBeNull()
    await waitFor(() => expect(getOverviewAuto).not.toHaveBeenCalled())
  })
})
