/** 题目页的芝士面板：说出去的话交给外面去问；芝士还在答的时候不能再问，也不能换
 * 对话或开新对话；一段对话都没有时，起步问题点一下就是一个问题；列表里点哪段就换到哪段。 */
import type { PanelConversation, PanelMessage } from './AssistantPanel.vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import AssistantPanel from './AssistantPanel.vue'

import i18n, { setLocale } from '@/i18n'

const conversations: PanelConversation[] = [
  { id: 'a', title: '学习率一般设多少？', lastActiveAt: '2026-10-01T08:00:00Z', questions: 2 },
  { id: 'b', title: '数据增强要不要做？', lastActiveAt: '2026-09-25T08:00:00Z', questions: 1 },
]

function mount(props: Partial<InstanceType<typeof AssistantPanel>['$props']> = {}) {
  return render(AssistantPanel, {
    props: {
      conversations,
      currentId: 'a',
      title: '学习率一般设多少？',
      messages: [] as PanelMessage[],
      streaming: null,
      tool: null,
      notice: null,
      busy: false,
      ...props,
    },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

beforeEach(() => setLocale('zh-CN'))

describe('AssistantPanel', () => {
  it('hands what was typed to the page and clears the box', async () => {
    const view = mount()
    const box = view.getByRole('textbox') as HTMLTextAreaElement
    await fireEvent.update(box, '  验证集怎么划？ ')
    await fireEvent.click(view.getByRole('button', { name: '发送' }))

    expect(view.emitted('send')).toEqual([['验证集怎么划？']])
    expect(box.value).toBe('')
  })

  it('does not take a second question, or a switch, while 芝士 is still answering', async () => {
    const view = mount({ busy: true, streaming: '常见起点是' })
    const box = view.getByRole('textbox') as HTMLTextAreaElement
    await fireEvent.update(box, '还有呢？')
    await fireEvent.keyDown(box, { key: 'Enter' })
    await fireEvent.click(view.getByRole('button', { name: '新对话' }))

    expect(view.emitted('send')).toBeUndefined()
    expect(view.emitted('new')).toBeUndefined()
  })

  it('asks a starter question in one click when the conversation is empty', async () => {
    const view = mount({ currentId: null, title: '' })
    await fireEvent.click(view.getByRole('button', { name: '做这道题要先会什么？' }))

    expect(view.emitted('send')).toEqual([['做这道题要先会什么？']])
  })

  it('switches to the conversation picked from the list', async () => {
    // Vuetify's menu needs a real viewport; the list itself is what is tested.
    const view = render(AssistantPanel, {
      props: {
        conversations,
        currentId: 'a',
        title: '学习率一般设多少？',
        messages: [],
        streaming: null,
        tool: null,
        notice: null,
        busy: false,
      },
      global: {
        plugins: [createVuetify({ components, directives }), i18n],
        stubs: { VMenu: { template: '<div><slot name="activator" :props="{}" /><slot /></div>' } },
      },
    })
    await fireEvent.click(view.getByText('数据增强要不要做？'))

    expect(view.emitted('select')).toEqual([['b']])
  })
})
