import { defineComponent, h, nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { render } from '@testing-library/vue'
import { afterEach, expect, it, vi } from 'vitest'

import { defineCommands } from '../../commands'
import { installShortcuts } from '../../commands/shortcuts'
import { usePreviewFrames } from '../usePreviewFrames'

vi.mock('../../lib/previewSession', () => ({ postPreviewSession: vi.fn() }))

const wrappers: ReturnType<typeof render>[] = []
const undos: (() => void)[] = []
afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount())
  undos.splice(0).forEach((undo) => undo())
})

async function setup(options: Parameters<typeof usePreviewFrames>[1] = {}) {
  let host!: ReturnType<typeof usePreviewFrames>
  const wrapper = render(
    defineComponent({
      setup() {
        host = usePreviewFrames('runtime-test', options)
        return () =>
          h(
            'div',
            host.frames.value.map((frame) => h('iframe', { key: frame.id, name: frame.name }))
          )
      },
    })
  )
  wrappers.push(wrapper)
  await host.navigate(
    {
      url: 'https://preview-fixed.example/_cheese/session',
      grant: 'grant',
      resource_id: 'resource',
      resource: { kind: 'app', path: 'http://localhost:5173', instance: 'listener' },
    },
    {
      url: 'https://preview-fixed.example/',
      label: 'app',
      mime: 'text/html',
      live: true,
      version: null,
      identity: 'first',
    },
    () => true
  )
  await nextTick()
  const frame = wrapper.container.querySelector('iframe')!
  Object.defineProperty(frame, 'contentDocument', { get: () => null })
  const send = vi.spyOn(frame.contentWindow!, 'postMessage').mockImplementation(() => {})
  host.loaded(host.incoming.value!.id, { target: frame } as unknown as Event)
  const hello = send.mock.calls[0]![0]
  return { host, frame, hello, send }
}

it('load is navigation only; readiness requires current origin, window and session', async () => {
  const { host, frame, hello } = await setup()
  expect(host.displayed.value?.runtime).toBe('unconfirmed')
  const data = { ...hello, type: 'ready' }
  for (const invalid of [
    { origin: 'https://wrong.example', source: frame.contentWindow, data },
    { origin: 'https://preview-fixed.example', source: window, data },
    { origin: 'https://preview-fixed.example', source: frame.contentWindow, data: { ...data, sessionId: 'old' } },
  ])
    window.dispatchEvent(new MessageEvent('message', invalid))
  expect(host.displayed.value?.runtime).toBe('unconfirmed')
  window.dispatchEvent(
    new MessageEvent('message', { origin: 'https://preview-fixed.example', source: frame.contentWindow, data })
  )
  expect(host.displayed.value?.runtime).toBe('ready')
  host.reset()
  window.dispatchEvent(
    new MessageEvent('message', { origin: 'https://preview-fixed.example', source: frame.contentWindow, data })
  )
  expect(host.displayed.value).toBeNull()
})

it('displayed reload resets readiness and rejects the previous document session', async () => {
  const { host, frame, hello, send } = await setup()
  const dispatch = (sessionId: string) =>
    window.dispatchEvent(
      new MessageEvent('message', {
        origin: 'https://preview-fixed.example',
        source: frame.contentWindow,
        data: { ...hello, sessionId, type: 'ready' },
      })
    )
  dispatch(hello.sessionId)
  expect(host.displayed.value?.runtime).toBe('ready')
  const shown = host.displayed.value
  host.loaded(shown!.id, { target: frame } as unknown as Event)
  expect(host.displayed.value).toBe(shown)
  expect(shown?.runtime).toBe('unconfirmed')
  dispatch(hello.sessionId)
  expect(shown?.runtime).toBe('unconfirmed')
  const renewed = send.mock.calls[1]![0]
  expect(renewed.sessionId).not.toBe(hello.sessionId)
  dispatch(renewed.sessionId)
  expect(shown?.runtime).toBe('ready')
})

