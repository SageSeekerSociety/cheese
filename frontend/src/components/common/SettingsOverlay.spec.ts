// 设置浮层：说得出的几条是，Esc 和 × 都能关掉；设置里开着的对话框上按 Esc 只关对话
// 框，不连设置一起关；手机上打开设置先看到目录，点进一项才是那一页。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import SettingsOverlay from './SettingsOverlay.vue'

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
})
afterAll(() => vi.unstubAllGlobals())
afterEach(() => {
  document.body.innerHTML = ''
})

const groups = [
  {
    key: 'account',
    title: '账号',
    items: [
      { key: 'profile', label: '个人资料', to: '/profile' },
      { key: 'security', label: '密码与安全', to: '/security' },
    ],
  },
]

function mount(width: number, active: string | null) {
  ;(window as unknown as { innerWidth: number }).innerWidth = width
  const onClose = vi.fn()
  const Host = {
    components: { SettingsOverlay },
    data: () => ({ dialog: false }),
    setup: () => ({ onClose, groups, active }),
    template: `
      <v-app>
        <SettingsOverlay label="个人设置" :groups="groups" :active="active" index-to="/"
          close-label="关闭设置" close-title="关闭设置（Esc）" back-label="返回设置" @close="onClose">
          <p>这一页的内容</p>
          <button type="button" @click="dialog = true">打开对话框</button>
          <v-dialog v-model="dialog"><v-card>确认一下</v-card></v-dialog>
        </SettingsOverlay>
      </v-app>`,
  }
  render(Host, { global: { plugins: [createVuetify({ components, directives })] } })
  return onClose
}

describe('设置浮层', () => {
  it('× 和 Esc 都关掉它', async () => {
    const onClose = mount(1280, 'profile')
    await fireEvent.click(screen.getByRole('button', { name: '关闭设置' }))
    expect(onClose).toHaveBeenCalledTimes(1)
    await fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('开着对话框时按 Esc 不关设置', async () => {
    const onClose = mount(1280, 'profile')
    await fireEvent.click(screen.getByRole('button', { name: '打开对话框' }))
    await screen.findByText('确认一下')
    await fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
  })

  it('手机上先是目录，选了一项才是那一页', async () => {
    mount(390, null)
    await waitFor(() => expect(screen.getByText('密码与安全')).toBeTruthy())
    expect(screen.queryByText('这一页的内容')).toBeNull()
    document.body.innerHTML = ''

    mount(390, 'security')
    expect(await screen.findByText('这一页的内容')).toBeTruthy()
  })

  it('目录里按名字搜：只剩对得上的；都对不上时说一句', async () => {
    mount(1280, 'profile')
    const box = document.querySelector('.so__search input') as HTMLInputElement
    await fireEvent.update(box, '安全')
    expect(screen.queryByText('个人资料')).toBeNull()
    expect(screen.getByText('密码与安全')).toBeTruthy()
    await fireEvent.update(box, '没有这一项')
    expect(screen.queryByText('密码与安全')).toBeNull()
    expect(document.body.textContent).toContain('No settings named “没有这一项”')
  })

  it('搜索框里有字时 Esc 只清字，不关设置', async () => {
    const onClose = mount(1280, 'profile')
    const box = document.querySelector('.so__search input') as HTMLInputElement
    await fireEvent.update(box, '安全')
    await fireEvent.keyDown(box, { key: 'Escape' })
    expect(box.value).toBe('')
    expect(onClose).not.toHaveBeenCalled()
  })
})
