// The desktop app's 关于 dialog and the top bar's 重启以完成更新: what the
// person sees of the app's own version and update. The web app ships before the
// app does, so an app that cannot report its update must see no button that
// asks it to, and a browser sees none of this.
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

type AppWindow = { __TAURI__?: unknown; __CHEESE_APP__?: unknown }
type Status = { state: string; version?: string }

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
  ;(window as unknown as { innerWidth: number }).innerWidth = 1280
})
afterAll(() => vi.unstubAllGlobals())

// Each test gets fresh modules: the update status is one for the whole page.
beforeEach(() => vi.resetModules())
afterEach(() => {
  cleanup()
  document.body.innerHTML = ''
  delete (window as AppWindow).__TAURI__
  delete (window as AppWindow).__CHEESE_APP__
})

/** An app that says what `about` says, answers the update commands, and can send events. */
function desktopApp(about: object, answers: { status?: Status; check?: Status; restart?: () => Promise<void> } = {}) {
  const handlers = new Map<string, (event: { payload: unknown }) => void>()
  const invoke = vi.fn(async (cmd: string) => {
    if (cmd === 'update_status') return answers.status ?? { state: 'idle' }
    if (cmd === 'check_for_updates') return answers.check ?? { state: 'latest' }
    if (cmd === 'restart_to_update') return answers.restart?.()
    return undefined
  })
  const listen = vi.fn(async (event: string, handle: (event: { payload: unknown }) => void) => {
    handlers.set(event, handle)
    return () => handlers.delete(event)
  })
  ;(window as AppWindow).__TAURI__ = { core: { invoke }, event: { listen } }
  ;(window as AppWindow).__CHEESE_APP__ = { origin: 'https://okcheese.com', ...about }
  const send = (event: string, payload: unknown = null) => handlers.get(event)?.({ payload })
  return { invoke, send, handlers }
}

const NEW_APP = { version: '0.1.42', can: ['links', 'updates'] }

async function mount() {
  const { setLocale } = await import('@/i18n')
  setLocale('zh-CN')
  const { default: DesktopAboutDialog } = await import('./DesktopAboutDialog.vue')
  const { default: DesktopUpdateReady } = await import('./Navigation/DesktopUpdateReady.vue')
  const { useDesktopApp } = await import('@/composables/useDesktopApp')
  const Host = {
    components: { DesktopAboutDialog, DesktopUpdateReady },
    template: '<v-app><DesktopUpdateReady /><DesktopAboutDialog /></v-app>',
  }
  render(Host, { global: { plugins: [createVuetify({ components, directives })] } })
  return useDesktopApp()
}

describe('关于 in the desktop app', () => {
  it('opens from the app menu and shows the app’s version and the web build', async () => {
    const app = desktopApp(NEW_APP)
    await mount()
    await waitFor(() => expect(app.handlers.has('show-about')).toBe(true))
    app.send('show-about')
    expect(await screen.findByText('0.1.42')).toBeTruthy()
    // A test build is made from no commit.
    expect(screen.getByText('开发版')).toBeTruthy()
  })

  it('checks for updates when asked and says the app is current', async () => {
    const app = desktopApp(NEW_APP)
    const { aboutOpen } = await mount()
    aboutOpen.value = true
    await fireEvent.click(await screen.findByRole('button', { name: '检查更新' }))
    expect(app.invoke).toHaveBeenCalledWith('check_for_updates')
    expect(await screen.findByText('已是最新版本')).toBeTruthy()
  })

  it('shows the download as it happens', async () => {
    const app = desktopApp(NEW_APP)
    const { aboutOpen } = await mount()
    aboutOpen.value = true
    await waitFor(() => expect(app.handlers.has('update-status')).toBe(true))
    app.send('update-status', { state: 'downloading', version: '0.1.43' })
    expect(await screen.findByText('正在下载新版本 0.1.43…')).toBeTruthy()
  })

  it('restarts to finish a downloaded update only when clicked', async () => {
    const app = desktopApp(NEW_APP, { status: { state: 'ready', version: '0.1.43' } })
    const { aboutOpen } = await mount()
    aboutOpen.value = true
    expect(await screen.findByText('新版本 0.1.43 已下载')).toBeTruthy()
    expect(app.invoke).not.toHaveBeenCalledWith('restart_to_update')
    const [, inDialog] = await screen.findAllByRole('button', { name: '重启以完成更新' })
    await fireEvent.click(inDialog)
    expect(app.invoke).toHaveBeenCalledWith('restart_to_update')
  })

  it('says why it cannot restart while this computer is being connected', async () => {
    desktopApp(NEW_APP, {
      status: { state: 'ready', version: '0.1.43' },
      restart: () => Promise.reject('connecting'),
    })
    const { toast } = await import('vuetify-sonner')
    await mount()
    await fireEvent.click(await screen.findByRole('button', { name: '重启以完成更新' }))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('这台电脑正在接入，接入完成后再重启'))
  })

  it('asks an app from before updates could be checked for nothing', async () => {
    const app = desktopApp({ can: ['notices', 'badge', 'autostart', 'links'] })
    const { aboutOpen } = await mount()
    aboutOpen.value = true
    expect(await screen.findByText('较早的版本')).toBeTruthy()
    expect(screen.getByText('新版本会在窗口关上时自动安装')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '检查更新' })).toBeNull()
    expect(app.invoke).not.toHaveBeenCalled()
  })
})

describe('重启以完成更新 in the top bar', () => {
  it('appears once the app reports a downloaded update', async () => {
    const app = desktopApp(NEW_APP)
    await mount()
    await waitFor(() => expect(app.handlers.has('update-status')).toBe(true))
    expect(screen.queryByRole('button', { name: '重启以完成更新' })).toBeNull()
    app.send('update-status', { state: 'ready', version: '0.1.43' })
    expect(await screen.findByRole('button', { name: '重启以完成更新' })).toBeTruthy()
  })

  it('never appears in an app that cannot report its update', async () => {
    const app = desktopApp({ can: ['links'] })
    await mount()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(app.handlers.has('update-status')).toBe(false)
    expect(screen.queryByRole('button', { name: '重启以完成更新' })).toBeNull()
  })

  it('never appears in a browser', async () => {
    await mount()
    expect(screen.queryByRole('button', { name: '重启以完成更新' })).toBeNull()
  })
})
