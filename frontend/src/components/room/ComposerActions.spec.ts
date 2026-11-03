// 「键盘快捷键」这条入口在触屏上收起来：那儿没有键盘，点开是一整页用不上的按键表。
// 桌面上它照旧在「+」里。
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
  it('相册允许照片和视频，并将所选视频交给附件上传流程', async () => {
    pointer('touch')
    const view = mount(390)
    const input = view.container.querySelector('input[accept]') as HTMLInputElement
    expect(input.accept.split(',')).toEqual(['image/*', 'video/*'])
    const video = new File(['video'], 'screen.mp4', { type: 'video/mp4' })
    await fireEvent.change(input, { target: { files: [video] } })
    expect(view.emitted('files')).toEqual([[[video]]])
    await fireEvent.change(input, { target: { files: [video] } })
    expect(view.emitted('files')).toHaveLength(2)
  })

  it('触屏：「+」里没有「键盘快捷键」那一行', async () => {
    pointer('touch')
    mount(390)
    await fireEvent.click(screen.getByRole('button', { name: t('work.room.composer.more') }))
    // 面板里该有的那几行在，快捷键那行不在（等它出来之后再断言，免得断言了个空面板）。
    expect(await screen.findByText(t('work.room.checklist.compose'))).toBeTruthy()
    expect(screen.queryByText(t('global.shortcuts.open'))).toBeNull()
  })

  it('桌面：「+」里那一行还在', async () => {
    pointer('mouse')
    mount(1440)
    await fireEvent.click(screen.getByRole('button', { name: t('work.room.composer.more') }))
    expect(await screen.findByText(t('global.shortcuts.open'))).toBeTruthy()
  })

  it('攒着待发批注时，发送键上写着会带走几条', async () => {
    pointer('mouse')
    mount(1440, { pendingCount: 2 })
    // 只写进 title 的话，读者在按下之前看不见这一下会带走什么。
    expect(screen.getByText(t('work.room.preview.sendWithNotes', { count: 2 }))).toBeTruthy()
  })

  it('带着附件时发送键不写「含 N 条批注」：那一下发的是附件，不含这一批', async () => {
    // 写了就是承诺它做不到的事——读者按下之后会发现批注还在那儿。批注由托盘上的
    // 「全部发送」发（`RoomComposer` 的 `sendableCount` 在带附件时给 0）。
    pointer('mouse')
    mount(1440, { pendingCount: 0 })
    expect(screen.queryByText(t('work.room.preview.sendWithNotes', { count: 2 }))).toBeNull()
  })

  it('没攒批注时发送键不带字', async () => {
    pointer('mouse')
    mount(1440)
    expect(screen.queryByText(t('work.room.preview.sendWithNotes', { count: 0 }))).toBeNull()
    expect(screen.queryByText(t('work.room.preview.sendWithNotes', { count: 2 }))).toBeNull()
  })
})
