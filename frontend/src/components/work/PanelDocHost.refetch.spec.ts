// 概览文档打开后，正文只挂一次。
//
// 任务页、资料库文档页的数据一刷新，父层就把 `topic` 换成另一个对象（id 没变）。这一份
// 钉的正是外壳到正文这一段：换对象不能让正文被拆掉重挂 —— 拆了骨架会回来、正文重排一遍，
// 打开时看着就是闪一下。真的换了房间、换了任务或换了另一份文档，才换一份正文。
import type { Component } from 'vue'
import type { Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/docThreads', () => ({
  listDocThreads: async () => ({ data: [], total: 0 }),
  writeDocThread: async () => ({}),
}))
vi.mock('../../api/docHistory', () => ({
  getDocVersions: async () => ({ versions: [], cursor: null }),
  restoreDocVersion: async () => ({}),
}))
vi.mock('../../api/docCollab', async () => ({
  ...(await vi.importActual<typeof import('../../api/docCollab')>('../../api/docCollab')),
  // 测试里房间的文档就用房间的 id 来认：fakeDocCollab 按它预置文档。
  getRoomDocument: async (topicId: string) => ({ id: topicId }),
}))
vi.mock('../../composables/useDocCollab', async () => ({
  useDocCollab: (await import('../../test/fakeDocCollab')).useFakeDocCollab,
}))
vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return { ...actual, getDocNodes: async () => ({ data: [], total: 0 }) }
})

import { resetRooms, seedRoom } from '../../test/fakeDocCollab'

import PanelDocHost from './PanelDocHost.vue'

import { setLocale } from '@/i18n'

// 断言按中文文案写：默认 locale 是 en，这里钉回 zh-CN。
beforeEach(() => setLocale('zh-CN'))

const Doc = PanelDocHost as unknown as Component

/** 一间房；每次调用都是一个新对象，和父层重新取数时一样。 */
function room(id: string, title: string): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: null,
    title,
    kind: 'topic',
    status: 'active',
    created_by: 'u',
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
  } as Topic
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  resetRooms()
})

function open(topic: Topic) {
  return render(Doc, {
    props: { topic, activityTick: 0, topicList: [] },
    global: { plugins: [vuetify] },
  })
}

/** 让挂起的东西先落地：这几步都是 await 出来的，没有可等的 DOM 变化。 */
function settle() {
  return new Promise((resolve) => setTimeout(resolve, 20))
}

describe('概览文档打开后', () => {
  it('页面重新取数不会把正文拆掉重挂', async () => {
    seedRoom('t1', '## 一段\n\n正文')
    const { container, rerender } = open(room('t1', '话题'))
    await waitFor(() => expect(container.querySelector('.doc-editor')?.textContent).toContain('正文'))
    const body = container.querySelector('.doc-editor')

    // 数据重新取了一次：还是这间房，父层递下来的是另一个对象。
    await rerender({ topic: room('t1', '话题'), activityTick: 0, topicList: [] })
    await settle()

    expect(container.querySelector('.doc-skel'), '重新取数不该退回骨架').toBeNull()
    expect(container.querySelector('.doc-editor'), '正文不该被拆掉重挂').toBe(body)
  })

  it('真换了房间才换正文', async () => {
    seedRoom('t1', '## 一段\n\n第一份正文')
    seedRoom('t2', '## 一段\n\n第二份正文')
    const { container, rerender } = open(room('t1', '第一间'))
    await waitFor(() => expect(container.querySelector('.doc-editor')?.textContent).toContain('第一份正文'))

    await rerender({ topic: room('t2', '第二间'), activityTick: 0, topicList: [] })

    await waitFor(() => expect(container.querySelector('.doc-editor')?.textContent).toContain('第二份正文'))
    expect(container.querySelector('.doc-editor')?.textContent).not.toContain('第一份正文')
  })
})
