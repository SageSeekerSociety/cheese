/** 看 / 批注 / 改 三段模式：默认哪一段、按文件记、改那一段什么时候给。 */
import { ref } from 'vue'
import { expect, it } from 'vitest'

import { MODE_KEYS, usePreviewMode } from './previewMode'

it('刚打开一份是「看」，三段都能切', () => {
  const mode = usePreviewMode(
    () => true,
    () => 'a.pdf'
  )
  expect(mode.mode.value).toBe('view')
  mode.set('note')
  expect(mode.mode.value).toBe('note')
  mode.set('edit')
  expect(mode.mode.value).toBe('edit')
})

it('模式按文件记：换一份回到「看」，换回来还认着', () => {
  const path = ref('a.pdf')
  const mode = usePreviewMode(
    () => true,
    () => path.value
  )
  mode.set('note')
  expect(mode.mode.value).toBe('note')
  path.value = 'b.pdf'
  // 刚打开一份东西先是在读它。
  expect(mode.mode.value).toBe('view')
  path.value = 'a.pdf'
  expect(mode.mode.value).toBe('note')
})

it('这一份改不了时，「改」切不过去、也不算在改', () => {
  // 判据是「这一份现在开不开得起来编辑」，可能是异步才为真的，所以每次问它。
  const canEdit = ref(false)
  const mode = usePreviewMode(
    () => canEdit.value,
    () => 'a.pdf'
  )
  expect(mode.enabled('edit')).toBe(false)
  mode.set('edit')
  expect(mode.mode.value).toBe('view')
  expect(mode.editing.value).toBe(false)
  // 协同会话连上之后，「改」才给。
  canEdit.value = true
  expect(mode.enabled('edit')).toBe(true)
  mode.set('edit')
  expect(mode.mode.value).toBe('edit')
  expect(mode.editing.value).toBe(true)
})

it('开着「改」时这一份变得改不了了，就不再算在改', () => {
  // 协同会话断了、编辑器关了：模式还停在「改」，但正文不该再是编辑态。
  const canEdit = ref(true)
  const mode = usePreviewMode(
    () => canEdit.value,
    () => 'a.pdf'
  )
  mode.set('edit')
  expect(mode.editing.value).toBe(true)
  canEdit.value = false
  expect(mode.editing.value).toBe(false)
})

it('「批注」只有那一段算数', () => {
  const mode = usePreviewMode(
    () => true,
    () => 'a.pdf'
  )
  mode.set('note')
  mode.set('edit')
})

it('快捷键 1/2/3 对着看/批注/改', () => {
  expect(MODE_KEYS).toEqual({ '1': 'view', '2': 'note', '3': 'edit' })
})
