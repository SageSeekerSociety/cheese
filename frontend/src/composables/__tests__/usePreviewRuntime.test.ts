import { defineComponent, h, nextTick } from 'vue'
import { render } from '@testing-library/vue'
import { afterEach, expect, it, vi } from 'vitest'

import { usePreviewFrames } from '../usePreviewFrames'

vi.mock('../../lib/previewSession', () => ({ postPreviewSession: vi.fn() }))

const wrappers: ReturnType<typeof render>[] = []
afterEach(() => wrappers.splice(0).forEach((wrapper) => wrapper.unmount()))

async function setup() {
  let host!: ReturnType<typeof usePreviewFrames>
  const wrapper = render(
    defineComponent({
      setup() {
        host = usePreviewFrames('runtime-test')
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
