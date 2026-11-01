/**
 * 文档条上的「看 / 批注 / 改」三段。
 *
 * 守两件事：
 *   - 没有这一份模式时整个控件不摆。它能被单独挂起来（组件预览站、文档台自己的用例），
 *     那时按下去什么都不做——几颗死键比不摆更糟。
 *   - 「改」只在真能就地改的这一份上摆；改不了就整颗不出现，不是灰着。
 */
import { nextTick, ref } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, expect, it } from 'vitest'

import { PREVIEW_MODE, type PreviewModeApi, usePreviewMode } from '@/composables/panelPreview/previewMode'

import PreviewModeSwitch from './PreviewModeSwitch.vue'

import { setLocale } from '@/i18n'

setLocale('zh-CN')

afterEach(cleanup)

const stubs = { VIcon: true }

function mount(mode: PreviewModeApi | null) {
  return render(PreviewModeSwitch, {
    global: {
      stubs,
      ...(mode ? { provide: { [PREVIEW_MODE as symbol]: mode } } : {}),
    },
  })
}

it('没有这一份模式时整个不摆：三颗按下去什么都不做的键比不摆更糟', () => {
  const ui = mount(null)
  expect(ui.queryByTestId('preview-mode-view')).toBeNull()
  expect(ui.container.querySelector('.mode-switch')).toBeNull()
})

it('这一份能改：「改」是可点的，title 是原型那句快捷键说明', () => {
  const ui = mount(
    usePreviewMode(
      () => true,
      () => 'a.docx'
    )
  )
  const edit = ui.getByTestId('preview-mode-edit')
  expect(edit.hasAttribute('disabled')).toBe(false)
  expect(edit.getAttribute('title')).toContain('快捷键 3')
})

it('这一份改不了：「改」那一颗不摆，不是灰着', () => {
  // 一颗按下去什么都不做的键，读者还得悬停才知道为什么（2026-11-01 负责人：「不能改你
  // 显示出来干啥，看着让人难受吗」）。不能改就不摆，留下能做的两颗。
  const ui = mount(
    usePreviewMode(
      () => false,
      () => 'a.pdf'
    )
  )
  expect(ui.queryByTestId('preview-mode-edit')).toBeNull()
  expect(ui.getByTestId('preview-mode-view')).toBeTruthy()
  expect(ui.getByTestId('preview-mode-note')).toBeTruthy()
})

it('这一份能改时「改」才出现：判据现问，协同会话连上那一刻它自己冒出来', async () => {
  // 真产品里 `canEdit` 读的是响应式的 `liveEdit.canEdit`，会话连上就从 false 变 true；
  // 这里用一个 ref 照它的形状。
  const canEdit = ref(false)
  const ui = mount(
    usePreviewMode(
      () => canEdit.value,
      () => 'a.md'
    )
  )
  expect(ui.queryByTestId('preview-mode-edit')).toBeNull()
  canEdit.value = true
  await nextTick()
  expect(ui.getByTestId('preview-mode-edit')).toBeTruthy()
})
