import type { PreviewFrame } from '@/composables/usePreviewFrames'
import type { ChainInput } from './previewChain'

import { expect, it } from 'vitest'

import { appAsleep, chainBroken, previewChain } from './previewChain'

const frame = (over: Partial<PreviewFrame> = {}): PreviewFrame => ({
  id: 1,
  name: 'f',
  url: 'https://preview.example/',
  label: 'app',
  mime: 'text/html',
  version: null,
  live: true,
  posted: true,
  connection: 'online',
  runtime: 'unconfirmed',
  ...over,
})
const base: ChainInput = {
  platformError: null,
  live: true,
  tunnelUp: true,
  appAnswering: true,
  frame: frame(),
  navigationError: '',
  agentName: '小 C',
}
const states = (input: Partial<ChainInput>) => previewChain({ ...base, ...input }).map((s) => `${s.key}:${s.state}`)

it('a running app is three green links; a static page is two', () => {
  expect(states({})).toEqual(['platform:ok', 'tunnel:ok', 'app:ok'])
  expect(chainBroken(previewChain(base))).toBe(false)
  expect(states({ live: false, frame: frame({ live: false }) })).toEqual(['platform:ok', 'app:ok'])
})

it('the first link that breaks is down, and the ones after it cannot be judged', () => {
  expect(states({ platformError: '502' })).toEqual(['platform:down', 'tunnel:wait', 'app:wait'])
  expect(states({ tunnelUp: false, appAnswering: false })).toEqual(['platform:ok', 'tunnel:down', 'app:wait'])
  expect(states({ appAnswering: false })).toEqual(['platform:ok', 'tunnel:ok', 'app:down'])
})

it('the tunnel link names the teammate whose computer it is', () => {
  const [, tunnel] = previewChain({ ...base, tunnelUp: false })
  expect(tunnel!.params).toEqual({ agent: '小 C' })
})

it('asleep means the machine is down, or the machine is up and the app does not answer', () => {
  expect(appAsleep(previewChain({ ...base, tunnelUp: false, appAnswering: false }))).toBe('machine')
  expect(appAsleep(previewChain({ ...base, appAnswering: false }))).toBe('app')
  // 平台自己连不上、页面报错、应用刚重启：都不是「睡着」。
  expect(appAsleep(previewChain({ ...base, platformError: '502' }))).toBeNull()
  expect(appAsleep(previewChain({ ...base, frame: frame({ runtime: 'failed', runtimeError: 'x' }) }))).toBeNull()
  expect(appAsleep(previewChain({ ...base, frame: frame({ connection: 'gone' }) }))).toBeNull()
  expect(appAsleep(previewChain({ ...base, live: false }))).toBeNull()
})

it('a page error and a restart are told apart from a dead app', () => {
  const failed = previewChain({ ...base, frame: frame({ runtime: 'failed', runtimeError: '端口没起来' }) })
  expect(failed[2]).toMatchObject({
    state: 'down',
    reason: 'work.room.preview.chain.pageError',
    params: { error: '端口没起来' },
  })
  expect(previewChain({ ...base, frame: frame({ connection: 'gone' }) })[2]).toMatchObject({
    state: 'wait',
    reason: 'work.room.preview.chain.appRestarted',
  })
  expect(previewChain({ ...base, frame: frame({ connection: 'disconnected' }) })[2]).toMatchObject({
    state: 'wait',
    reason: 'work.room.preview.chain.appReconnecting',
  })
})
