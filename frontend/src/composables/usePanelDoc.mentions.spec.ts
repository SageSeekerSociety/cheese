// @vitest-environment jsdom
// The AI teammate answers a document comment only when the comment names it the
// way a chat message does — with its mention token — so a comment written as
// 「@芝士 …」 is sent with the token, in a new comment and in a reply alike.
import { effectScope } from 'vue'
import { render, screen } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

import DocThreadCard from '../components/panels/doc/DocThreadCard.vue'

import { usePanelDoc } from './usePanelDoc'

const addComment = vi.fn(async () => ({}))
vi.mock('../api', async () => ({
  ...(await vi.importActual<typeof import('../api')>('../api')),
  addComment: (...a: unknown[]) => addComment(...(a as [])),
  getDocNodes: async () => ({ data: [] }),
}))
vi.mock('./useDocCollab', async () => ({
  useDocCollab: (await import('../test/fakeDocCollab')).useFakeDocCollab,
}))

describe('asking the AI teammate in a document comment', () => {
  it('sends the comment with the teammate’s mention token', async () => {
    const scope = effectScope()
    const doc = scope.run(() =>
      usePanelDoc({ topic: null, activityTick: 0, topicList: [], agentName: '芝士', agentHandle: 'cheese-a1' })
    )!
    await doc.sendComment('t1', '@芝士 这个数字是怎么来的', '200 ms')
    expect(addComment).toHaveBeenCalledWith('t1', '<@cheese-a1> 这个数字是怎么来的', '200 ms')
    expect(doc.withMentions('@芝士 按测试结果改一下')).toBe('<@cheese-a1> 按测试结果改一下')
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
    })
    expect(screen.getByText('@芝士 来自课程要求第 3 页')).toBeTruthy()
  })
})
