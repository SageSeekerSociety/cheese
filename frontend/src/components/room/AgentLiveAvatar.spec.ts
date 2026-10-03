/**
 * 队友在干活时，悬停它在动的头像看到「思考中 · 已用 12 秒」，秒数每秒走一格。
 * 这一句要在原地更新：系统原生的悬停气泡（title）一换字就收起再弹出，人看到的是
 * 它每秒闪一下。
 */
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import i18n, { setLocale } from '../../i18n'
import AgentNoticeFrame from '../AgentNoticeFrame.vue'

import RoomMessage from './RoomMessage.vue'

const plugins = () => [createVuetify({ components, directives }), i18n]

beforeAll(() => {
  // Vuetify 的 overlay（v-tooltip）会摸这个浏览器 API，happy-dom 没有。
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

beforeEach(() => setLocale('zh-CN'))

const message = {
  id: 'm1',
  author: 'cheese-a1',
  content: '我先看一下代码。',
  kind: 'message',
  created_at: '',
  meta: {},
}

function messageProps(faceLabel: string) {
  return {
    block: message,
    parent: null,
    parentName: null,
    runStart: true,
    mine: false,
    topicId: 't1',
    authorName: '芝士',
    avatar: null,
    isAgent: true,
    time: '10:00',
    refs: { mentionNames: {}, topicTitles: {} },
    viewer: 'alice',
    askBusy: false,
    face: 'thinking',
    faceLabel,
    faceStatus: '思考中',
  }
}

function noticeProps(faceLabel: string) {
  return { name: '芝士', handle: 'cheese-a1', time: '10:00', face: 'thinking', faceLabel, faceStatus: '思考中' }
}

const cases = [
  ['消息里的头像', RoomMessage, messageProps],
  ['事件行里的头像', AgentNoticeFrame, noticeProps],
] as const

describe.each(cases)('%s：悬停看到的那一句随秒数走，不闪', (_name, component, props) => {
  it('悬停时看得到在做什么、用了多久，秒数走了原地换字，原生气泡不跟着换', async () => {
    const out = render(component as unknown as Component, {
      props: props('思考中 · 已用 12 秒'),
      global: { plugins: plugins() },
    })
    const avatar = out.container.querySelector('button[data-site]') as HTMLButtonElement
    const nativeBefore = avatar.getAttribute('title')
    const nameBefore = avatar.getAttribute('aria-label')

    await fireEvent.mouseEnter(avatar)
    await waitFor(() => expect(screen.getByText('芝士：思考中 · 已用 12 秒，点击查看现场')).toBeTruthy())

    await out.rerender(props('思考中 · 已用 13 秒'))
    await waitFor(() => expect(screen.getByText('芝士：思考中 · 已用 13 秒，点击查看现场')).toBeTruthy())
    expect(screen.queryByText('芝士：思考中 · 已用 12 秒，点击查看现场')).toBeNull()
    // 原生气泡的字一换，浏览器就把它收起再弹：每秒一闪。它不能跟着秒数变。
    expect(avatar.getAttribute('title')).toBe(nativeBefore)
    // 读屏读到的名字也不跟着秒数变：一变就可能每秒再念一遍。
    expect(avatar.getAttribute('aria-label')).toBe(nameBefore)
    out.unmount()
  })
})
