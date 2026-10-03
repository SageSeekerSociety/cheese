import type { Notification } from '@/network/api/notifications/types'

import { render } from '@testing-library/vue'
import { beforeAll, expect, it } from 'vitest'

import RenderCheeseQuestionNotification from './RenderCheeseQuestionNotification.vue'

import i18n, { setLocale } from '@/i18n'

beforeAll(() => setLocale('zh-CN'))

function question(extra: Record<string, string> = {}): Notification {
  return {
    id: 1,
    type: 'CHEESE_QUESTION',
    read: false,
    createdAt: 0,
    entities: {},
    contextMetadata: {
      projectId: 'p1',
      topicId: 't1',
      topicTitle: '预算复核',
      question: '预算按哪个口径统计',
      ...extra,
    },
  }
}

it('an open question says the turn is paused and waits for an answer', () => {
  const view = render(RenderCheeseQuestionNotification, {
    props: { notification: question() },
    global: { plugins: [i18n] },
  })

  expect(view.getByText('预算按哪个口径统计')).toBeTruthy()
  expect(view.getByText('在「预算复核」，已暂停，待你回答')).toBeTruthy()
})

it('an answered question says what was chosen, not that it is still waiting', () => {
  const view = render(RenderCheeseQuestionNotification, {
    props: { notification: question({ answered: '按项目' }) },
    global: { plugins: [i18n] },
  })

  expect(view.getByText('在「预算复核」，已回答：按项目')).toBeTruthy()
  expect(view.queryByText(/待你回答/)).toBeNull()
})
