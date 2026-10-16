// 输入框打字那一块：正文是要发出去的那段字，引用画成标签、当一个字删。
import { defineComponent, h, nextTick, ref } from 'vue'
import { render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import ComposerEditor from './ComposerEditor.vue'

import { composerText, placeCaret, pressInComposer, typeInComposer } from '@/test/composerInput'

const names = { mentionNames: { zhangsan: '张三' }, topicTitles: {}, notified: true }

function mount(initial: string) {
  const text = ref(initial)
  const caret = ref(initial.length)
  const utils = render(
    defineComponent({
      setup: () => () =>
        h(ComposerEditor, {
          names,
          modelValue: text.value,
          'onUpdate:modelValue': (v: string) => (text.value = v),
          caret: caret.value,
          'onUpdate:caret': (v: number) => (caret.value = v),
        }),
    })
  )
  return { ...utils, text, caret }
}

describe('ComposerEditor', () => {
  it('shows a reference as the person it names, and keeps the reference in what is sent', () => {
    const { container, text } = mount('请 <@zhangsan> 看一下')
    expect(container.querySelector('.mention')?.textContent).toBe('@张三')
    expect(text.value).toBe('请 <@zhangsan> 看一下')
  })

  it('takes a reference away whole with one Backspace after it', () => {
    const { container, text } = mount('请 <@zhangsan> 看一下')
    placeCaret(container, '请 <@zhangsan>'.length)
    pressInComposer(container, 'Backspace')
    expect(text.value).toBe('请  看一下')
  })

  it('treats a reference as one word when deleting or moving by word', () => {
    const { container, text, caret } = mount('请 <@zhangsan> 看一下')
    placeCaret(container, '请 '.length)
    pressInComposer(container, 'ArrowRight', { ctrlKey: true })
    expect(caret.value).toBe('请 <@zhangsan>'.length)
    pressInComposer(container, 'Backspace', { ctrlKey: true })
    expect(text.value).toBe('请  看一下')
  })

  it('steps over a reference in one press of an arrow key', () => {
    const { container, caret } = mount('请 <@zhangsan> 看一下')
    placeCaret(container, '请 <@zhangsan>'.length)
    pressInComposer(container, 'ArrowLeft')
    expect(caret.value).toBe('请 '.length)
  })

  it('hands what is typed to the page, line breaks included', async () => {
    const { container, text } = mount('')
    await typeInComposer(container, '第一行\n第二行')
    expect(text.value).toBe('第一行\n第二行')
  })

  it('shows what the page puts in, and leaves the caret where the page says', async () => {
    const { container, text, caret } = mount('你好')
    text.value = '你好，<@zhangsan> 在吗'
    caret.value = 3
    await nextTick()
    expect(composerText(container)).toBe('你好，<@zhangsan> 在吗')
    await typeInComposer(container, '喂')
    expect(text.value).toBe('你好，喂<@zhangsan> 在吗')
  })

  it('puts the caret at the end of a draft the page fills in without placing it', async () => {
    const { container, text } = mount('')
    text.value = '<@zhangsan> 帮我看看'
    await nextTick()
    await typeInComposer(container, '吧')
    expect(text.value).toBe('<@zhangsan> 帮我看看吧')
  })

  it('indents with Tab inside a fenced code block and leaves Tab alone outside it', () => {
    const { container, text } = mount('看这段\n```\nx = 1\n```')
    placeCaret(container, '看这段\n```\n'.length)
    expect(pressInComposer(container, 'Tab')).toBe(true)
    expect(text.value).toBe('看这段\n```\n    x = 1\n```')
    placeCaret(container, 1)
    expect(pressInComposer(container, 'Tab')).toBe(false)
  })
})
