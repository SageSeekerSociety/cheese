import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DesignImage from './DesignImage.vue'
import DesignRasterRegion from './DesignRasterRegion.vue'
import DesignViewportToolbar from './DesignViewportToolbar.vue'

import * as api from '@/api'
import ArtifactVersionPreview from '@/components/ArtifactVersionPreview.vue'
import FileBytesPreview from '@/components/common/FileBytesPreview.vue'
import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)
function imageFixture() {
  const image = document.createElement('img')
  Object.defineProperties(image, {
    complete: { value: true },
    naturalWidth: { value: 1000 },
    naturalHeight: { value: 500 },
  })
  image.getBoundingClientRect = () => ({ left: 10, top: 20, width: 500, height: 250 }) as DOMRect
  return image
}
it('maps a captured drag to natural pixels and cancels a drag when identity retires', async () => {
  const ui = render(DesignRasterRegion, { props: { image: imageFixture(), enabled: true, identity: 'v1' } })
  const overlay = ui.getByRole('group', { name: '选择图片区域' })
  overlay.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(overlay, { button: 0, pointerId: 7, clientX: 60, clientY: 70 })
  await fireEvent.pointerMove(overlay, { pointerId: 7, clientX: 160, clientY: 120 })
  await fireEvent.pointerUp(overlay, { pointerId: 7, clientX: 160, clientY: 120 })
  expect(ui.emitted().select![0]).toEqual([
    {
      region: { x: 100, y: 100, width: 200, height: 100 },
      identity: 'v1',
      src: '',
      naturalWidth: 1000,
      naturalHeight: 500,
    },
  ])
  await fireEvent.pointerDown(overlay, { button: 0, pointerId: 8, clientX: 60, clientY: 70 })
  await ui.rerender({ identity: 'v2' })
  await fireEvent.pointerUp(overlay, { pointerId: 8, clientX: 160, clientY: 120 })
  expect(ui.emitted().select).toHaveLength(1)
})
it('offers viewport and visual-scale actions without fetching or replacing any content', async () => {
  const ui = render(DesignViewportToolbar, { props: { device: 'desktop', scale: 0.5, fitted: true } })
  await fireEvent.update(ui.getByRole('combobox', { name: '视口' }), 'mobile')
  expect(ui.emitted().device![0]).toEqual(['mobile'])
  await fireEvent.click(ui.getByRole('button', { name: '放大内容' }))
  expect(ui.emitted().zoom![0]).toEqual([0.625])
  await fireEvent.click(ui.getByRole('button', { name: '适合视口' }))
  expect(ui.emitted().fit).toHaveLength(1)
})
it('rejects a drag when the image source changes before pointerup', async () => {
  const image = imageFixture()
  image.setAttribute('src', 'blob:first')
  const ui = render(DesignRasterRegion, { props: { image, enabled: true, identity: 'v1' } })
  const overlay = ui.getByRole('group', { name: '选择图片区域' })
  overlay.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(overlay, { button: 0, pointerId: 7, clientX: 60, clientY: 70 })
  image.setAttribute('src', 'blob:replacement')
  await fireEvent.pointerUp(overlay, { pointerId: 7, clientX: 160, clientY: 120 })
  expect(ui.emitted().select).toBeUndefined()
})

