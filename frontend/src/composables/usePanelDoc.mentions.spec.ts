// @vitest-environment jsdom
// The AI teammate answers a document comment only when the comment names it the
// way a chat message does — with its mention token — so a comment written as
// 「@芝士 …」 is sent with the token, in a new comment and in a reply alike.
import { effectScope } from 'vue'
import { render, screen } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

import DocCommentBody from '../components/panels/doc/DocCommentBody.vue'

import { usePanelDoc } from './usePanelDoc'

const addComment = vi.fn(async () => ({}))
vi.mock('../api', async () => ({
  ...(await vi.importActual<typeof import('../api')>('../api')),
  addComment: (...a: unknown[]) => addComment(...(a as [])),
  getComments: async () => ({ data: [] }),
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
    await doc.sendComment('t1', '@芝士 这个数字是怎么来的', 'n1', '200 ms')
    expect(addComment).toHaveBeenCalledWith('t1', '<@cheese-a1> 这个数字是怎么来的', 'n1', '200 ms')
    expect(doc.withMentions('@芝士 按测试结果改一下')).toBe('<@cheese-a1> 按测试结果改一下')
    scope.stop()
  })

  it('reads the token in a comment as the teammate’s name', () => {
    render(DocCommentBody, {
      props: {
        comment: { id: 'c1', content: '<@cheese-a1> 来自课程要求第 3 页' },
        anchor: null,
        quoteStatus: 'unique',
        expanded: false,
        overflowing: false,
        mentionNames: { 'cheese-a1': '芝士' },
      } as never,
    })
    expect(screen.getByText('@芝士 来自课程要求第 3 页')).toBeTruthy()
  })
})
