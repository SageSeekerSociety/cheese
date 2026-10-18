// 「移动到…」：人说得出的几条——文档现在所在的地方选不了；没选地方之前「移动」按不了；
// 按下去挪到的是人选的那一处；按名字找一个项目时，看得出它在哪个团队里。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import DocMoveDialog, { type MovePlace } from './DocMoveDialog.vue'

import { t } from '@/i18n'

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  vi.stubGlobal('visualViewport', {
    width: 1280,
    height: 900,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  ;(window as unknown as { innerWidth: number }).innerWidth = 1280
})
afterAll(() => vi.unstubAllGlobals())
afterEach(() => {
  document.body.innerHTML = ''
})

const PLACES: MovePlace[] = [
  { teamId: 1, name: 'zhanghua', personal: true, projects: [] },
  {
    teamId: 2,
    name: '视觉课题组',
    personal: false,
    projects: [
      { id: 'p-a', name: '图像分割' },
      { id: 'p-b', name: '组会纪要' },
    ],
  },
]

function mount(current = { teamId: 2, projectId: null as string | null }) {
  const onMove = vi.fn()
  const Host = {
    components: { DocMoveDialog },
    data: () => ({ open: true, places: PLACES, current }),
    setup: () => ({ onMove }),
    template: `
      <v-app>
        <DocMoveDialog v-model="open" title="新人须知" :places="places" :current="current"
          :saving="false" error="" @move="onMove" />
      </v-app>`,
  }
  render(Host, { global: { plugins: [createVuetify({ components, directives })] } })
  return { onMove }
}

const moveButton = () => screen.findByRole('button', { name: t('work.room.doc.moveDialog.move') })

describe('DocMoveDialog', () => {
  it('文档现在所在的地方选不了', async () => {
    mount()
    const here = await screen.findByRole('option', { name: /视觉课题组/ })
    expect((here as HTMLButtonElement).disabled).toBe(true)
  })

  it('没选地方之前移动不了，选了之后挪到选的那一处', async () => {
    const { onMove } = mount()
    expect(((await moveButton()) as HTMLButtonElement).disabled).toBe(true)

    await fireEvent.click(await screen.findByRole('option', { name: /组会纪要/ }))
    await fireEvent.click(await moveButton())
    expect(onMove).toHaveBeenCalledWith({ teamId: 2, projectId: 'p-b' })
  })

  it('选自己名下时说清只有自己能读', async () => {
    const { onMove } = mount()
    await fireEvent.click(await screen.findByRole('option', { name: new RegExp(t('work.room.doc.moveDialog.mine')) }))
    expect(screen.getByTestId('doc-move-readers').textContent).toContain(t('work.room.doc.moveDialog.readersPersonal'))
    await fireEvent.click(await moveButton())
    expect(onMove).toHaveBeenCalledWith({ teamId: 1, projectId: null })
  })

  it('按名字找到一个项目时，它所在的团队也留着', async () => {
    mount({ teamId: 1, projectId: null })
    await fireEvent.update(await screen.findByPlaceholderText(t('work.room.doc.moveDialog.search')), '分割')
    const rows = screen.getAllByRole('option').map((o) => o.textContent ?? '')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toContain('视觉课题组')
    expect(rows[1]).toContain('图像分割')
  })
})