describe('Design image fit in owning previews (DOM geometry doubles)', () => {
  let observed: Map<Element, ResizeObserverCallback>
  let urls: string[]
  beforeEach(() => {
    observed = new Map()
    urls = []
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
    vi.spyOn(URL, 'createObjectURL').mockImplementation(() => {
      const url = `blob:design-review-${urls.length + 1}`
      urls.push(url)
      return url
    })
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    vi.spyOn(api, 'artifactVersionBytes').mockResolvedValue(new ArrayBuffer(8))
  })
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  async function imageGeometry(ui: ReturnType<typeof render>, paneWidth: number) {
    await waitFor(() => expect(ui.container.querySelector('.design-image__pane img')).toBeTruthy())
    const pane = ui.container.querySelector('.design-image__pane') as HTMLElement
    const image = pane.querySelector('img')!
    Object.defineProperty(pane, 'clientWidth', { configurable: true, value: paneWidth })
    Object.defineProperties(image, {
      complete: { configurable: true, value: true },
      naturalWidth: { configurable: true, value: 4096 },
      naturalHeight: { configurable: true, value: 2304 },
    })
    await waitFor(() => expect(observed.has(pane)).toBe(true))
    observed.get(pane)!([], {} as ResizeObserver)
    await fireEvent.load(image)
    return { pane, image, sheet: pane.querySelector('.design-image__sheet') as HTMLElement }
  }
  function fitsSheet(sheet: HTMLElement, paneWidth: number) {
    // Geometry doubles supply only pane width and natural image dimensions.
    // Assert the rendered sheet fits the remaining space after 16px padding per side.
    expect(Number.parseFloat(sheet.style.width)).toBeLessThanOrEqual(paneWidth - 32)
    expect(Number.parseFloat(sheet.style.width)).toBeGreaterThanOrEqual(0)
  }
  const version = {
    number: 1,
    card_id: 'card-a',
    kind: 'file',
    filename: '4k.png',
    subject: '4K image',
  } as api.ArtifactVersion

  async function selectImage(ui: ReturnType<typeof render>, image: HTMLImageElement) {
    image.getBoundingClientRect = () => ({ left: 10, top: 20, width: 512, height: 288 }) as DOMRect
    await fireEvent.click(ui.getByRole('button', { name: '选择图片区域' }))
    const overlay = ui.getByRole('group', { name: '选择图片区域' })
    overlay.setPointerCapture = vi.fn()
    await fireEvent.pointerDown(overlay, { button: 0, pointerId: 7, clientX: 60, clientY: 70 })
    await fireEvent.pointerUp(overlay, { pointerId: 7, clientX: 160, clientY: 120 })
  }
  it('undefined activeRegion retains standalone selection and disabling selection clears it', async () => {
    const ui = render(DesignImage, {
      props: { src: 'blob:standalone', alt: '4k.png', identity: 'v1', activeRegion: undefined },
    })
    const { image } = await imageGeometry(ui, 1024)
    await selectImage(ui, image)
    expect(ui.container.querySelector('.design-image__selection')).toBeTruthy()
    expect(ui.emitted().region![0]).toEqual([
      expect.objectContaining({ region: { x: 400, y: 400, width: 800, height: 400 }, identity: 'v1' }),
    ])
    await ui.rerender({ selectionEnabled: false })
    expect(ui.container.querySelector('.design-image__selection')).toBeNull()
  })
  it('controlled null waits for accepted region and clearing it never revives a local selection', async () => {
    const ui = render(DesignImage, {
      props: { src: 'blob:controlled', alt: '4k.png', identity: 'v1', activeRegion: null },
    })
    const { image } = await imageGeometry(ui, 1024)
    await selectImage(ui, image)
    expect(ui.container.querySelector('.design-image__selection')).toBeNull()
    await ui.rerender({ activeRegion: { x: 400, y: 400, width: 800, height: 400 } })
    expect(ui.container.querySelector('.design-image__selection')).toBeTruthy()
    await ui.rerender({ activeRegion: null })
    expect(ui.container.querySelector('.design-image__selection')).toBeNull()
  })

  it('direct DesignImage fits 4K at a 1024px pane (width control)', async () => {
    const ui = render(DesignImage, { props: { src: 'blob:direct', alt: '4k.png', identity: 'v1' } })
    const { sheet } = await imageGeometry(ui, 1024)
    await fireEvent.click(ui.getByRole('button', { name: '放大内容' }))
    await fireEvent.click(ui.getByRole('button', { name: '适合视口' }))
    fitsSheet(sheet, 1024)
  })
  it('FileBytesPreview fit contains a 4K image inside a 320px pane', async () => {
    const read = vi.fn().mockResolvedValue(new ArrayBuffer(8))
    const ui = render(FileBytesPreview, { props: { filename: '4k.png', source: 'snapshot-a', read } })
    const { sheet } = await imageGeometry(ui, 320)
    await fireEvent.click(ui.getByRole('button', { name: '放大内容' }))
    await fireEvent.click(ui.getByRole('button', { name: '适合视口' }))
    expect(read).toHaveBeenCalledTimes(1)
    expect(read).toHaveBeenCalledWith(false)
    fitsSheet(sheet, 320)
  })
  it('ArtifactVersionPreview fit contains a 4K image inside a 320px pane', async () => {
    const ui = render(ArtifactVersionPreview, {
      props: { projectId: 'project', artifactId: 'artifact', version, bare: true },
    })
    const { sheet } = await imageGeometry(ui, 320)
    await fireEvent.click(ui.getByRole('button', { name: '放大内容' }))
    await fireEvent.click(ui.getByRole('button', { name: '适合视口' }))
    expect(api.artifactVersionBytes).toHaveBeenCalledTimes(1)
    expect(api.artifactVersionBytes).toHaveBeenCalledWith('project', 'artifact', 'card-a', false)
    fitsSheet(sheet, 320)
  })
  it('keeps manual zoom at 10% through 300% and returns to narrow-pane fit', async () => {
    const ui = render(DesignImage, { props: { src: 'blob:manual', alt: '4k.png', identity: 'v1' } })
    const { sheet } = await imageGeometry(ui, 320)
    await fireEvent.click(ui.getByRole('button', { name: '放大内容' }))
    expect(Number.parseFloat(sheet.style.width)).toBeCloseTo(409.6)
    for (let i = 0; i < 16; i++) await fireEvent.click(ui.getByRole('button', { name: '放大内容' }))
    expect(Number.parseFloat(sheet.style.width)).toBe(12288)
    for (let i = 0; i < 16; i++) await fireEvent.click(ui.getByRole('button', { name: '缩小内容' }))
    expect(Number.parseFloat(sheet.style.width)).toBeCloseTo(409.6)
    await fireEvent.click(ui.getByRole('button', { name: '适合视口' }))
    fitsSheet(sheet, 320)
  })
  it.each([0, 16, 32])('keeps fitted dimensions finite and nonnegative at a %ipx pane', async (paneWidth) => {
    const ui = render(DesignImage, { props: { src: 'blob:tiny', alt: '4k.png', identity: 'v1' } })
    const { sheet } = await imageGeometry(ui, paneWidth)
    expect(sheet.style.width).toBe('0px')
    expect(sheet.style.height).toBe('0px')
  })
  it('FileBytesPreview source changes create a new src', async () => {
    const read = vi.fn().mockResolvedValue(new ArrayBuffer(8))
    const ui = render(FileBytesPreview, { props: { filename: '4k.png', source: 'snapshot-a', read } })
    const { image } = await imageGeometry(ui, 320)
    const before = image.getAttribute('src')
    await ui.rerender({ source: 'snapshot-b' })
    await waitFor(() => expect(urls).toHaveLength(2))
    expect(ui.container.querySelector('.design-image img')!.getAttribute('src')).not.toBe(before)
    expect(read).toHaveBeenCalledTimes(2)
  })
  it('ArtifactVersionPreview card changes create a new src', async () => {
    const ui = render(ArtifactVersionPreview, {
      props: { projectId: 'project', artifactId: 'artifact', version, bare: true },
    })
    const { image } = await imageGeometry(ui, 320)
    const before = image.getAttribute('src')
    await ui.rerender({ version: { ...version, card_id: 'card-b', number: 2 } })
    await waitFor(() => expect(urls).toHaveLength(2))
    expect(ui.container.querySelector('.design-image img')!.getAttribute('src')).not.toBe(before)
    expect(api.artifactVersionBytes).toHaveBeenLastCalledWith('project', 'artifact', 'card-b', false)
  })
})
