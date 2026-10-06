import type { EntityInfo, Notification } from '@/network/api/notifications/types'

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

function view(notification: Notification) {
  return render(NotificationAvatar, {
    props: { notification },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

function icon(notification: Notification): Element {
  const el = view(notification).container.querySelector('.v-icon')
  if (!el) throw new Error('no icon rendered')
  return el
}

function person(over: Partial<EntityInfo> = {}): EntityInfo {
  return { id: '1', type: 'user', name: 'Alice', handle: 'alice', ...over }
}

function from(entities: Record<string, EntityInfo>): Notification {
  return {
    id: 2,
    type: 'MENTION',
    read: false,
    createdAt: 0,
    entities,
    contextMetadata: {},
  }
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

it('the person who sent it gets their own face when they have one', () => {
  const { container } = view(from({ sender: person({ avatarUrl: 'https://cdn.example.com/avatars/10' }) }))

  const img = container.querySelector('.v-img img')
  expect(img?.getAttribute('src')).toBe('https://cdn.example.com/avatars/10')
  expect(container.querySelector('.v-icon')).toBeNull()
})

it('a person who never picked an avatar gets their coloured initial, not the type icon', () => {
  const { container } = view(from({ sender: person() }))

  // 退回类型图标 = 这条通知看不出是谁发来的，正是这批要修的那种「降级显示」。
  expect(container.querySelector('.v-icon')).toBeNull()
  const initial = container.querySelector('.user-avatar-char')
  expect(initial?.textContent?.trim()).toBe('A')
})

it('a team entity is not drawn as the person who sent it, and is squared off', () => {
  const team: EntityInfo = {
    id: '7',
    type: 'team',
    name: 'Alpha',
    avatarUrl: 'https://cdn.example.com/avatars/42',
  }
  const { container } = view(from({ sender: team }))

  // 团队的头像照旧画出来，但形状是圆角方块 —— 人是圆的。
  expect(container.querySelector('.v-img img')?.getAttribute('src')).toBe('https://cdn.example.com/avatars/42')
  const tile = container.querySelector('.v-avatar') as HTMLElement
  expect(tile.style.borderRadius).not.toBe('')
})
