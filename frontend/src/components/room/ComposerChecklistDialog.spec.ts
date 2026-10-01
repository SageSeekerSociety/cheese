// 在输入框里发一张自己的清单：一行一步，空行不算；发出去了才关上，没发出去字还在。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ComposerChecklistDialog from './ComposerChecklistDialog.vue'

import { setLocale } from '@/i18n'

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
beforeEach(() => setLocale('zh-CN'))
afterEach(() => {
  document.body.innerHTML = ''
})

function mount(post: (steps: string[]) => Promise<boolean>) {
  ;(window as unknown as { innerWidth: number }).innerWidth = 1280
  const Host = {
    components: { ComposerChecklistDialog },
    data: () => ({ open: true }),
    setup: () => ({ post }),
    template: `
      <v-app>
        <span data-testid="state">{{ open ? 'open' : 'closed' }}</span>
        <ComposerChecklistDialog v-model="open" :post="post" />
      </v-app>`,
  }
  render(Host, { global: { plugins: [createVuetify({ components, directives })] } })
}

async function type(text: string) {
  await fireEvent.update(screen.getByLabelText('步骤'), text)
}

describe('发一张清单', () => {
  it('一行一步，空行和首尾空白不算', async () => {
    const post = vi.fn().mockResolvedValue(true)
    mount(post)
    await type('  核实问题 \n\n写实现\n   \n补测试')
    await fireEvent.click(screen.getByRole('button', { name: '发送' }))
    expect(post).toHaveBeenCalledWith(['核实问题', '写实现', '补测试'])
    await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('closed'))
  })

  it('没发出去就不关，写的字还在', async () => {
    const post = vi.fn().mockResolvedValue(false)
    mount(post)
    await type('写实现')
    await fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(post).toHaveBeenCalled())
    expect(screen.getByTestId('state').textContent).toBe('open')
    expect((screen.getByLabelText('步骤') as HTMLTextAreaElement).value).toBe('写实现')
  })

  it('一步都没有、或者超过 30 步时发不出去', async () => {
    const post = vi.fn().mockResolvedValue(true)
    mount(post)
    await fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await type(Array.from({ length: 31 }, (_, i) => `第 ${i + 1} 步`).join('\n'))
    await fireEvent.click(screen.getByRole('button', { name: '发送' }))
    expect(post).not.toHaveBeenCalled()
  })
})
