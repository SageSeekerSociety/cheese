// A page hands its panels to the shell; the shell lays out exactly the page on screen.
import type { PagePanels, PagePanelsHandle, PagePanelsHost } from './pagePanels'

import { defineComponent, h, nextTick, ref } from 'vue'
import { render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import { providePagePanels, usePagePanels } from './pagePanels'

const ROOM: PagePanels = {
  key: 'room',
  panels: [{ id: 'conversation' }, { id: 'work' }],
  arrangement: { direction: 'row', children: [{ panels: ['conversation'] }, { panels: ['work'] }] },
}

function shell() {
  let host!: PagePanelsHost
  const handles: Record<string, PagePanelsHandle> = {}
  const Page = defineComponent({
    props: { name: { type: String, required: true }, panels: { type: Boolean, default: true } },
    setup(p) {
      handles[p.name] = usePagePanels(() => (p.panels ? ROOM : null))
      return () => h('div', p.name)
    },
  })
  const shown = ref<string | null>('a')
  render(
    defineComponent({
      setup() {
        host = providePagePanels()
        return () => (shown.value ? h(Page, { key: shown.value, name: shown.value }) : null)
      },
    })
  )
  return { host: () => host, handles, shown }
}

describe('a page handing its panels to the shell', () => {
  it('is laid out by the shell while it is on screen, and gives the area back when it leaves', async () => {
    const { host, handles, shown } = shell()
    expect(host().claim.value?.spec.key).toBe('room')
    expect(handles.a.active.value).toBe(true)

    shown.value = null
    await nextTick()
    expect(host().claim.value).toBeNull()
  })

  it('the next page keeps its panels even though the previous one left in the same update', async () => {
    const { host, handles, shown } = shell()
    shown.value = 'b'
    await nextTick()
    expect(handles.b.active.value).toBe(true)
    expect(handles.a.active.value).toBe(false)
    expect(host().claim.value).not.toBeNull()
  })

  it('a page asks for one of its panels to fill the area, and only a page on screen can', async () => {
    const { host, handles, shown } = shell()
    handles.a.maximize('work')
    expect(handles.a.maximized.value).toBe('work')
    handles.a.restore()
    expect(host().maximized.value).toBeNull()

    shown.value = 'b'
    await nextTick()
    handles.a.maximize('work')
    expect(host().maximized.value).toBeNull()
  })
})
