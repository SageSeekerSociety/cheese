import type { Notification } from '@/network/api/notifications/types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { expect, it } from 'vitest'

import NotificationAvatar from './NotificationAvatar.vue'

function question(extra: Record<string, string> = {}): Notification {
  return {
    id: 1,
    type: 'CHEESE_QUESTION',
    read: false,
    createdAt: 0,
    entities: {},
    contextMetadata: { topicTitle: '预算复核', question: '预算按哪个口径统计', ...extra },
  }
}

function icon(notification: Notification): Element {
  const view = render(NotificationAvatar, {
    props: { notification },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  const el = view.container.querySelector('.v-icon')
  if (!el) throw new Error('no icon rendered')
  return el
}

it('an open question shows the waiting mark in the warning colour', () => {
  const el = icon(question())

  expect(el.classList).toContain('mdi-help-circle-outline')
  expect(el.classList).toContain('text-warning')
})

it('an answered question shows the answered mark, no longer the warning colour', () => {
  const el = icon(question({ answered: '按项目' }))

  expect(el.classList).toContain('mdi-check-circle-outline')
  expect(el.classList).toContain('text-success')
  expect(el.classList).not.toContain('text-warning')
})
