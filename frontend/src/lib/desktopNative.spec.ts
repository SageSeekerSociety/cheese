import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type AppWindow = { __TAURI__?: unknown }

const listeners: Array<[string, EventListenerOrEventListenerObject]> = []
const addListener = document.addEventListener.bind(document)

beforeEach(() => {
  vi.resetModules()
  vi.stubEnv('DEV', false)
  vi.spyOn(document, 'addEventListener').mockImplementation((type, listener, options) => {
    listeners.push([type, listener])
    addListener(type, listener, options)
  })
  document.body.innerHTML = `
    <nav><a href="/projects/7" class="team">042943</a><img class="tile" src="data:," /></nav>
    <a href="/projects/8" draggable="true" class="project"><img class="project-img" src="data:," /></a>
    <input class="field" />
    <textarea class="box"></textarea>
    <div contenteditable="true" class="editable"><span class="typed">draft</span></div>
    <div class="im-text md-content"><p class="said">hello <a href="https://example.com" class="said-link">link</a></p></div>
    <pre class="block"><code class="snippet">ls</code></pre>
  `
})

afterEach(() => {
  for (const [type, listener] of listeners.splice(0)) document.removeEventListener(type, listener)
  vi.restoreAllMocks()
  vi.unstubAllEnvs()
  delete (window as AppWindow).__TAURI__
  delete document.documentElement.dataset.desktopApp
  document.head.querySelectorAll('style').forEach((s) => s.remove())
  document.body.innerHTML = ''
})

async function start(inApp: boolean) {
  if (inApp) (window as AppWindow).__TAURI__ = { core: { invoke: async () => undefined } }
  const { behaveAsDesktopApp } = await import('./desktopNative')
  behaveAsDesktopApp()
}

function rightClick(selector: string): boolean {
  const target = document.querySelector(selector)!
  const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true })
  target.dispatchEvent(event)
  return event.defaultPrevented
}

function drag(selector: string): boolean {
  const event = new Event('dragstart', { bubbles: true, cancelable: true })
  document.querySelector(selector)!.dispatchEvent(event)
  return event.defaultPrevented
}

function selection(selector: string): string {
  return getComputedStyle(document.querySelector(selector)!).userSelect
}

describe('in the desktop app', () => {
  it('keeps the browser menu off the frame', async () => {
    await start(true)
    expect(rightClick('.team')).toBe(true)
    expect(rightClick('.tile')).toBe(true)
    expect(rightClick('nav')).toBe(true)
  })

  it('keeps the system menu on what a person types or reads', async () => {
    await start(true)
    for (const selector of ['.field', '.box', '.typed', '.said', '.said-link', '.snippet']) {
      expect(rightClick(selector), selector).toBe(false)
    }
  })

  it('makes the frame unselectable and leaves content selectable', async () => {
    await start(true)
    expect(selection('body')).toBe('none')
    for (const selector of ['.field', '.box', '.editable', '.md-content', '.block']) {
      expect(selection(selector), selector).toBe('text')
    }
  })

  it('keeps links and pictures in the frame from being dragged out', async () => {
    await start(true)
    expect(drag('.team')).toBe(true)
    expect(drag('.tile')).toBe(true)
  })

  it('still drags what the page made draggable, and content', async () => {
    await start(true)
    expect(drag('.project')).toBe(false)
    expect(drag('.project-img')).toBe(false)
    expect(drag('.said-link')).toBe(false)
    expect(drag('.snippet')).toBe(false)
  })

  it('keeps the browser menu everywhere in a development build', async () => {
    vi.stubEnv('DEV', true)
    await start(true)
    expect(rightClick('.team')).toBe(false)
  })
})

describe('in a browser', () => {
  it('changes nothing', async () => {
    await start(false)
    expect(document.documentElement.dataset.desktopApp).toBeUndefined()
    expect(rightClick('.team')).toBe(false)
    expect(rightClick('.tile')).toBe(false)
    expect(drag('.team')).toBe(false)
    expect(selection('body')).not.toBe('none')
  })
})
