/** 桌面版里清单上的字能选、能复制（右键菜单和选区用的是同一张名单，菜单在就是选得中）：队友在对话里的清单，房间和卡片上的「进度」。 */
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import TodoChecklist from '@/components/panels/TodoChecklist.vue'
import ChecklistMessage from '@/components/room/ChecklistMessage.vue'
import { setLocale } from '@/i18n'

type AppWindow = { __TAURI__?: unknown }

const ITEMS = [
  { id: '1', subject: '梳理数据模型', status: 'completed' as const },
  { id: '2', subject: '合并列表', status: 'pending' as const },
]

beforeEach(async () => {
  vi.resetModules()
  vi.stubEnv('DEV', false)
  setLocale('zh-CN')
  ;(window as AppWindow).__TAURI__ = { core: { invoke: async () => undefined } }
  const { behaveAsDesktopApp } = await import('./desktopNative')
  behaveAsDesktopApp()
})

afterEach(() => {
  cleanup()
  vi.unstubAllEnvs()
  delete (window as AppWindow).__TAURI__
  delete document.documentElement.dataset.desktopApp
  document.head.querySelectorAll('style').forEach((s) => s.remove())
})

const vuetify = () => createVuetify({ components, directives })

function rightClickBlocked(text: string): boolean {
  const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true })
  screen.getByText(text).dispatchEvent(event)
  return event.defaultPrevented
}

describe('in the desktop app a checklist is text to copy', () => {
  it("a teammate's checklist in the chat", () => {
    render(ChecklistMessage as unknown as Component, {
      props: { checklist: { items: ITEMS }, updatedAt: new Date().toISOString(), edited: false },
      global: { plugins: [vuetify()] },
    })
    expect(rightClickBlocked('合并列表')).toBe(false)
  })

  it("a room's or a card's progress", () => {
    render(TodoChecklist as unknown as Component, {
      props: { items: ITEMS },
      global: { plugins: [vuetify()] },
    })
    expect(rightClickBlocked('梳理数据模型')).toBe(false)
  })
})
