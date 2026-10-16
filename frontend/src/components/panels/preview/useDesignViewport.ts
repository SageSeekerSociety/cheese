// 视口那一半：适配比例、缩放、空格/中键平移，以及「选中的那块区域落在屏幕哪儿」的测量。
//
// 从 DesignImage.vue 里搬出来的——那个组件贴着 file-size 闸门的边。这里只跟「图在屏幕上
// 多大、滚到哪」有关，跟画了什么无关：测量结果 `geometry` 交给说明卡（region-note 插槽）
// 定位，缩放与平移只改视图，不动笔画数据。
import type { Ref } from 'vue'
import type { RasterRegion } from './designRegion'
import type { NoteRect, RegionNoteGeometry } from './designRegionNotePosition'

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

export interface DesignViewportOptions {
  /** 滚轮是不是用来缩放的（见 DesignImage 的 zoomOnWheel）。 */
  zoomOnWheel: () => boolean
  natural: Ref<{ width: number; height: number }>
  image: Ref<HTMLImageElement | null>
  /** 框出区域的那只盒子：测量拿它的边界。 */
  selectedBox: Ref<HTMLElement | null>
  /** 此刻选中的区域；没有就没得测。 */
  selectedRegion: Ref<RasterRegion | null>
  /** 按下的那一瞬：手上可能还挑着一笔，`.design-image__viewport` 的 pointerdown 先记焦点。 */
  beforePanStart: () => void
}

