// 审阅一件任务时，预览里指出的一处该进哪条对话。
//
// 那一栏左边是自己的对话（任务自己那一条），页面手里另有一个频道主对话的句柄。混用的话，
// 审阅时的一句「这块改一下」会落到频道里当成另一件活；更糟的是落在没有对话的那一处时
// 整句悄悄丢掉——这正是补这条之前的样子。
import type { ChatColumnHandle } from './shared'

import { expect, it, vi } from 'vitest'

import { useReviewChat } from './reviewChat'

function handle(): ChatColumnHandle {
  return {
    linkDown: false,
    reloadFeedback: vi.fn(),
    say: vi.fn(() => true),
    submitQuestion: vi.fn(() => true),
  }
}

it('指出的那一处发进这件任务自己的对话', () => {
  const reviewChat = useReviewChat()
  const mine = handle()
  reviewChat.hold('task-1', mine)

  expect(reviewChat.say('task-1', { message: '这块留白收一下' })).toBe(true)
  expect(mine.say).toHaveBeenCalledWith('这块留白收一下', undefined)
})

it('画完的标注连同那张图一起走', () => {
  const reviewChat = useReviewChat()
  const mine = handle()
  reviewChat.hold('task-1', mine)

  const attachment = { path: 'a.png', url: 'u' } as never
  reviewChat.say('task-1', { message: '按这张改成蓝色', attachments: [attachment] })
  expect(mine.say).toHaveBeenCalledWith('按这张改成蓝色', [attachment])
})

it('两件任务各走各的对话，不串', () => {
  const reviewChat = useReviewChat()
  const first = handle()
  const second = handle()
  reviewChat.hold('task-1', first)
  reviewChat.hold('task-2', second)

  reviewChat.say('task-2', { message: '第二件的' })
  expect(second.say).toHaveBeenCalledTimes(1)
  expect(first.say).not.toHaveBeenCalled()
})

it('对话栏还没挂上（或已经卸下）时说发不出去，而不是假装发了', () => {
  const reviewChat = useReviewChat()
  expect(reviewChat.say('task-1', { message: 'x' })).toBe(false)

  const mine = handle()
  reviewChat.hold('task-1', mine)
  reviewChat.hold('task-1', null)
  expect(reviewChat.say('task-1', { message: 'x' })).toBe(false)
  expect(mine.say).not.toHaveBeenCalled()
})

it('结构化提问也走那一条对话', () => {
  const reviewChat = useReviewChat()
  const mine = handle()
  reviewChat.hold('task-1', mine)

  const request = { intent: 'ask-agent', topicId: 'task-1', content: '改一下' } as never
  expect(reviewChat.submit('task-1')(request)).toBe(true)
  expect(mine.submitQuestion).toHaveBeenCalledWith(request)
})

it('没有对话时结构化提问答「发不出去」，让面板退回拼一句话那条路', () => {
  const reviewChat = useReviewChat()
  expect(reviewChat.submit('task-1')({ intent: 'ask-agent', topicId: 'task-1', content: 'x' } as never)).toBe(false)
})
