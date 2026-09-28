// 表单弹窗：桌面是对话框，手机是整页。人说得出的几条：主操作按了才执行；正在提交
// 的时候关不掉（关掉了，人以为没提交）；分步表单的「上一步」在手机上也够得着。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import AdaptiveDialog from './AdaptiveDialog.vue'

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
    width: 390,
    height: 844,
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

function mount(width: number, props: Record<string, unknown>) {
  ;(window as unknown as { innerWidth: number }).innerWidth = width
  const onPrimary = vi.fn()
  const onBack = vi.fn()
  const Host = {
    components: { AdaptiveDialog },
    props: ['extra'],
    data: () => ({ open: true }),
    setup: () => ({ onPrimary, onBack }),
    template: `
      <v-app>
        <span data-testid="state">{{ open ? 'open' : 'closed' }}</span>
        <AdaptiveDialog v-model="open" title="新建" primary-label="创建" v-bind="extra" @primary="onPrimary">
          <input aria-label="名称" />
          <template #actions><button type="button" @click="onBack">上一步</button></template>
        </AdaptiveDialog>
      </v-app>`,
  }
  render(Host, { props: { extra: props }, global: { plugins: [createVuetify({ components, directives })] } })
  return { onPrimary, onBack }
}

describe('AdaptiveDialog', () => {
  for (const [name, width] of [
    ['桌面', 1280],
    ['手机', 390],
  ] as const) {
    it(`${name}：正在提交时关不掉`, async () => {
      mount(width, { closeDisabled: true })
      const close = await screen.findByRole('button', {
        name: width > 960 ? t('global.cancel') : t('navigation.shell.close'),
      })
      await fireEvent.click(close)
      expect(screen.getByTestId('state').textContent).toBe('open')
    })

    it(`${name}：次要操作和主操作都点得到`, async () => {
      const { onPrimary, onBack } = mount(width, {})
      await fireEvent.click(await screen.findByRole('button', { name: '上一步' }))
      expect(onBack).toHaveBeenCalledTimes(1)
      await fireEvent.click(screen.getByRole('button', { name: '创建' }))
      expect(onPrimary).toHaveBeenCalledTimes(1)
    })
  }
})
