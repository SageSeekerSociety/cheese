// 一列带 ⋯ 的行：右键哪一行，弹的就是那一行的操作；⋯ 照旧能点开。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { useRowMenu } from './useRowMenu'

import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'

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
    height: 800,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.stubGlobal('devicePixelRatio', 1)
})
afterAll(() => vi.unstubAllGlobals())
afterEach(() => {
  document.body.innerHTML = ''
})

function mount(onSelect: (row: string) => void) {
  ;(window as unknown as { innerWidth: number }).innerWidth = 1280
  const Host = {
    components: { AdaptiveMenu },
    setup() {
      const rowMenu = useRowMenu<string>()
      const actions = (row: string) => [
        { key: 'open', label: `打开 ${row}`, icon: 'mdi-open', onSelect: () => onSelect(row) },
      ]
      return { rowMenu, actions, rows: ['甲', '乙'] }
    },
    template: `
      <v-app>
        <ul>
          <li v-for="row in rows" :key="row" @contextmenu="rowMenu.open(row, $event)">
            <span>{{ row }}</span>
            <a :href="'/rows/' + row">{{ row }} 的链接</a>
            <AdaptiveMenu v-bind="rowMenu.bind(row)" :actions="actions(row)">
              <template #activator="{ props }">
                <button type="button" v-bind="props">{{ row }} 的操作</button>
              </template>
            </AdaptiveMenu>
          </li>
        </ul>
      </v-app>`,
  }
  return render(Host, { global: { plugins: [createVuetify({ components, directives })] } })
}

describe('useRowMenu', () => {
  it('右键哪一行就弹那一行的操作，选了就执行', async () => {
    const onSelect = vi.fn()
    mount(onSelect)
    await fireEvent.contextMenu(screen.getByText('乙'), { clientX: 30, clientY: 60 })
    await fireEvent.click(await screen.findByText('打开 乙'))
    expect(onSelect).toHaveBeenCalledWith('乙')
    expect(screen.queryByText('打开 甲')).toBeNull()
  })

  it('右键在链接上、或者正选着字：留给浏览器自己的菜单', async () => {
    mount(vi.fn())
    const onLink = new MouseEvent('contextmenu', { bubbles: true, cancelable: true })
    screen.getByText('乙 的链接').dispatchEvent(onLink)
    expect(onLink.defaultPrevented).toBe(false)

    const range = document.createRange()
    range.selectNodeContents(screen.getByText('乙'))
    window.getSelection()!.removeAllRanges()
    window.getSelection()!.addRange(range)
    const withSelection = new MouseEvent('contextmenu', { bubbles: true, cancelable: true })
    screen.getByText('乙').dispatchEvent(withSelection)
    expect(withSelection.defaultPrevented).toBe(false)
    window.getSelection()!.removeAllRanges()
    await new Promise((r) => setTimeout(r, 0))
    expect(screen.queryByText('打开 乙')).toBeNull()
  })

  it('⋯ 照旧点得开', async () => {
    const onSelect = vi.fn()
    mount(onSelect)
    await fireEvent.click(screen.getByText('甲 的操作'))
    await fireEvent.click(await screen.findByText('打开 甲'))
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith('甲'))
  })
})