it('a bridge loaded after navigation can request the current handshake without replacing the frame', async () => {
  const { host, frame, hello, send } = await setup()
  const shown = host.displayed.value
  const request = { channel: 'cheese-preview-runtime', version: 1, type: 'hello-request' }
  for (const invalid of [
    { origin: 'https://wrong.example', source: frame.contentWindow, data: request },
    { origin: 'https://preview-fixed.example', source: window, data: request },
    { origin: 'https://preview-fixed.example', source: frame.contentWindow, data: { ...request, version: 2 } },
  ])
    window.dispatchEvent(new MessageEvent('message', invalid))
  expect(send.mock.calls).toHaveLength(1)
  window.dispatchEvent(
    new MessageEvent('message', {
      origin: 'https://preview-fixed.example',
      source: frame.contentWindow,
      data: request,
    })
  )
  expect(send.mock.calls).toHaveLength(2)
  const renewed = send.mock.calls[1]![0]
  expect(renewed.sessionId).toBe(hello.sessionId)
  expect(host.displayed.value?.runtime).toBe('unconfirmed')
  window.dispatchEvent(
    new MessageEvent('message', {
      origin: 'https://preview-fixed.example',
      source: frame.contentWindow,
      data: { ...renewed, type: 'ready' },
    })
  )
  expect(host.displayed.value?.runtime).toBe('ready')
  expect(host.displayed.value).toBe(shown)
  expect(document.querySelector(`iframe[name="${shown!.name}"]`)).toBe(frame)
})

it('keys travel with the handshake and only an id from that table runs a command', async () => {
  const run = vi.fn()
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:p(.*)*', component: {} }] })
  undos.push(installShortcuts(router))
  undos.push(defineCommands(() => [{ id: 'rail.1', title: '首页', shortcut: 'mod+1', run }]))
  const { frame, hello } = await setup()
  expect(hello.keys).toEqual([{ id: 'rail.1', mod: true, shift: false, alt: false, code: 'Digit1' }])
  const dispatch = (data: unknown) =>
    window.dispatchEvent(
      new MessageEvent('message', { origin: 'https://preview-fixed.example', source: frame.contentWindow, data })
    )
  // 别的 id、上个会话的 id、被换掉的会话，都不算。
  dispatch({ ...hello, type: 'key', id: 'library.upload' })
  dispatch({ ...hello, type: 'key', id: 'rail.1', sessionId: 'old' })
  dispatch({ ...hello, type: 'key', id: 'rail.1', version: 2 })
  expect(run).not.toHaveBeenCalled()
  dispatch({ ...hello, type: 'key', id: 'rail.1' })
  expect(run).toHaveBeenCalledTimes(1)
})

it('disconnect and same-instance recovery preserve browsing context; replacement stays gone', async () => {
  const { host, frame } = await setup()
  const displayed = host.displayed.value
  host.observeConnection(null, false)
  expect(host.displayed.value?.connection).toBe('disconnected')
  host.observeConnection('listener', true)
  expect(host.displayed.value?.connection).toBe('online')
  expect(host.displayed.value).toBe(displayed)
  expect(document.querySelector(`iframe[name="${displayed!.name}"]`)).toBe(frame)
  host.observeConnection('replacement', true)
  expect(host.displayed.value?.connection).toBe('gone')
  expect(host.incoming.value).toBeNull()
})

it('an escape from the current frame and session is handed back to the host', async () => {
  const escaped = vi.fn()
  const { host, frame, hello } = await setup({ onEscape: escaped })
  const dispatch = (
    data: unknown,
    source: MessageEventSource | null = frame.contentWindow,
    origin = 'https://preview-fixed.example'
  ) => window.dispatchEvent(new MessageEvent('message', { origin, source, data }))
  // 别的窗口、别的来源、别的会话发来的 escape 都不算。
  dispatch({ ...hello, type: 'escape' }, window)
  dispatch({ ...hello, type: 'escape' }, frame.contentWindow, 'https://wrong.example')
  dispatch({ ...hello, type: 'escape', sessionId: 'old' })
  expect(escaped).not.toHaveBeenCalled()
  dispatch({ ...hello, type: 'escape' })
  expect(escaped).toHaveBeenCalledTimes(1)
  // 交回控制权不是就绪信号：注入的页面从没报过 ready，它就绪状态仍是未确认。
  expect(host.displayed.value?.runtime).toBe('unconfirmed')
})
