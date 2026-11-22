/**
 * The 改动 tab reads and reviews; it does not edit. A file open there shows
 * its text without taking typing or offering a save, and「编辑」asks for it
 * to be opened in the task's 文件 panel instead.
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'
import { changesPanelProps } from '@/views/demo/catalogFixtures/panels'

vi.mock('../CodeEditor.vue', () => ({
  default: {
    name: 'CodeEditorStub',
    props: ['modelValue', 'filename', 'readonly'],
    template: '<textarea data-testid="editor" :value="modelValue" :readonly="readonly" />',
  },
}))

import PanelChangesView from './PanelChangesView.vue'

let vuetify: ReturnType<typeof createVuetify>
beforeAll(() => {
  vuetify = createVuetify({ components, directives })
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})
beforeEach(() => setLocale('zh-CN'))

describe('改动 does not edit files', () => {
  it('shows a file read-only, with no save, and hands editing to the 文件 panel', async () => {
    const view = render(PanelChangesView, {
      props: { ...changesPanelProps({ effectiveView: 'text', fileView: 'text' }), canEdit: true },
      global: { plugins: [vuetify] },
    })
    const editor = (await screen.findByTestId('editor')) as HTMLTextAreaElement
    expect(editor.readOnly).toBe(true)
    expect(screen.queryByRole('button', { name: '保存' })).toBeNull()

    await fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    expect(view.emitted('edit-file')).toEqual([[changesPanelProps().openPath]])
  })
})
