// 文档末尾的「被引用」（#3827）：只吃 props。没有就不画；对话一行一个，点了去那条消息。
import type { DocBacklink } from '../../../api/docLinks'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import i18n, { setLocale } from '../../../i18n'

import DocBacklinks from './DocBacklinks.vue'

beforeEach(() => setLocale('zh-CN'))
afterEach(() => document.body.replaceChildren())

const talk: DocBacklink = {
  kind: 'conversation',
  conversation: { kind: 'room', id: 'c1', title: '标注讨论', room_id: 'c1', project_id: 'p1' },
  message_id: 'b9',
  excerpt: '<@wangyue> 第二批清洗完了',
  author: 'liming',
  at: new Date().toISOString(),
  count: 3,
}
const spec: DocBacklink = {
  kind: 'document',
  id: 'd2',
  project_id: 'p1',
  title: '标注规范 v2',
  updated_by: 'alice',
  updated_at: new Date().toISOString(),
}

function mount(items: DocBacklink[]) {
  return render(DocBacklinks, {
    props: { items, names: { liming: '李明', wangyue: '王悦', alice: '张华' } },
    global: { plugins: [createVuetify({ components }), i18n] },
  })
}

describe('被引用', () => {
  it('没有被引用时不画', () => {
    mount([])
    expect(document.querySelector('[data-testid="doc-backlinks"]')).toBeNull()
  })

  it('文档在前，对话写出最近一条引用它的消息', () => {
    mount([talk, spec])
    const rows = [...document.querySelectorAll('li')].map((li) => li.textContent ?? '')
    expect(rows[0]).toContain('标注规范 v2')
    expect(rows[1]).toContain('#标注讨论')
    expect(rows[1]).toContain('王悦 第二批清洗完了')
  })

  it('点一条对话，打开的是那个对话里的那条消息', async () => {
    const view = mount([talk])
    await fireEvent.click(screen.getByText('#标注讨论'))
    expect(view.emitted('open')).toEqual([['c1', 'b9']])
  })
})
