// What a reader sees of a mini-app block while it starts, and when it does not:
// it says it is loading until the app has drawn; an app that cannot be loaded
// says so and can be tried again; an app that starts and never draws is the
// app's own failure, which its writer can be asked to fix.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { answerDocAppsWith, type DocAppSource, type DocAppVersion } from '../../../../lib/docApps'

import { appBlock } from './appView'

import { t } from '@/i18n'

const say = (key: string, params: Record<string, unknown> = {}) => t(`work.room.doc.blocks.app.${key}`, params)

const VERSION: DocAppVersion = {
  key: 'z8staby2',
  version: 1,
  note: '',
  author: 'cheese',
  requested_by: null,
  checked: false,
  created_at: '2026-10-14T10:08:26Z',
  code: '<template><p>x</p></template>',
  shell: 1,
  shell_url: 'about:blank',
  heights: {},
  shots: [],
}

function serve(version: DocAppSource['version']) {
  answerDocAppsWith({
    app: async () => ({ key: VERSION.key, document_id: 'd', document_title: null, versions: [VERSION] }),
    version,
    shot: async () => '',
  })
}

const stops: (() => void)[] = []
function open(agent = vi.fn()) {
  const block = appBlock({ app: VERSION.key, version: 1, title: '单摆' }, { agent: { name: '芝士', run: agent } })
  document.body.append(block.dom)
  stops.push(block.start())
  return block.dom
}

/** The frame says something, as the shell inside it would. */
function frameSays(dom: HTMLElement, message: object) {
  const frame = dom.querySelector('iframe')!
  window.dispatchEvent(
    new MessageEvent('message', { data: { ch: 'cheese-app', ...message }, source: frame.contentWindow })
  )
}

const button = (dom: HTMLElement, name: string) =>
  [...dom.querySelectorAll('button')].find((b) => b.textContent === name) ?? null

beforeEach(() => {
  vi.useFakeTimers()
  // happy-dom lays nothing out and has no scrollBy; the block keeps the
  // reader's place with it as the app grows.
  window.scrollBy = vi.fn()
})
afterEach(() => {
  for (const stop of stops.splice(0)) stop()
  document.body.replaceChildren()
  vi.useRealTimers()
})

describe('a mini-app block while it starts', () => {
  it('says it is loading until the app has drawn', async () => {
    serve(async () => VERSION)
    const dom = open()
    await vi.advanceTimersByTimeAsync(0)
    expect(dom.textContent).toContain(say('loading'))
    frameSays(dom, { type: 'ready' })
    expect(dom.textContent).toContain(say('loading'))
    frameSays(dom, { type: 'height', h: 240 })
    expect(dom.textContent).not.toContain(say('loading'))
  })

  it('says an app that cannot be loaded did not load, and tries again when asked', async () => {
    serve(async () => VERSION)
    const dom = open()
    await vi.advanceTimersByTimeAsync(30_000)
    expect(dom.textContent).toContain(say('loadFailed'))
    expect(button(dom, say('fix', { name: '芝士' }))).toBeNull()
    button(dom, say('retry'))!.click()
    await vi.advanceTimersByTimeAsync(0)
    frameSays(dom, { type: 'ready' })
    frameSays(dom, { type: 'height', h: 240 })
    expect(dom.textContent).not.toContain(say('loadFailed'))
    expect(dom.querySelector('iframe')).not.toBeNull()
  })

  it('reports an app that starts and never draws as its own failure, for its writer to fix', async () => {
    serve(async () => VERSION)
    const agent = vi.fn()
    const dom = open(agent)
    await vi.advanceTimersByTimeAsync(0)
    frameSays(dom, { type: 'ready' })
    await vi.advanceTimersByTimeAsync(30_000)
    expect(dom.textContent).toContain(say('failedTitle'))
    button(dom, say('fix', { name: '芝士' }))!.click()
    expect(agent).toHaveBeenCalledOnce()
  })

  it('tries reading the code again when reading it failed', async () => {
    let calls = 0
    serve(async () => {
      if (++calls === 1) throw Object.assign(new Error('bad gateway'), { status: 502 })
      return VERSION
    })
    const dom = open()
    await vi.advanceTimersByTimeAsync(0)
    expect(dom.textContent).toContain(say('loadFailed'))
    button(dom, say('retry'))!.click()
    await vi.advanceTimersByTimeAsync(0)
    expect(dom.querySelector('iframe')).not.toBeNull()
  })
})
