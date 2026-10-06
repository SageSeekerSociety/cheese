// 顶栏左边那一句「谁 · 多久之前编辑的」在窄屏上要被截断，不是把右边的动作挤出去。
//
// 截断靠 `text-overflow: ellipsis`，而它只对**块级/行内盒**里的行内内容生效：这颗
// 按钮是 `display: inline-flex`，直接写在按钮上的省略号形同虚设（这句话在 390 宽的
// 手机上要 118px，按钮只拿到 88px，字却不肯收）。所以这句字得有自己的一个元素，
// 省略号挂在它身上。这里钉住的就是那个元素——happy-dom 不排版，量不出真的省略号，
// 能钉的是「它挂在哪一层」。
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

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

beforeEach(() => {
  // These assertions read the Chinese copy.
  setLocale('zh-CN')
})

describe('文档顶栏左边那一句状态', () => {
  it('最近编辑那一句：字装在自己的元素里，省略号才收得动', () => {
    const { container } = mount({ lastEdit: { name: '王长鑫', at: new Date().toISOString() } })
    const text = container.querySelector('.doc-top-bar__quiet-text')
    expect(text?.textContent).toContain('王长鑫')
    // 它必须是那颗安静按钮的孩子，而不是被平铺回按钮里。
    expect(text?.parentElement?.classList.contains('doc-top-bar__btn--quiet')).toBe(true)
  })

  it('没有最近编辑就不画这一句', () => {
    const { container } = mount()
    expect(container.querySelector('.doc-top-bar__btn--quiet')).toBeNull()
  })

  it('断网那一句：字同样装在自己的元素里', () => {
    const { container } = mount({ connection: 'offline', peers: [] })
    const note = container.querySelector('.doc-top-bar__note--warn')
    expect(note?.textContent).toContain(t('work.room.doc.offlineSync'))
    expect(note?.querySelector('.doc-top-bar__note-text')).toBeTruthy()
  })
})
