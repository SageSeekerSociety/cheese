/** 频道概览「支线」那一格：说每一行的是谁 —— 名册给了身份就画头像 + 名字（不是一行纯名字），
 *  没给时退回 handle + 彩色首字母。 */
import type { Component } from 'vue'
import type { ThreadRow } from '../../types/threads'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import PanelThreads from './PanelThreads.vue'

import i18n, { setLocale } from '@/i18n'

setLocale('zh-CN')
const vuetify = createVuetify({ components, directives })

const ROW = {
  id: 'th1',
  room_id: 'r1',
  root_block_id: 'b1',
  reply_count: 3,
  last_reply_at: '2026-10-06T00:00:00Z',
  last_reply: { author: 'bob', content: '我改好了', created_at: '2026-10-06T00:00:00Z' },
  participants: ['alice', 'bob'],
  tasks: [],
  root: { author: 'alice', content: '周四之后只合修复', created_at: '2026-10-06T00:00:00Z' },
  unread: false,
} as unknown as ThreadRow

/** 名册给的身份：头像 id + 是不是队友。没给时视图退回 handle + 彩色首字母。 */
const FACES = {
  alice: { name: '爱丽丝', avatarId: 7, agent: false },
  bob: { name: '鲍勃', avatarId: null, agent: false },
}

function mount(faces?: typeof FACES, names: Record<string, string> = {}) {
  return render(PanelThreads as unknown as Component, {
    props: {
      rows: [ROW],
      loading: false,
      error: null,
      refs: { mentionNames: names, topicTitles: {} },
      nameOf: (handle: string) => names[handle] || handle,
      fmtTime: (iso: string) => iso,
      faces,
    },
    global: { plugins: [vuetify, i18n] },
  })
}

describe('支线那一行里的人', () => {
  it('谁挂的、谁最后说的，都画成头像 + 名字', () => {
    const ui = mount(FACES)
    const root = ui.container.querySelector('.thread-row__root')!
    expect(root.textContent).toContain('爱丽丝')
    expect(root.querySelector('.identity-view')).not.toBeNull()
    expect(root.querySelector('img')?.getAttribute('src')).toContain('/avatars/7')

    const last = ui.container.querySelector('.thread-row__last')!
    expect(last.textContent).toContain('鲍勃')
    expect(last.querySelector('.identity-view')).not.toBeNull()
  })

  it('说过话的每个人各是一颗 chip，中间用顿号隔开', () => {
    const ui = mount(FACES)
    const people = ui.container.querySelector('.thread-row__people')!
    expect(people.querySelectorAll('.identity-view')).toHaveLength(2)
    // 每一颗是「彩色首字母 + @名字」，两颗之间是名册那个分隔符。
    expect(people.textContent).toContain('@爱丽丝')
    expect(people.textContent).toContain('、')
    expect(people.textContent).toContain('@鲍勃')
  })

  it('名册没给身份时退回名字，仍是一颗可认的人 chip', () => {
    const ui = mount(undefined, { alice: '爱丽丝', bob: '鲍勃' })
    const root = ui.container.querySelector('.thread-row__root')!
    expect(root.textContent).toContain('爱丽丝')
    // 没有头像素材就画按名字派生的彩色首字母，不去取全站默认那张脸。
    expect(root.querySelector('img')).toBeNull()
    expect(root.querySelector('.user-avatar-char')?.textContent?.trim()).toBe('爱')
  })
})
