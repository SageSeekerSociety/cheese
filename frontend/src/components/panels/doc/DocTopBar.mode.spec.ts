// 顶栏上的编辑方式：能改的人在「编辑」和「建议」之间切；只能提建议的人（导师）固定在
// 「建议」，没有切换，「⋯」里也没有删除、移动这些归作者的事。
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import DocTopBar from './DocTopBar.vue'

import i18n, { setLocale, t } from '@/i18n'

const BASE: Record<string, unknown> = {
  loading: false,
  connection: 'connected',
  peers: [],
  editable: true,
  readOnly: false,
  lastEdit: null,
  suggestionCount: 0,
  suggestionsOpen: false,
  commentCount: 0,
  commentsOpen: false,
  agentName: '芝士',
  mentionNames: {},
  headings: [],
  findOpen: false,
}

function mount(overrides: Record<string, unknown> = {}) {
  return render(DocTopBar as unknown as Component, {
    props: { ...BASE, ...overrides },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  vi.stubGlobal('visualViewport', {
    width: 1280,
    height: 900,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.stubGlobal('devicePixelRatio', 1)
})
afterAll(() => vi.unstubAllGlobals())
beforeEach(() => setLocale('zh-CN'))

describe('编辑方式', () => {
  it('能改的人切到建议', async () => {
    const { emitted } = mount()
    await fireEvent.click(screen.getByTestId('doc-mode'))
    await fireEvent.click(
      await screen.findByRole('menuitemradio', { name: new RegExp(t('work.room.doc.mode.suggest')) })
    )
    expect(emitted()['set-suggesting']).toEqual([[true]])
  })

  it('只能提建议的人固定在建议，没有切换', () => {
    mount({ suggestOnly: true, suggesting: true })
    expect(screen.queryByTestId('doc-mode')).toBeNull()
    expect(screen.getByTitle(t('work.room.doc.mode.suggestOnly')).textContent).toContain(
      t('work.room.doc.mode.suggest')
    )
  })

  it('不能改的人看不到编辑方式', () => {
    mount({ editable: false, readOnly: true })
    expect(screen.queryByTestId('doc-mode')).toBeNull()
    expect(screen.queryByTitle(t('work.room.doc.mode.suggestOnly'))).toBeNull()
  })
})
