/** 「改动」页顶部那块交付情况（审阅、退回、采纳）不跟着差异的取数走。
 *
 * 差异取不到（还在取、取失败）时，人要做的决定还在那里：交上来的那一版在已提交版本
 * 里完好，审阅和采纳不该因为这一格的现场读不到就跟着不见。
 */
import type { Component } from 'vue'

import { h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, screen } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import { changesBundle } from '../../views/demo/catalogPanelsFixtures'

import PanelChanges from './PanelChanges.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

let vuetify: ReturnType<typeof createVuetify>
beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

function mount(over: Parameters<typeof changesBundle>[0]) {
  return render(PanelChanges as unknown as Component, {
    props: { topicId: 'room-1', changes: changesBundle({ openPath: null, ...over }) },
    slots: { head: () => h('button', { type: 'button' }, '采纳') },
    global: { plugins: [vuetify] },
  })
}

describe('取不到差异时，交付情况还在', () => {
  it('取失败：报错和「采纳」都在', () => {
    mount({ errorMsg: '任务的环境空闲后已释放；可以查看已提交版本', loading: false })
    expect(screen.getByText(/任务的环境空闲后已释放/)).toBeTruthy()
    expect(screen.getByText('采纳')).toBeTruthy()
  })

  it('还在取：「采纳」不用等它', () => {
    mount({ loading: true, errorMsg: null })
    expect(screen.getByText('采纳')).toBeTruthy()
  })
})
