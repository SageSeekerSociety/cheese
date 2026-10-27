// 并排看两格的规则：此刻这格在左，上一格在右；点右边那格就换边；关掉右边那格、面板
// 变窄、在手机上，都收回一列。
import { nextTick, reactive, ref } from 'vue'
import { expect, it } from 'vitest'

import { SPLIT_MIN_WIDTH, usePanelSplit } from './usePanelSplit'

function setup(width = 1000, phone = false) {
  const active = ref('overview')
  const w = ref(width)
  const p = ref(phone)
  // 响应式的：面板里 exists 读的是响应式的页签表，一格没了才会自己收回。
  const keys = reactive(new Set(['overview', 'preview', 'file:a.md', 'chat']))
  const s = usePanelSplit(active, { width: w, phone: p, exists: (k) => keys.has(k) })
  return { active, w, p, keys, ...s }
}

it('还没看过别的格时不能并排；看过一格以后，和那一格并排', async () => {
  const s = setup()
  expect(s.canSplit.value).toBe(false)
  s.active.value = 'file:a.md'
  await nextTick()
  expect(s.canSplit.value).toBe(true)
  s.toggle()
  expect(s.split.value).toBe('overview')
  expect(s.shown('overview')).toBe(true)
  expect(s.shown('file:a.md')).toBe(true)
  expect(s.shown('preview')).toBe(false)
  s.toggle()
  expect(s.split.value).toBeNull()
})

it('点右边那一格：两格换个边，不丢', async () => {
  const s = setup()
  s.active.value = 'file:a.md'
  await nextTick()
  s.toggle()
  s.active.value = 'overview'
  await nextTick()
  expect(s.split.value).toBe('file:a.md')
})

it('关掉右边那一格、面板变窄、在手机上：收回一列', async () => {
  const s = setup()
  s.active.value = 'file:a.md'
  await nextTick()
  s.toggle()
  s.drop('overview')
  expect(s.split.value).toBeNull()

  s.active.value = 'preview'
  await nextTick()
  s.toggle()
  expect(s.split.value).toBe('file:a.md')
  s.w.value = SPLIT_MIN_WIDTH - 1
  await nextTick()
  expect(s.split.value).toBeNull()
  expect(s.canSplit.value).toBe(false)

  const phone = setup(1000, true)
  phone.active.value = 'preview'
  await nextTick()
  expect(phone.canSplit.value).toBe(false)
})

it('「对话」那一格（手机上）不当作上一格：越过它，和再往前那一格并排', async () => {
  const s = setup()
  s.active.value = 'chat'
  await nextTick()
  s.active.value = 'preview'
  await nextTick()
  s.toggle()
  expect(s.split.value).toBe('overview')
})

it('右边那一格不经「关掉」就没了（临时页签被下一份文件顶掉）：自己收回一列', async () => {
  const s = setup()
  s.active.value = 'file:a.md'
  await nextTick()
  s.active.value = 'preview'
  await nextTick()
  s.toggle()
  expect(s.split.value).toBe('file:a.md')
  s.keys.delete('file:a.md')
  await nextTick()
  expect(s.split.value).toBeNull()
})
