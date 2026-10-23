/** 个人芝士的面板：头上写着是谁在回答、属于谁；说出去的话交给外面去问；芝士还在答的
 * 时候不能再问，也不能开新话题，但能停下；停下的回答留着写出的部分；话题空着时，起步问题
 * 点一下就是一个问题；之前的话题摆在上面，用「新话题」隔开，「更早的话题」再往上读。 */
import type { PanelMessage } from './AssistantPanel.vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import AssistantPanel from './AssistantPanel.vue'

import i18n, { setLocale } from '@/i18n'
import { composerText, typeInComposer } from '@/test/composerInput'

const SAID: PanelMessage[] = [
  { role: 'user', text: '学习率一般设多少？', at: '2026-10-01T08:00:00Z' },
  { role: 'assistant', text: '常见起点是 0.1', at: '2026-10-01T08:00:05Z' },
]

function mount(props: Partial<InstanceType<typeof AssistantPanel>['$props']> = {}) {
  return render(AssistantPanel, {
    props: {
      messages: SAID,
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
    await typeInComposer(view.container, '  验证集怎么划？ ')
    await fireEvent.click(view.getByRole('button', { name: '发送' }))

    expect(view.emitted('send')).toEqual([['验证集怎么划？']])
    await waitFor(() => expect(composerText(view.container)).toBe(''))
  })

  it('sends files handed over alone, with nothing typed', async () => {
    const view = mount({ files: [{ name: '课题.pdf', uploading: false }], canAttach: true })
    await fireEvent.click(view.getByRole('button', { name: '发送' }))

    expect(view.emitted('send')).toEqual([['']])
  })

  it('says who is answering: there is one 芝士, so nothing says whose', () => {
    const view = mount()
    expect(view.getByText('芝士')).toBeTruthy()
    expect(view.queryByText('个人')).toBeNull()
  })

  it('does not take a second question, or a new topic, while 芝士 is still answering', async () => {
    const view = mount({ busy: true, streaming: '常见起点是' })
    const box = view.getByRole('textbox') as HTMLTextAreaElement
    await fireEvent.update(box, '还有呢？')
    await fireEvent.keyDown(box, { key: 'Enter' })
    await fireEvent.click(view.getByRole('button', { name: '新话题' }))

    expect(view.emitted('send')).toBeUndefined()
    expect(view.emitted('new')).toBeUndefined()
  })

  it('stops the answer being written, and asks nothing new', async () => {
    const view = mount({ busy: true, streaming: '常见起点是' })
    await fireEvent.click(view.getByRole('button', { name: '停止' }))

    expect(view.emitted('stop')).toHaveLength(1)
    expect(view.emitted('send')).toBeUndefined()
  })

  it('keeps a stopped answer and says it was stopped', () => {
    const view = mount({
      messages: [
        { role: 'user', text: '学习率设多少？', at: '2026-10-04T08:00:00Z' },
        { role: 'assistant', text: '常见起点是 0.1', stopped: true, at: '2026-10-04T08:00:05Z' },
      ],
    })

    expect(view.getByText('常见起点是 0.1')).toBeTruthy()
    expect(view.getByText('已停止')).toBeTruthy()
  })

  it('asks a starter question in one click when the conversation is empty', async () => {
    const view = mount({ messages: [] })
    await fireEvent.click(view.getByRole('button', { name: '做这道题要先会什么？' }))

    expect(view.emitted('send')).toEqual([['做这道题要先会什么？']])
  })

  it('shows earlier topics above, set apart by 新话题, and reads further back on asking', async () => {
    const view = mount({
      messages: [],
      earlier: [{ id: 'a', title: '学习率一般设多少？', at: '2026-10-01T08:00:00Z', messages: SAID }],
      hasOlder: true,
    })

    expect(view.getByText('常见起点是 0.1')).toBeTruthy()
    expect(view.getAllByText('新话题').length).toBeGreaterThan(0)
    await fireEvent.click(view.getByRole('button', { name: '更早的话题' }))
    expect(view.emitted('older')).toHaveLength(1)
  })

  it('pins itself to the right, or floats, when the page can dock it', async () => {
    const view = mount({ dockable: true, docked: false })
    await fireEvent.click(view.getByRole('button', { name: '固定到右侧' }))
    expect(view.emitted('dock')).toEqual([[true]])
  })

  it('a run the platform started reads as what started it, not as what the platform told 芝士', () => {
    const view = mount({
      messages: [
        {
          role: 'platform',
          text: '<平台>\n例行事务「每日待办」现在运行',
          at: '2026-10-01T00:00:00Z',
          shown: { routine: '每日待办' },
        },
        { role: 'assistant', text: '今天没有待办。', at: '2026-10-01T00:00:05Z' },
      ],
    })
    expect(view.getByText('例行事务「每日待办」开始运行')).toBeTruthy()
    expect(view.queryByText(/<平台>/)).toBeNull()
  })
})
