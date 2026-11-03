// 芝士输入框里的 @：刚打 @ 先给最近去过和四类入口；打了字按类分组、眼前项目的排前面、
// 拼音首字母也找得到；挑中在光标处写下引用；够五样不再给挑；Esc 收起、退格退出一类。
import type { Citable } from '@/types/citable'

import { nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { MOST_CITED, useCitePicker } from './useCitePicker'

const ITEMS: Citable[] = [
  { id: 'a1', kind: 'task', title: '接入搜索', where: '知是', place: '后端' },
  { id: 'a2', kind: 'task', title: '接入数据集', where: '机器学习课程设计', place: '实验' },
  { id: 'c1', kind: 'channel', title: '后端', where: '知是' },
  { id: 'd1', kind: 'doc', title: '搜索接入方案', where: '知是' },
  { id: 'file-f1', kind: 'file', title: '数据集说明.pdf', where: '机器学习课程设计' },
]

function picker(text = '', opts: { recents?: string[]; project?: string | null } = {}) {
  const draft = ref(text)
  const caret = ref(text.length)
  const picked = vi.fn()
  const opened = vi.fn()
  const p = useCitePicker({
    draft,
    caret,
    items: () => ITEMS,
    recents: () => opts.recents ?? [],
    project: () => (opts.project === undefined ? '知是' : opts.project),
    most: MOST_CITED,
    opened,
    picked,
    scrollActiveIntoView: () => {},
  })
  return { p, draft, caret, picked, opened }
}

const titles = (rows: ReturnType<typeof picker>['p']['rows']['value']) =>
  rows.map((r) =>
    r.type === 'item' ? r.item.title : r.type === 'kind' ? `[${r.kind}]` : r.type === 'group' ? `#${r.label}` : r.type
  )

describe('芝士输入框里的 @', () => {
  it('just typed, lists where the person was lately and the four kinds, and asks for the list', async () => {
    const { p, draft, caret, opened } = picker('', { recents: ['d1', 'gone', 'a1'] })
    draft.value = '帮我看看 @'
    caret.value = draft.value.length
    await nextTick()

    expect(p.open.value).toBe(true)
    expect(opened).toHaveBeenCalled()
    expect(titles(p.rows.value)).toEqual([
      '#recent',
      '搜索接入方案',
      '接入搜索',
      '#kinds',
      '[task]',
      '[channel]',
      '[doc]',
      '[file]',
    ])
  })

  it('typed, groups the matches by kind with the project in view first, and finds by pinyin initials', () => {
    expect(titles(picker('@接入').p.rows.value)).toEqual(['#task', '接入搜索', '接入数据集', '#doc', '搜索接入方案'])
    expect(titles(picker('@jrss').p.rows.value)).toEqual(['#task', '接入搜索'])
  })

  it('picked, writes the reference at the caret and keeps the words after it', () => {
    const { p, draft, caret, picked } = picker('比较 @接入')
    draft.value = '比较 @接入 和别的'
    caret.value = '比较 @接入'.length

    p.choose(p.choices.value[0])

    expect(draft.value).toBe('比较 <#a1> 和别的')
    expect(caret.value).toBe('比较 <#a1>'.length)
    expect(picked).toHaveBeenCalledWith(ITEMS[0])
    expect(p.open.value).toBe(false)
  })

  it('in a kind, lists only that kind of the project in view; Backspace with nothing typed goes back', () => {
    const { p } = picker('@')
    p.choose({ type: 'kind', kind: 'task' })
    expect(titles(p.rows.value)).toEqual(['接入搜索'])

    const handled = p.onKeydown(new KeyboardEvent('keydown', { key: 'Backspace' }))

    expect(handled).toBe(true)
    expect(p.kind.value).toBeNull()
  })

  it('with five things cited, offers nothing more to pick', () => {
    const five = Array.from({ length: MOST_CITED }, (_, i) => `<#x${i}>`).join(' ')
    expect(titles(picker(`${five} @`).p.rows.value)).toEqual(['full'])
  })

  it('Esc closes it, and the @ stays in the text', async () => {
    const { p, draft } = picker('@接')
    p.onKeydown(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()

    expect(p.open.value).toBe(false)
    expect(draft.value).toBe('@接')
  })

  it('arrow keys move through what can be picked, skipping the group titles', () => {
    const { p } = picker('@接入')
    p.onKeydown(new KeyboardEvent('keydown', { key: 'ArrowDown' }))
    p.onKeydown(new KeyboardEvent('keydown', { key: 'ArrowDown' }))

    const chosen = p.choices.value[p.active.value]
    expect(chosen.type === 'item' && chosen.item.title).toBe('搜索接入方案')
  })
})
