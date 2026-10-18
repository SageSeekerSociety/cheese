// 顶栏上的编辑方式：能改的人在「编辑」「建议」「只读」之间切；只能提建议的人（导师）
// 没有「编辑」；没有编辑权限的人没有编辑方式可切。
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

  it('能改的人切到只读', async () => {
    const { emitted } = mount()
    await fireEvent.click(screen.getByTestId('doc-mode'))
    await fireEvent.click(await screen.findByRole('menuitemradio', { name: new RegExp(t('work.room.doc.mode.read')) }))
    expect(emitted()['toggle-editable']).toHaveLength(1)
    expect(emitted()['set-suggesting']).toBeUndefined()
  })

  it('自己切到只读后，从同一个菜单回到建议', async () => {
    const { emitted } = mount({ editable: false })
    expect(screen.getByTestId('doc-mode').textContent).toContain(t('work.room.doc.mode.read'))
    await fireEvent.click(screen.getByTestId('doc-mode'))
    await fireEvent.click(
      await screen.findByRole('menuitemradio', { name: new RegExp(t('work.room.doc.mode.suggest')) })
    )
    expect(emitted()['toggle-editable']).toHaveLength(1)
    expect(emitted()['set-suggesting']).toEqual([[true]])
  })

  it('只能提建议的人只有建议和只读，没有编辑', async () => {
    mount({ suggestOnly: true, suggesting: true })
    await fireEvent.click(screen.getByTestId('doc-mode'))
    const names = (await screen.findAllByRole('menuitemradio')).map((el) => el.textContent ?? '')
    expect(names).toHaveLength(2)
    expect(names[0]).toContain(t('work.room.doc.mode.suggest'))
    expect(names[1]).toContain(t('work.room.doc.mode.read'))
  })

  it('不能改的人看不到编辑方式', () => {
    mount({ editable: false, readOnly: true })
    expect(screen.queryByTestId('doc-mode')).toBeNull()
  })
})
