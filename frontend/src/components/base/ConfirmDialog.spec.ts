/** 确认框只有两颗按钮、没有 ✕、点遮罩不关：这几条是它和表单弹窗的分别，锁在这里。 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ConfirmDialog from './ConfirmDialog.vue'

import { setLocale } from '@/i18n'

beforeEach(() => {
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function mount(props: Record<string, unknown> = {}) {
  return render(ConfirmDialog, {
    props: { modelValue: true, title: 'Remove Alice?', confirmLabel: 'Remove', ...props },
    slots: { default: 'She will lose access to this project.' },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

describe('ConfirmDialog', () => {
  it('shows the title, the consequence and exactly two buttons', async () => {
    mount()
    expect(await screen.findByText('Remove Alice?')).toBeTruthy()
    expect(screen.getByText('She will lose access to this project.')).toBeTruthy()
    const dialog = screen.getByRole('alertdialog')
    expect(dialog.querySelectorAll('button')).toHaveLength(2)
  })

  it('emits confirm from the action button and closes on cancel', async () => {
    const view = mount()
    await fireEvent.click(await screen.findByRole('button', { name: 'Remove' }))
    expect(view.emitted('confirm')).toHaveLength(1)
    await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(view.emitted('cancel')).toHaveLength(1)
    expect(view.emitted('update:modelValue')?.at(-1)).toEqual([false])
  })

  it('draws a solid red confirm button when danger', async () => {
    mount({ danger: true })
    const button = await screen.findByRole('button', { name: 'Remove' })
    expect(button.classList.contains('bg-error')).toBe(true)
  })

  it('does not confirm or cancel while loading', async () => {
    const view = mount({ loading: true })
    await fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
    expect(view.emitted('cancel')).toBeUndefined()
  })
})
