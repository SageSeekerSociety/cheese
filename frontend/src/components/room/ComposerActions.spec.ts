// 「键盘快捷键」这条入口在触屏上收起来：那儿没有键盘，点开是一整页用不上的按键表。
// 桌面上它照旧——显式那颗图标、⋯ 里那一行，都留着。
//
// 判据按输入方式（`hover: none`），不按视口宽度：插上鼠标/触控板的平板不算触屏，
// 和 useChatRowActions、RoomComposer 是同一句话。
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ComposerActions from './ComposerActions.vue'

import i18n, { setLocale, t } from '@/i18n'

const BASE = {
  uploading: false,
  canSend: true,
  showImagePicker: true,
  enterSends: false,
  alwaysSummon: false,
  summonOn: false,
  summonReady: true,
  agentName: '芝士',
  canChecklist: true,
  canRemind: true,
}

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

/** 换一种输入方式：`hover: none` 就是触屏（唯一的那个匹配看它）。 */
function pointer(kind: 'touch' | 'mouse') {
  vi.stubGlobal('matchMedia', (query: string) => ({
    media: query,
    matches: query.includes('hover: none') ? kind === 'touch' : false,
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  }))
}

function mount(width: number, overrides: Record<string, unknown> = {}) {
  ;(window as unknown as { innerWidth: number }).innerWidth = width
  return render(ComposerActions as unknown as Component, {
    props: { ...BASE, ...overrides },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

const shortcutButton = (container: Element) => container.querySelector(`[aria-label="${t('global.shortcuts.open')}"]`)
const checklistButton = (container: Element) =>
  container.querySelector(`[aria-label="${t('work.room.checklist.compose')}"]`)

beforeEach(() => {
  // These assertions read the Chinese copy.
  setLocale('zh-CN')
  vi.stubGlobal('visualViewport', {
    width: 390,
    height: 844,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.stubGlobal('devicePixelRatio', 1)
})

afterEach(() => {
  cleanup()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

describe('输入框下面那一行', () => {
  it('触屏：不摆那颗键盘图标', () => {
    pointer('touch')
    const { container } = mount(390)
    expect(shortcutButton(container)).toBeNull()
    // 别的动作还在。
    expect(checklistButton(container)).toBeTruthy()
  })

  it('触屏：⋯ 里也没有「键盘快捷键」那一行', async () => {
    pointer('touch')
    mount(390, { collapseExtras: true })
    await fireEvent.click(screen.getByRole('button', { name: t('work.room.composer.more') }))
    // 面板里该有的两行在，快捷键那行不在（等它出来之后再断言，免得断言了个空面板）。
    expect(await screen.findByText(t('work.room.checklist.compose'))).toBeTruthy()
    expect(screen.queryByText(t('global.shortcuts.open'))).toBeNull()
  })

  it('桌面：显式那颗键盘图标还在', () => {
    pointer('mouse')
    const { container } = mount(1440)
    expect(shortcutButton(container)).toBeTruthy()
  })

  it('桌面：⋯ 里那一行也还在', async () => {
    pointer('mouse')
    mount(1440, { collapseExtras: true })
    await fireEvent.click(screen.getByRole('button', { name: t('work.room.composer.more') }))
    expect(await screen.findByText(t('global.shortcuts.open'))).toBeTruthy()
  })
})
