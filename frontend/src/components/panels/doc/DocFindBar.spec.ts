// 查找条的接线：输入往上发、Enter / Shift+Enter 上下跳、Esc 关闭、计数与空结果。
// 匹配与下标的计算在 lib/docFind.ts 里单测；这里只锁这一层的键盘与展示。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import DocFindBar from './DocFindBar.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('en'))
afterEach(cleanup)

function mount(props: Record<string, unknown>) {
  return render(DocFindBar, {
    props: { open: true, query: '', total: 0, current: 0, ...props },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

describe('DocFindBar', () => {
  it('关着的时候什么都不画', () => {
    const view = mount({ open: false })
    expect(view.container.querySelector('.doc-find')).toBeNull()
  })

  it('输入往上发 update:query', async () => {
    const view = mount({})
    const input = view.getByRole('textbox') as HTMLInputElement
    await fireEvent.update(input, '部署')
    expect(view.emitted('update:query')?.at(-1)).toEqual(['部署'])
  })

  it('Enter 是下一个、Shift+Enter 是上一个', async () => {
    const view = mount({ total: 3, current: 1 })
    const input = view.getByRole('textbox')
    await fireEvent.keyDown(input, { key: 'Enter' })
    await fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
    expect(view.emitted('next')).toHaveLength(1)
    expect(view.emitted('prev')).toHaveLength(1)
  })

  it('Esc 关闭', async () => {
    const view = mount({})
    await fireEvent.keyDown(view.getByRole('textbox'), { key: 'Escape' })
    expect(view.emitted('close')).toHaveLength(1)
  })

  it('计数写「第几个 / 共几个」', () => {
    const view = mount({ total: 5, current: 2 })
    expect(view.container.querySelector('.doc-find__count')?.textContent?.trim()).toBe('2/5')
  })

  it('有查询但没结果时说一声', () => {
    const view = mount({ query: '橘子', total: 0 })
    expect(view.container.querySelector('.doc-find__count')?.textContent?.trim()).toBe('No results')
  })
})
