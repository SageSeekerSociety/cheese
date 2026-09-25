/** When the GPU drops the scene's context — a driver reset, a laptop waking up —
 *  the pane falls back to its plain ground instead of keeping a dead canvas,
 *  and the scene comes back once if it can. */
import { ref } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import BrandScene from './BrandScene.vue'

vi.mock('@/theme', () => ({ useAppTheme: () => ({ isDark: ref(false) }) }))

// A stand-in for the shader library: mounting draws one canvas into the host.
vi.mock('@paper-design/shaders', () => ({
  ShaderMount: class {
    constructor(host: HTMLElement) {
      host.appendChild(document.createElement('canvas'))
    }
    setUniforms() {}
    dispose() {}
  },
}))

vi.mock('./scenes', () => ({
  sceneForThisPage: () => 'still',
  SCENES: {
    still: async () => ({
      fragment: '',
      uniforms: {},
      speed: 0,
      mipmaps: [],
      drift: false,
      pointer: () => ({}),
    }),
  },
}))

async function settle() {
  await vi.dynamicImportSettled()
  for (let i = 0; i < 10; i++) await Promise.resolve()
}

function open() {
  const view = render(BrandScene)
  const host = view.container.firstElementChild as HTMLElement
  return { host, shown: () => host.classList.contains('brand-scene--shown'), close: view.unmount }
}

function loseContext(host: HTMLElement) {
  host.querySelector('canvas')!.dispatchEvent(new Event('webglcontextlost'))
}

beforeEach(() => vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] }))
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('the brand scene when its GPU context is lost', () => {
  it('shows the plain ground instead of the dead canvas', async () => {
    const { host, shown } = open()
    await settle()
    expect(host.querySelector('canvas')).not.toBeNull()
    expect(shown()).toBe(true)

    loseContext(host)
    await settle()

    expect(host.querySelector('canvas')).toBeNull()
    expect(shown()).toBe(false)
  })

  it('draws the scene again a moment later', async () => {
    const { host, shown } = open()
    await settle()

    loseContext(host)
    vi.advanceTimersByTime(1000)
    await settle()

    expect(host.querySelector('canvas')).not.toBeNull()
    expect(shown()).toBe(true)
  })

  it('stays plain when the context is lost again', async () => {
    const { host, shown } = open()
    await settle()

    loseContext(host)
    vi.advanceTimersByTime(1000)
    await settle()
    loseContext(host)
    vi.advanceTimersByTime(60_000)
    await settle()

    expect(host.querySelector('canvas')).toBeNull()
    expect(shown()).toBe(false)
  })

  it('does not come back after the page has closed', async () => {
    const { host, close } = open()
    await settle()

    loseContext(host)
    close()
    vi.advanceTimersByTime(1000)
    await settle()

    expect(host.querySelector('canvas')).toBeNull()
  })
})
