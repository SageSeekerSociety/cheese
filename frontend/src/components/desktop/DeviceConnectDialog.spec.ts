// 接入这台设备的对话框，人说得出的几条：问的时候选「暂不接入」就什么都不接；接入进行中关掉
// 对话框是取消接入，不是让它在后台接着跑；接好之后关掉不会再接一次。
import type { ConnectStep } from '@/lib/desktop'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import DeviceConnectDialog from './DeviceConnectDialog.vue'

import { setLocale } from '@/i18n'

beforeAll(() => {
  setLocale('zh-CN')
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
  ;(window as unknown as { innerWidth: number }).innerWidth = 1280
})
afterAll(() => vi.unstubAllGlobals())
afterEach(() => {
  document.body.innerHTML = ''
})

function mount(stage: 'ask' | 'progress' | 'done') {
  const props = {
    open: true,
    stage,
    steps: ['download', 'approve', 'start'] as ConnectStep[],
    current: (stage === 'progress' ? 'download' : null) as ConnectStep | null,
    percent: stage === 'progress' ? 40 : null,
    failure: null,
    mac: true,
    deviceName: 'MacBook Air',
    teams: [],
    teamIds: [],
    claudeLoggedIn: false,
    claudePlan: null,
    claudeState: 'idle' as const,
    error: null,
  }
  return render(DeviceConnectDialog, {
    props,
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

describe('connecting this device', () => {
  it('connects nothing when asked and declined', async () => {
    const view = mount('ask')
    await fireEvent.click(await screen.findByText('暂不接入'))
    expect(view.emitted('later')).toHaveLength(1)
    expect(view.emitted('connect')).toBeUndefined()
  })

  it('connects when asked and accepted', async () => {
    const view = mount('ask')
    await fireEvent.click(await screen.findByText('接入'))
    expect(view.emitted('connect')).toHaveLength(1)
  })

  it('cancels the connection when closed while it is under way', async () => {
    const view = mount('progress')
    await fireEvent.click(await screen.findByText('取消'))
    expect(view.emitted('cancel')).toHaveLength(1)
    expect(view.emitted('finish')).toBeUndefined()
  })

  it('keeps the name given before finishing', async () => {
    const view = mount('done')
    const name = (await screen.findByLabelText('设备名称')) as HTMLInputElement
    await fireEvent.update(name, '宿舍的 Mac')
    await fireEvent.click(screen.getByText('完成'))
    expect(view.emitted('rename')?.at(-1)).toEqual(['宿舍的 Mac'])
    expect(view.emitted('finish')).toHaveLength(1)
    expect(view.emitted('connect')).toBeUndefined()
  })
})
