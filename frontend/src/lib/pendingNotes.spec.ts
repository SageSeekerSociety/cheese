/**
 * 攒着的待发批注怎么合成一条消息：各条按空行连起来，可选的总体说明写在最前面。
 * 只有「交给芝士」的那些并进去——只留评论的那几条不经对话发。
 */
import type { PendingNote } from './pendingNotes'

import { expect, it, vi } from 'vitest'

import { composePending, usePendingNotes } from './pendingNotes'

function note(over: Partial<PendingNote> = {}): PendingNote {
  return {
    id: 'x',
    file: 'a.md',
    version: '',
    where: '开头',
    quote: 'q',
    text: 't',
    audience: 'cheese',
    message: 'm',
    ...over,
  }
}

it('没有总体说明时，就是各条按空行连起来', () => {
  expect(composePending([note({ message: '甲' }), note({ message: '乙' })], '')).toBe('甲\n\n乙')
})

it('有总体说明时写在最前面，后面空一行再跟各条', () => {
  expect(composePending([note({ message: '甲' }), note({ message: '乙' })], '都按这个来')).toBe(
    '都按这个来\n\n甲\n\n乙'
  )
})

it('只有评论的那几条不进这一条消息', () => {
  expect(composePending([note({ message: '甲' }), note({ message: '乙', audience: 'comment' })], '')).toBe('甲')
})

it('加一条就多一条，id 各不相同', () => {
  const api = usePendingNotes(() => true)
  api.add(note())
  api.add(note())
  expect(api.notes.value).toHaveLength(2)
  expect(new Set(api.notes.value.map((n) => n.id)).size).toBe(2)
})

it('send 把这一批交给对话栏，收下了才清空', () => {
  const send = vi.fn(() => true)
  const api = usePendingNotes(send)
  api.add(note({ message: '甲' }))
  expect(api.send('说明')).toBe(true)
  expect(send).toHaveBeenCalledWith('说明\n\n甲')
  expect(api.notes.value).toHaveLength(0)
})

it('对话栏没收下时这一批留着，不能悄悄丢掉', () => {
  const api = usePendingNotes(() => false)
  api.add(note())
  expect(api.send('')).toBe(false)
  expect(api.notes.value).toHaveLength(1)
})