export function useDesignViewport(options: DesignViewportOptions) {
  const { natural, image, selectedBox, selectedRegion } = options
  /** 外面那一层：浮层的坐标以它为原点。 */
  const viewport = ref<HTMLElement | null>(null)
  /** 里面那一层：真的滚的是它，clientWidth 决定「适合视口」的比例。 */
  const pane = ref<HTMLElement | null>(null)
  const available = ref(0)
  const zoom = ref(1)
  const fitted = ref(true)
  const geometry = ref<RegionNoteGeometry | null>(null)
  const spaceHeld = ref(false)
  const panning = ref(false)

  const scale = computed(() => {
    if (!fitted.value) return zoom.value
    const width = natural.value.width
    if (!Number.isFinite(width) || width <= 0 || !Number.isFinite(available.value)) return 0
    return Math.min(1, Math.max(0, (available.value - 32) / width))
  })

  const dimensions = computed(() =>
    natural.value.width
      ? {
          width: `${natural.value.width * scale.value}px`,
          height: `${natural.value.height * scale.value}px`,
        }
      : {}
  )

  let observer: ResizeObserver | null = null
  let frame = 0
  function clippingAncestors(element: HTMLElement) {
    const clips: { element: HTMLElement; horizontal: boolean; vertical: boolean }[] = []
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      const style = getComputedStyle(parent)
      const horizontal = /^(auto|scroll|hidden|clip)$/.test(style.overflowX)
      const vertical = /^(auto|scroll|hidden|clip)$/.test(style.overflowY)
      if (horizontal || vertical) clips.push({ element: parent, horizontal, vertical })
    }
    return clips
  }
  function measure() {
    frame = 0
    const host = viewport.value
    const element = pane.value
    const selected = selectedBox.value
    if (!host || !element || !selected || !selectedRegion.value) {
      geometry.value = null
      return
    }
    const origin = host.getBoundingClientRect()
    const bounds = element.getBoundingClientRect()
    const visual = window.visualViewport
    let left = Math.max(bounds.left + element.clientLeft, visual?.offsetLeft ?? 0)
    let top = Math.max(bounds.top + element.clientTop, visual?.offsetTop ?? 0)
    let right = Math.min(
      bounds.left + element.clientLeft + element.clientWidth,
      (visual?.offsetLeft ?? 0) + (visual?.width ?? window.innerWidth)
    )
    let bottom = Math.min(
      bounds.top + element.clientTop + element.clientHeight,
      (visual?.offsetTop ?? 0) + (visual?.height ?? window.innerHeight)
    )
    for (const clip of clippingAncestors(element)) {
      const box = clip.element.getBoundingClientRect()
      if (clip.horizontal) {
        left = Math.max(left, box.left + clip.element.clientLeft)
        right = Math.min(right, box.left + clip.element.clientLeft + clip.element.clientWidth)
      }
      if (clip.vertical) {
        top = Math.max(top, box.top + clip.element.clientTop)
        bottom = Math.min(bottom, box.top + clip.element.clientTop + clip.element.clientHeight)
      }
    }
    const region = selected.getBoundingClientRect()
    const regionLeft = Math.max(region.left, left)
    const regionTop = Math.max(region.top, top)
    const regionRight = Math.min(region.right, right)
    const regionBottom = Math.min(region.bottom, bottom)
    const local = (x: number, y: number, width: number, height: number): NoteRect => ({
      left: x - origin.left,
      top: y - origin.top,
      width,
      height,
    })
    geometry.value = {
      viewport: local(left, top, Math.max(0, right - left), Math.max(0, bottom - top)),
      region:
        regionRight > regionLeft && regionBottom > regionTop
          ? local(regionLeft, regionTop, regionRight - regionLeft, regionBottom - regionTop)
          : null,
    }
  }
  function scheduleMeasure() {
    if (!frame) frame = requestAnimationFrame(measure)
  }
  function setZoom(value: number) {
    zoom.value = Math.max(0.1, Math.min(3, value))
    fitted.value = false
  }
  function fit() {
    fitted.value = true
  }
  /** 滚轮缩放：光标底下那个点不动，缩放才不「跑掉」。 */
  let anchor: { clientX: number; clientY: number; naturalX: number; naturalY: number } | null = null
  function wheel(event: WheelEvent) {
    // 不缩放时什么都不做，让滚轮去干它本来那件事：滚这段。
    if (!options.zoomOnWheel()) return
    event.preventDefault()
    const current = image.value
    const bounds = current?.getBoundingClientRect()
    if (!current || !bounds?.width || !natural.value.width) return
    const before = bounds.width / natural.value.width
    anchor = {
      clientX: event.clientX,
      clientY: event.clientY,
      naturalX: (event.clientX - bounds.left) / before,
      naturalY: (event.clientY - bounds.top) / before,
    }
    setZoom(scale.value * Math.exp(-event.deltaY / 400))
    void nextTick(applyAnchor)
  }
  function applyAnchor() {
    const pending = anchor
    anchor = null
    const host = pane.value
    const current = image.value
    if (!pending || !host || !current || !natural.value.width) return
    const bounds = current.getBoundingClientRect()
    const after = bounds.width / natural.value.width
    if (!after) return
    host.scrollLeft += bounds.left + pending.naturalX * after - pending.clientX
    host.scrollTop += bounds.top + pending.naturalY * after - pending.clientY
  }
  /** 空格或中键按住拖动＝平移，和画布类应用一套手感。 */
  let pan: { x: number; y: number; left: number; top: number } | null = null
  function panStart(event: PointerEvent) {
    options.beforePanStart()
    const host = pane.value
    if (!host) return
    if (!spaceHeld.value && event.button !== 1) return
    event.preventDefault()
    event.stopPropagation()
    panning.value = true
    pan = { x: event.clientX, y: event.clientY, left: host.scrollLeft, top: host.scrollTop }
    ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
  }
  function panMove(event: PointerEvent) {
    const host = pane.value
    if (!host || !pan) return
    host.scrollLeft = pan.left - (event.clientX - pan.x)
    host.scrollTop = pan.top - (event.clientY - pan.y)
  }
  function panEnd() {
    pan = null
    panning.value = false
  }
  /** 这一格被收起来（或整页失焦）时，把按着的空格和手上那一次平移放下。 */
  function blur() {
    spaceHeld.value = false
    panEnd()
  }

  watch(
    pane,
    (element) => {
      observer?.disconnect()
      if (!element) return
      observer = new ResizeObserver(() => {
        available.value = element.clientWidth
        scheduleMeasure()
      })
      observer.observe(element)
      for (const clip of clippingAncestors(element)) observer.observe(clip.element)
    },
    { flush: 'post' }
  )

  onMounted(() => {
    document.addEventListener('scroll', scheduleMeasure, true)
    window.addEventListener('resize', scheduleMeasure)
    window.visualViewport?.addEventListener('resize', scheduleMeasure)
    window.visualViewport?.addEventListener('scroll', scheduleMeasure)
    window.addEventListener('blur', blur)
  })
  onBeforeUnmount(() => {
    observer?.disconnect()
    if (frame) cancelAnimationFrame(frame)
    document.removeEventListener('scroll', scheduleMeasure, true)
    window.removeEventListener('resize', scheduleMeasure)
    window.visualViewport?.removeEventListener('resize', scheduleMeasure)
    window.visualViewport?.removeEventListener('scroll', scheduleMeasure)
    window.removeEventListener('blur', blur)
  })

  return {
    viewport,
    pane,
    available,
    scale,
    zoom,
    fitted,
    dimensions,
    geometry,
    spaceHeld,
    panning,
    setZoom,
    fit,
    wheel,
    panStart,
    panMove,
    panEnd,
    scheduleMeasure,
  }
}
