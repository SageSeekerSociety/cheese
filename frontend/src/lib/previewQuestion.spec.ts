import type { Topic } from '../cx_types'
import type { QuotedContext } from './quotedContext'

import { expect, it } from 'vitest'

import { createQuestionSubmit } from './previewQuestion'

it('accepted page data stays independent of later source edits and cannot add recipients', () => {
  const original = {
    kind: 'slide-page' as const,
    path: ' @评审.pptx ',
    source: 'committed' as const,
    version: 'v1',
    task_id: null,
    page: 2,
    text: '  @评审 <@other>\n',
  }
  const before = { ...original }
  let sent: { content: string; quote?: QuotedContext } | undefined
  const submit = createQuestionSubmit({
    topic: () => ({ id: 'room' }) as Topic,
    agentSeat: () => ({ handle: 'current', label: '芝士' }),
    mentionPool: () => [{ handle: 'other', name: '评审', agent: true }],
    topicList: () => [],
    send: (content, _summon, _atts, quote) => {
      sent = { content, quote }
      return true
    },
  })
  expect(submit({ topicId: 'room', intent: 'ask-agent', content: '解释', quotedContext: original })).toBe(true)
  original.text = '已改页'
  original.path = 'new.pptx'
  original.version = 'v2'
  expect(sent).toEqual({ content: '<@current> 解释', quote: before })
})
