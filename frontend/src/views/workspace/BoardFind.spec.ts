/** 看板的「本视图内按标题找」：`/` 聚焦（输入框里不抢），Esc 清空。 */
import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import BoardFind from './BoardFind.vue'

function mount() {
  const text = ref('')
  const Host = defineComponent(
    () => () =>
      h('div', [
        h('textarea'),
        h(BoardFind, { modelValue: text.value, 'onUpdate:modelValue': (v: string) => (text.value = v) }),
      ])
  )
  const view = render(Host, { global: { plugins: [createVuetify({ components })] } })
  const input = view.container.querySelector('input') as HTMLInputElement
  return { text, input, view }
}

describe('看板标题过滤框', () => {
  it('按 / 聚焦到框里；焦点在别的输入框里时不抢', async () => {
    const { input, view } = mount()
    await fireEvent.keyDown(window, { key: '/' })
    expect(document.activeElement).toBe(input)
    const other = view.container.querySelector('textarea')!
    other.focus()
    await fireEvent.keyDown(other, { key: '/' })
    expect(document.activeElement).toBe(other)
  })

  it('Esc 清空', async () => {
    const { input, text } = mount()
    await fireEvent.update(input, '登录')
    expect(text.value).toBe('登录')
    await fireEvent.keyDown(input, { key: 'Escape' })
    expect(text.value).toBe('')
  })
})
