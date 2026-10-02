// @vitest-environment jsdom
// 找 AI 队友的输入框：方向键选，回车做的是选中的那一项；打字时只留名字对得上的；一项都对不上时
// 回车把这句话交出去；Esc 什么都不做；不能改的选区不给改它的说法。
import type { PresetContext } from '../../../lib/docAgent'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import DocAgentBox from './DocAgentBox.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

const editable: PresetContext = { editable: true, list: false, chinese: true }

function mount(context: PresetContext = editable, scope: 'selection' | 'document' = 'selection') {
  const view = render(DocAgentBox, {
    props: { agentName: '芝士', scope, context },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  const input = screen.getByRole('combobox') as HTMLInputElement
  return { ...view, input }
}

const options = () => screen.queryAllByRole('option').map((o) => o.textContent?.trim())

describe('找 AI 队友的输入框', () => {
  it('方向键选，回车做的是选中的那一项', async () => {
    const { input, emitted } = mount()

    await fireEvent.keyDown(input, { key: 'ArrowDown' })
    await fireEvent.keyDown(input, { key: 'Enter' })

    const [[preset]] = emitted('run') as [[{ id: string }]]
    expect(preset.id).toBe('shorten')
  })

  it('打字时只留名字对得上的那一项，回车做它', async () => {
    const { input, emitted } = mount()

    await fireEvent.update(input, '精')
    expect(options()).toEqual(['精简'])
    await fireEvent.keyDown(input, { key: 'Enter' })

    const [[preset]] = emitted('run') as [[{ id: string }]]
    expect(preset.id).toBe('shorten')
  })

  it('一项都对不上时，回车把这句话交出去', async () => {
    const { input, emitted } = mount()

    await fireEvent.update(input, '把 30 人写进去')
    expect(options()).toEqual([])
    await fireEvent.keyDown(input, { key: 'Enter' })

    expect(emitted('say')).toEqual([['把 30 人写进去']])
    expect(emitted('run')).toBeUndefined()
  })

  it('Esc 收起，什么都不做', async () => {
    const { input, emitted } = mount()

    await fireEvent.keyDown(input, { key: 'Escape' })

    expect(emitted('cancel')).toHaveLength(1)
    expect(emitted('run')).toBeUndefined()
    expect(emitted('say')).toBeUndefined()
  })

  it('不能改的选区只给提问的说法', () => {
    mount({ editable: false, list: false, chinese: true })

    expect(options()).not.toContain('润色')
    expect(options()).not.toContain('精简')
    expect(options()).toContain('解释')
  })
})
