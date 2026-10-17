/** 人发的消息和芝士的一样按 Markdown 画：输入框里写成代码的，发出去也是代码；复制出来
 *  的还是那个人打的原文，点到的人写成名字。 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { copyMessage } from './messageActions'
import RoomMessage from './RoomMessage.vue'

import i18n, { setLocale } from '@/i18n'
import { loadMarkdownReader } from '@/test/markdownReader'

const CONTENT = '<@bob> 看一下这段\n第二行\n```\ndef f(x):\n    return x * 2\n```'

function mount() {
  return render(RoomMessage, {
    props: {
      block: { id: 'b1', author: 'alice', content: CONTENT, kind: 'message', created_at: '' },
      parent: null,
      parentName: null,
      runStart: true,
      mine: false,
      topicId: 't1',
      avatar: null,
      isAgent: false,
      time: '10:00',
      refs: { mentionNames: { bob: 'Bob' }, topicTitles: {} },
      viewer: 'alice',
      pickerOpen: false,
      askBusy: false,
    } as never,
    attrs: { 'data-mid': 'b1' },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

beforeEach(() => setLocale('zh-CN'))

beforeAll(loadMarkdownReader)

describe('人发的消息', () => {
  it('代码块画成代码，点到的人是名字，单个回车还是换行', async () => {
    const { container } = mount()
    await waitFor(() => expect(container.querySelector('.im-text pre')).toBeTruthy())
    expect(container.querySelector('.im-text pre')!.textContent).toContain('return x * 2')
    expect(container.querySelector('.im-text pre')!.textContent).not.toContain('```')
    expect(container.querySelector('.im-text [data-handle="bob"]')?.textContent).toBe('@Bob')
    expect(container.querySelector('.im-text br')).toBeTruthy()
  })

  it('复制出来的是原文，换行和代码块都在，点到的人写成名字', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const { container } = mount()
    await waitFor(() => expect(container.querySelector('.im-text pre')).toBeTruthy())
    await copyMessage({ id: 'b1', content: CONTENT } as never, false)
    expect(writeText).toHaveBeenCalledWith(CONTENT.replace('<@bob>', '@Bob'))
  })
})
