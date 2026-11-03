/**
 * 动图逐帧：浏览器自己放 GIF 时不停、也不能往回翻。读者要指着第 3 帧说事，就得能停下来
 * 翻到那一帧。
 *
 * 走的不是浏览器真的解码：`ImageDecoder` 只在安全上下文里有，这里给它一个替身，验的是
 * 「有解码器时摆不摆控制条、点了前后翻不翻、换了图作不作废」这条接线。真解码在
 * `useDesignGif` 里，用的就是浏览器给的帧数。
 */
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import DesignImage from './DesignImage.vue'

import { setLocale } from '@/i18n'

const FRAMES = 5

class FakeDecoder {
  static last: FakeDecoder | null = null
  tracks = {
    ready: Promise.resolve(),
    selectedTrack: { frameCount: FRAMES },
  }
  closed = false
  constructor() {
    FakeDecoder.last = this
  }
  async decode({ frameIndex }: { frameIndex: number }) {
    if (frameIndex < 0 || frameIndex >= FRAMES) throw new Error('out of range')
    return {
      image: {
        displayWidth: 40,
        displayHeight: 30,
        close: () => {},
      },
    }
  }
  close() {
    this.closed = true
  }
}

let observed: Map<Element, ResizeObserverCallback>
beforeEach(() => {
  setLocale('zh-CN')
  observed = new Map()
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(private callback: ResizeObserverCallback) {}
      observe(element: Element) {
        observed.set(element, this.callback)
      }
      disconnect() {}
    }
  )
  // 画布：jsdom 没实现 getContext，直接给它一个替身，逐帧那一步才跑得下去。
  Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', {
    configurable: true,
    writable: true,
    value: () => ({ clearRect: () => {}, drawImage: () => {} }),
  })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  FakeDecoder.last = null
})

async function painted(ui: ReturnType<typeof render>) {
  await waitFor(() => expect(ui.container.querySelector('.design-image__pane img')).toBeTruthy())
  const pane = ui.container.querySelector('.design-image__pane') as HTMLElement
  const image = pane.querySelector('img')!
  Object.defineProperty(pane, 'clientWidth', { configurable: true, value: 532 })
  Object.defineProperties(image, {
    complete: { configurable: true, value: true },
    naturalWidth: { configurable: true, value: 1000 },
    naturalHeight: { configurable: true, value: 500 },
  })
  await waitFor(() => expect(observed.has(pane)).toBe(true))
  observed.get(pane)!([], {} as ResizeObserver)
  await fireEvent.load(image)
  return { pane, image }
}

function mountGif() {
  return render(DesignImage, {
    props: { src: 'blob:gif', alt: 'loop.gif', identity: 'v1', gifBytes: new ArrayBuffer(8) },
  })
}

it('offers frame controls once the bytes decode into more than one frame', async () => {
  vi.stubGlobal('isSecureContext', true)
  vi.stubGlobal('ImageDecoder', FakeDecoder)
  const ui = mountGif()
  await painted(ui)
  await waitFor(() => expect(ui.container.querySelector('.design-image__gifbar')).toBeTruthy())
  expect(ui.container.querySelector('.design-image__gifbar')!.textContent).toContain(`1 / ${FRAMES}`)
})

it('steps forward and back through the frames and pauses on the one shown', async () => {
  vi.stubGlobal('isSecureContext', true)
  vi.stubGlobal('ImageDecoder', FakeDecoder)
  const ui = mountGif()
  await painted(ui)
  await waitFor(() => expect(ui.container.querySelector('.design-image__gifbar')).toBeTruthy())
  const buttons = () => Array.from(ui.container.querySelectorAll('.design-image__gifbar button'))
  // 三颗按钮：播放/暂停、上一帧、下一帧。
  await fireEvent.click(buttons()[2])
  await waitFor(() =>
    expect(ui.container.querySelector('.design-image__gifbar')!.textContent).toContain(`2 / ${FRAMES}`)
  )
  await fireEvent.click(buttons()[2])
  await fireEvent.click(buttons()[2])
  await waitFor(() =>
    expect(ui.container.querySelector('.design-image__gifbar')!.textContent).toContain(`4 / ${FRAMES}`)
  )
  await fireEvent.click(buttons()[1])
  await waitFor(() =>
    expect(ui.container.querySelector('.design-image__gifbar')!.textContent).toContain(`3 / ${FRAMES}`)
  )
  // 翻过帧就是暂停着看：第一颗按钮这时说的是「播放」。
  expect(buttons()[0].textContent).toContain('播放')
})

it('wraps around rather than sticking at either end', async () => {
  vi.stubGlobal('isSecureContext', true)
  vi.stubGlobal('ImageDecoder', FakeDecoder)
  const ui = mountGif()
  await painted(ui)
  await waitFor(() => expect(ui.container.querySelector('.design-image__gifbar')).toBeTruthy())
  const buttons = () => Array.from(ui.container.querySelectorAll('.design-image__gifbar button'))
  await fireEvent.click(buttons()[1])
  await waitFor(() =>
    expect(ui.container.querySelector('.design-image__gifbar')!.textContent).toContain(`${FRAMES} / ${FRAMES}`)
  )
})

it('closes the decoder when the picture changes', async () => {
  vi.stubGlobal('isSecureContext', true)
  vi.stubGlobal('ImageDecoder', FakeDecoder)
  const ui = mountGif()
  await painted(ui)
  await waitFor(() => expect(ui.container.querySelector('.design-image__gifbar')).toBeTruthy())
  const first = FakeDecoder.last
  await ui.rerender({ identity: 'v2' })
  await waitFor(() => expect(first?.closed).toBe(true))
})

it('leaves the animation to the browser when there is no decoder', async () => {
  vi.stubGlobal('isSecureContext', false)
  vi.stubGlobal('ImageDecoder', undefined)
  const ui = mountGif()
  await painted(ui)
  // 等一个微任务，确认没有异步挂上控制条。
  await new Promise((resolve) => setTimeout(resolve, 0))
  expect(ui.container.querySelector('.design-image__gifbar')).toBeNull()
})
