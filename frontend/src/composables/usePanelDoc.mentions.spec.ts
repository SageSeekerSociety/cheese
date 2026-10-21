// @vitest-environment jsdom
// The AI teammate answers a document comment only when the comment names it the
// way a chat message does — with its mention token — so a comment written as
// 「@芝士 …」 is sent with the token, in a new comment and in a reply alike.
import type { Topic } from '../cx_types'

import { effectScope, nextTick } from 'vue'
import { createVuetify } from 'vuetify'
import { render, screen } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

import DocThreadCard from '../components/panels/doc/DocThreadCard.vue'

import { usePanelDoc } from './usePanelDoc'

const addComment = vi.fn(async () => ({}))

// 文档的链接那一侧（#3827）：打开文档时问一次被引用、打 # 时问一次能引用的文档。这里不关心。
vi.mock('@/api/docLinks', () => ({ getDocumentBacklinks: async () => ({ links_to: [], linked_from: [] }) }))
vi.mock('@/lib/linkableDocuments', () => ({ linkableDocuments: async () => [] }))
vi.mock('../api', async () => ({
  ...(await vi.importActual<typeof import('../api')>('../api')),
  addComment: (...a: unknown[]) => addComment(...(a as [])),
  getDocNodes: async () => ({ data: [] }),
}))
vi.mock('../api/docHistory', () => ({
  getDocVersions: async () => ({ versions: [], cursor: null }),
  restoreDocVersion: async () => ({}),
}))
vi.mock('../api/docCollab', async () => ({
  ...(await vi.importActual<typeof import('../api/docCollab')>('../api/docCollab')),
  getRoomDocument: async () => ({ id: 'd1' }),
}))
vi.mock('./useDocCollab', async () => ({
  useDocCollab: (await import('../test/fakeDocCollab')).useFakeDocCollab,
}))

describe('asking the AI teammate in a document comment', () => {
  it('sends the comment with the teammate’s mention token', async () => {
    const scope = effectScope()
    const topic = { id: 't1', project_id: 'p1' } as Topic
    const doc = scope.run(() => usePanelDoc({ topic, activityTick: 0, agentName: '芝士', agentHandle: 'cheese-a1' }))!
    await vi.waitFor(() => expect(doc.documentId.value).toBe('d1'))
    await nextTick()
    await doc.sendComment('@芝士 这个数字是怎么来的', '200 ms')
    expect(addComment).toHaveBeenCalledWith('d1', '<@cheese-a1> 这个数字是怎么来的', '200 ms')
    expect(doc.withMentions('@芝士 按测试结果改一下')).toBe('<@cheese-a1> 按测试结果改一下')
    scope.stop()
  })

  // 备注名不是名册上的名字：后端只认名册上的名字和 handle，所以评论里 @ 的是
  // 备注名时，写进正文的必须已经是 <@handle>，否则被 @ 的人收不到通知。
  it('sends a person mentioned by their remark as their handle token', async () => {
    const scope = effectScope()
    const topic = { id: 't1', project_id: 'p1' } as Topic
    const doc = scope.run(() =>
      usePanelDoc({ topic, activityTick: 0, agentName: '芝士', agentHandle: 'cheese-a1' }, () => [
        { handle: 'u1', label: '老王' },
      ])
    )!
    expect(doc.withMentions('@老王 看一下这里')).toBe('<@u1> 看一下这里')
    scope.stop()
  })

  it('reads the token in a comment as the teammate’s name', () => {
    render(DocThreadCard, {
      props: {
        thread: {
          comment: { id: 'c1', author: 'alice', content: '<@cheese-a1> 来自课程要求第 3 页', anchor_quote: '200 ms' },
          revision: 1,
          state: 'open',
          replies: [],
        },
        active: false,
        place: 'marked',
        busy: false,
        unknown: false,
        agentName: '芝士',
        mentionNames: { 'cheese-a1': '芝士' },
        nameOf: (handle: string) => handle,
        writable: true,
        draftKey: 'mentions-draft',
      } as never,
      global: { plugins: [createVuetify()] },
    })
    // The teammate's name is a chip inside the line, so the line is read whole.
    expect(screen.getByText(/来自课程要求第 3 页/).textContent).toBe('@芝士 来自课程要求第 3 页')
  })
})
