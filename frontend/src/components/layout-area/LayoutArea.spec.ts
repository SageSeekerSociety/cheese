import type { LayoutArrangement, LayoutPanel } from '.'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LayoutArea } from '.'

import i18n from '@/i18n'

/** A panel that only remembers what was typed into it. */
const Note = defineComponent({
  props: { label: { type: String, required: true } },
  setup(p) {
    const text = ref('')
    return () =>
      h('textarea', {
        'aria-label': p.label,
        value: text.value,
        onInput: (e: Event) => (text.value = (e.target as HTMLTextAreaElement).value),
      })
  },
})

const PANELS: LayoutPanel[] = [
  { id: 'one', title: 'One', component: Note, props: { label: 'note one' } },
  { id: 'two', title: 'Two', component: Note, props: { label: 'note two' }, closable: true },
  { id: 'three', title: 'Three', component: Note, props: { label: 'note three' }, closable: true },
]

const DEFAULT: LayoutArrangement = {
  root: { direction: 'row', children: [{ panels: ['one'] }, { panels: ['two'] }] },
}

const KEY = 'test.layout-area'

function mount(props: Record<string, unknown> = {}) {
  return render(LayoutArea, {
    props: { panels: PANELS, defaultArrangement: DEFAULT, storageKey: KEY, version: 1, narrow: true, ...props },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

const tab = (name: string) => screen.getByRole('tab', { name })
/** The panel the reader is looking at: the one inside the visible tab panel. */
const shown = (label: string) => within(screen.getByRole('tabpanel')).getByLabelText<HTMLTextAreaElement>(label)

describe('LayoutArea on a narrow screen', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.stubGlobal('visualViewport', {
      width: 1024,
      height: 768,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
    })
  })
  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('keeps what was typed in a panel while another panel is shown', async () => {
    mount()
    await waitFor(() => shown('note one'))
    await fireEvent.update(shown('note one'), 'draft')
    await fireEvent.click(tab('Two'))
    await waitFor(() => shown('note two'))
    await fireEvent.click(tab('One'))
    await waitFor(() => expect(shown('note one').value).toBe('draft'))
  })

  it('shows the panels of the arrangement saved for this page', async () => {
    localStorage.setItem(KEY, JSON.stringify({ v: 1, root: { panels: ['three', 'one'], active: 'three' } }))
    mount()
    await screen.findByLabelText('note three')
    expect(screen.getAllByRole('tab').map((t) => t.textContent?.trim())).toEqual(['Three', 'One'])
  })

  it('ignores an arrangement saved under another version and starts from the default', async () => {
    localStorage.setItem(KEY, JSON.stringify({ v: 0, root: { panels: ['three'] } }))
    mount()
    await screen.findByLabelText('note one')
    expect(screen.getAllByRole('tab').map((t) => t.textContent?.trim())).toEqual(['One', 'Two'])
  })

  it('a panel in no cell can be brought back from "+", and is remembered there', async () => {
    mount()
    await fireEvent.click(await screen.findByRole('button', { name: i18n.global.t('shell.layout.add') }))
    await fireEvent.click(await screen.findByText('Three'))
    await waitFor(() => shown('note three'))
    const saved = JSON.parse(localStorage.getItem(KEY) ?? '{}')
    expect(JSON.stringify(saved.root)).toContain('three')
  })

  it('reset brings the default arrangement back', async () => {
    localStorage.setItem(KEY, JSON.stringify({ v: 1, root: { panels: ['three'] } }))
    const area = ref<{ reset(): void }>()
    render(
      defineComponent({
        setup: () => () =>
          h(LayoutArea, {
            ref: area,
            panels: PANELS,
            defaultArrangement: DEFAULT,
            storageKey: KEY,
            version: 1,
            narrow: true,
          }),
      }),
      { global: { plugins: [createVuetify({ components, directives }), i18n] } }
    )
    await screen.findByLabelText('note three')
    area.value!.reset()
    await waitFor(() => expect(screen.getAllByRole('tab').map((t) => t.textContent?.trim())).toEqual(['One', 'Two']))
  })
})
