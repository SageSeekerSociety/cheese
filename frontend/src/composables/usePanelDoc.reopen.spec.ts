// @vitest-environment jsdom
// 概览文档打开后只能开一次。任务页的数据一刷新，props.topic 就换成另一个对象（id 没变），
// 这一格不能因此把协同连接拆掉重开：拆了正文就销毁、骨架重来一遍。真的换了任务、换了房间
// 或换了资料库里的文档，才换一份正文。
import type { Topic } from '../cx_types'

import { effectScope, nextTick, reactive, watch } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { resetRooms, seedRoom } from '../test/fakeDocCollab'

import { usePanelDoc } from './usePanelDoc'

const getRoomDocument = vi.fn(async (room: string) => ({ id: `doc-${room}` }))
vi.mock('../api', async () => ({
  ...(await vi.importActual<typeof import('../api')>('../api')),
  getDocNodes: async () => ({ data: [] }),
}))
vi.mock('../api/docHistory', () => ({
  getDocVersions: async () => ({ versions: [], cursor: null }),
  restoreDocVersion: async () => ({}),
}))
vi.mock('../api/docCollab', async () => ({
  ...(await vi.importActual<typeof import('../api/docCollab')>('../api/docCollab')),
  getRoomDocument: (room: string) => getRoomDocument(room),
}))
vi.mock('./useDocCollab', async () => ({
  useDocCollab: (await import('../test/fakeDocCollab')).useFakeDocCollab,
}))

function topic(id: string): Topic {
  return { id, project_id: 'p1' } as Topic
}

describe('the overview document opens once', () => {
  it('stays open when the page refetches the same room', async () => {
    resetRooms()
    seedRoom('doc-r1', '# 概览\n\n正文')
    const props = reactive({ topic: topic('r1'), taskId: null as string | null, activityTick: 0 })
    const scope = effectScope()
    const doc = scope.run(() => usePanelDoc(props))!
    const opened: unknown[] = []
    scope.run(() => watch(doc.session, (session) => opened.push(session), { flush: 'sync' }))
    await vi.waitFor(() => expect(doc.session.value).toBeTruthy())

    // 页面重新取了一次数据：还是这个房间，只是换了个对象。
    props.topic = topic('r1')
    await nextTick()
    await nextTick()

    expect(opened).toEqual([doc.session.value])
    expect(doc.documentId.value).toBe('doc-r1')
    expect(doc.loading.value).toBe(false)
    scope.stop()
  })

  it('opens the task’s own document when the task changes', async () => {
    resetRooms()
    seedRoom('doc-k1', '# 第一个任务')
    seedRoom('doc-k2', '# 第二个任务')
    const props = reactive({ topic: topic('r1'), taskId: 'k1' as string | null, activityTick: 0 })
    const scope = effectScope()
    const doc = scope.run(() => usePanelDoc(props))!
    const opened: unknown[] = []
    scope.run(() => watch(doc.session, (session) => opened.push(session), { flush: 'sync' }))
    await vi.waitFor(() => expect(doc.documentId.value).toBe('doc-k1'))

    props.taskId = 'k2'
    await vi.waitFor(() => expect(doc.documentId.value).toBe('doc-k2'))
    await vi.waitFor(() => expect(doc.session.value).toBeTruthy())

    expect(doc.loading.value).toBe(false)
    expect(opened.length).toBeGreaterThan(1)
    scope.stop()
  })

  it('opens the document it is handed, and the next one when it changes', async () => {
    resetRooms()
    seedRoom('doc-lib1', '# 第一份')
    seedRoom('doc-lib2', '# 第二份')
    const props = reactive({
      topic: null as Topic | null,
      taskId: null as string | null,
      document: { id: 'doc-lib1', projectId: 'p1', title: '第一份' },
      activityTick: 0,
    })
    const scope = effectScope()
    const doc = scope.run(() => usePanelDoc(props))!
    await vi.waitFor(() => expect(doc.session.value).toBeTruthy())
    expect(doc.documentId.value).toBe('doc-lib1')

    props.document = { id: 'doc-lib2', projectId: 'p1', title: '第二份' }
    await vi.waitFor(() => expect(doc.documentId.value).toBe('doc-lib2'))
    await vi.waitFor(() => expect(doc.session.value).toBeTruthy())
    expect(doc.loading.value).toBe(false)
    scope.stop()
  })
})
