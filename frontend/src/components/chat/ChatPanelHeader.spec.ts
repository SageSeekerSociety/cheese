// 私聊顶栏上那一位要画成一张脸 + 他的名字（`titlePeer`），不是一行字符串；不是私聊
// 的标题（本体、别的话题）照旧只写一行字，不为它凭空画一张脸。
import type { Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, describe, expect, it } from 'vitest'

import ChatPanelHeader from './ChatPanelHeader.vue'

import i18n, { setLocale } from '@/i18n'

beforeAll(() => {
  setLocale('zh-CN')
})

const topic = {
  id: 'dm-1',
  project_id: 'p1',
  parent_id: null,
  title: '私聊 · me · alice',
  kind: 'topic',
  status: 'active',
  created_by: 'me',
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
} as Topic

const base = {
  topic,
  connected: true,
  prHeader: false,
  hideHeader: false,
  prShortId: '1',
  prState: { cls: 'pr-state--open', label: '进行中' },
  titleOverride: '爱丽丝',
  backLabel: null,
}

const plugins = [createVuetify({ components, directives }), i18n]

describe('ChatPanelHeader · 私聊顶栏上那个人', () => {
  it('titlePeer 给了就画共用身份：脸 + 名，名字只写一遍', () => {
    const { container } = render(ChatPanelHeader, {
      props: { ...base, titlePeer: { handle: 'alice', name: '爱丽丝', avatarId: 5, agent: false } },
      global: { plugins },
    })

    // `class` 落在共用身份那一层的根上：`.pr-header__peer` 就是 `.identity-view`。
    const peer = container.querySelector('.pr-header__peer') as HTMLElement
    expect(peer.classList.contains('identity-view'), '顶栏上没画身份').toBe(true)
    expect(peer.querySelector('.identity-view__name')?.textContent).toContain('爱丽丝')
    expect(peer.querySelector('img')?.getAttribute('src')).toContain('/avatars/5')
    expect(container.querySelectorAll('.identity-view__name').length, '名字被写了两遍').toBe(1)
  })

  it('队友画超椭圆，不画真人的圆头像', () => {
    const { container } = render(ChatPanelHeader, {
      props: {
        ...base,
        titleOverride: '芝士',
        titlePeer: { handle: 'cheese', name: '芝士', avatarId: null, agent: true },
      },
      global: { plugins },
    })

    expect(container.querySelector('.pr-header__peer .cheese-avatar')).not.toBeNull()
  })

  it('没有 titlePeer 就照旧只写一行标题，不凭空画脸', () => {
    const { container } = render(ChatPanelHeader, {
      props: { ...base, titleOverride: null },
      global: { plugins },
    })

    expect(container.querySelector('.pr-header__peer')).toBeNull()
    expect(container.querySelector('.identity-view')).toBeNull()
    expect(container.textContent).toContain('私聊 · me · alice')
  })
})
