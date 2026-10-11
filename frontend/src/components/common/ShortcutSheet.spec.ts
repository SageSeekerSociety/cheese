// The shortcut sheet opens from `?` or the keyboard button beside the composer.
// A keyboard user must stay inside it while it is open, and land back where they
// were when it closes, not on <body>.
import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'

import ShortcutSheet from './ShortcutSheet.vue'

beforeAll(() => {
  // Vuetify's overlay measures the viewport and watches sizes; happy-dom lacks these.
  const g = globalThis as unknown as Record<string, unknown>
  g.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  g.visualViewport ??= Object.assign(new EventTarget(), {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    scale: 1,
  })
  g.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {} })
})
afterEach(cleanup)

const groups = [{ scope: 'Chat', rows: [{ keys: ['Enter'], action: 'Send' }] }]

function mount() {
  const open = ref(false)
  const Host = defineComponent(() => () => [
    h('button', { onClick: () => (open.value = true) }, 'Shortcuts'),
    h(ShortcutSheet, {
      modelValue: open.value,
      title: 'Keyboard shortcuts',
      groups,
      'onUpdate:modelValue': (v: boolean) => (open.value = v),
    }),
  ])
  const view = render(Host, { global: { plugins: [createVuetify({ components, directives })] } })
  return { view, open }
}

describe('shortcut sheet', () => {
  it('has a place inside it for focus, so Tab does not leave for the page behind', async () => {
    const { view } = mount()
    await fireEvent.click(view.getByRole('button', { name: 'Shortcuts' }))

    const region = await screen.findByLabelText('Keyboard shortcuts')
    expect(region.tabIndex).toBe(0)
    expect(region.closest('.v-overlay__content')).not.toBeNull()
  })

  it('gives focus back to what opened it when it closes', async () => {
    const { view, open } = mount()
    const opener = view.getByRole('button', { name: 'Shortcuts' })
    opener.focus()
    await fireEvent.click(opener)
    await screen.findByLabelText('Keyboard shortcuts')
    ;(document.activeElement as HTMLElement | null)?.blur()

    open.value = false
    await waitFor(() => expect(document.activeElement).toBe(opener))
  })
})
