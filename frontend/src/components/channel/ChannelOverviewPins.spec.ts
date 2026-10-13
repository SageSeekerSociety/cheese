/** 频道概览的置顶：谁都能看、能跳回原消息；只有在主线说得上话的人能取消置顶。 */
import type { Component } from 'vue'
import type { ChannelPin } from '@/types/channels'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

// 菜单的弹层在测试环境里画不出来；这里只关心菜单里给了哪几项、点了做什么。
vi.mock('@/components/common/AdaptiveMenu.vue', () => ({
  default: {
    name: 'AdaptiveMenu',
    props: ['actions'],
    template:
      '<div><button v-for="a in actions" :key="a.key" type="button" @click="a.onSelect()">{{ a.label }}</button></div>',
  },
}))

import ChannelOverviewPins from './ChannelOverviewPins.vue'

import i18n, { setLocale } from '@/i18n'

setLocale('zh-CN')
const vuetify = createVuetify({ components, directives })

const PIN = {
  pinned_by: 'alice',
  pinned_at: '2026-10-06T00:00:00Z',
  block: {
    id: 'b1',
    conversation_id: 'r1',
    kind: 'message',
    author: 'alice',
    author_type: 'participant',
    content: '周四 18:00 之后只合修复',
    created_at: '2026-10-06T00:00:00Z',
  },
} as unknown as ChannelPin

/** 名册给的身份：头像 id + 是不是队友。没给时视图退回 handle + 彩色首字母。 */
const FACES = { alice: { name: '爱丽丝', avatarId: 7, agent: false } }

function mount(canPin: boolean, faces?: typeof FACES, memberNames: Record<string, string> = {}) {
  return render(ChannelOverviewPins as unknown as Component, {
    props: { pins: [PIN], canPin, memberNames, faces, refs: { mentionNames: {}, topicTitles: {} } },
    global: { plugins: [vuetify, i18n] },
  })
}

describe('置顶的操作', () => {
  it('能在主线说话的人能取消置顶', async () => {
    const ui = mount(true)
    await fireEvent.click(ui.getByRole('button', { name: '取消置顶' }))
    expect(ui.emitted().unpin).toEqual([['b1']])
  })

  it('没加入频道的人只能跳回原消息', async () => {
    const ui = mount(false)
    expect(ui.getByRole('button', { name: '跳到原消息' })).toBeTruthy()
    expect(ui.queryByRole('button', { name: '取消置顶' })).toBeNull()
  })
})

describe('置顶那一行里的人', () => {
  it('名册给了身份时，置顶的人画成头像 + 名字', () => {
    // PIN 说的是 alice：名册这一行给的是「爱丽丝」和她自己挑的头像 7。
    const ui = mount(false, FACES)
    const said = ui.container.querySelector('.pin__said')!.previousElementSibling!
    expect(said.textContent).toContain('爱丽丝')
    expect(said.querySelector('.identity-view')).not.toBeNull()
    expect(said.querySelector('img')?.getAttribute('src')).toContain('/avatars/7')
  })

  it('名册没给身份时退回名字，仍是一颗可认的人 chip', () => {
    const ui = mount(false, undefined, { alice: '爱丽丝' })
    const said = ui.container.querySelector('.pin__said')!.previousElementSibling!
    expect(said.textContent).toContain('爱丽丝')
    // 没有头像素材就画按名字派生的彩色首字母，不去取全站默认那张脸。
    expect(said.querySelector('img')).toBeNull()
    expect(said.querySelector('.user-avatar-char')?.textContent?.trim()).toBe('爱')
  })
})
