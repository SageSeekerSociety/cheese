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
    path: 'output/a.md',
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

it('只清掉发出去的那几条：只留评论的不经对话发，不能跟着清', () => {
  // 今天篮里只有交给芝士的，所以这一条守的是「去处菜单加了一项之后」那种情形：
  // 整批清掉就是把没发出去的那几条无声地丢了。
  const api = usePendingNotes(() => true)
  api.add(note({ message: '甲' }))
  api.add(note({ message: '乙', audience: 'comment' }))
  expect(api.send('')).toBe(true)
  const left = api.notes.value
  expect(left).toHaveLength(1)
  expect(left[0].audience).toBe('comment')
  expect(left[0].message).toBe('乙')
})

it('发送失败那句话不会跟到新的一批上', () => {
  // 托盘只在有货时出现：上一次失败的那句留着，读者清空篮、再攒一条时它会跟着这一条
  // 一起冒出来，可这一批压根没按过发送。
  const api = usePendingNotes(() => false)
  api.add(note())
  expect(api.send('')).toBe(false)
  expect(api.failed.value).toBe(true)
  // 逐条删空（托盘上每行的 ✕），再攒一条新的。
  api.remove(api.notes.value[0].id)
  api.add(note({ message: '新的' }))
  expect(api.failed.value).toBe(false)
})

it('删光交给芝士的那几条之后，那句话就清了', () => {
  // 这句话说的是「带得走的没发出去」。篮里还剩一条只挂评论的时，它已经不指任何东西了：
  // 「全部发送」那时是灰的（带得走的 0 条），发送键也不动它，所以不在这里清就再没有一条
  // 路能清掉它。判据得用带得走的那些，不是总数。
  const api = usePendingNotes(() => false)
  api.add(note({ message: '甲' }))
  api.add(note({ message: '乙', audience: 'comment' }))
  expect(api.send('')).toBe(false)
  expect(api.failed.value).toBe(true)
  const cheeseId = api.notes.value.find((n) => n.audience === 'cheese')!.id
  api.remove(cheeseId)
  expect(api.notes.value).toHaveLength(1)
  expect(api.failed.value).toBe(false)
})

it('发送成功之后那句失败话也清了', () => {
  let ok = false
  const api = usePendingNotes(() => ok)
  api.add(note())
  expect(api.send('')).toBe(false)
  expect(api.failed.value).toBe(true)
  api.add(note())
  ok = true
  expect(api.send('')).toBe(true)
  expect(api.failed.value).toBe(false)
})
