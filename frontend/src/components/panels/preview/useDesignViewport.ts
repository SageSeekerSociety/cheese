// 视口那一半：适配比例、缩放、空格/中键平移，以及「选中的那块区域落在屏幕哪儿」的测量。
//
// 从 DesignImage.vue 里搬出来的——那个组件贴着 file-size 闸门的边。这里只跟「图在屏幕上
// 多大、滚到哪」有关，跟画了什么无关：测量结果 `geometry` 交给说明卡（region-note 插槽）
// 定位，缩放与平移只改视图，不动笔画数据。
import type { Ref } from 'vue'
import type { RasterRegion } from './designRegion'
import type { NoteRect, RegionNoteGeometry } from './designRegionNotePosition'

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { usePinchZoom } from '@/composables/usePinchZoom'

import { ORIGINAL, sideways, displayPoint, sourcePoint } from '@/lib/imageEdit'
import type { ImageAdjust } from '@/lib/imageEdit'
import { usePreviewNarrow } from '@/lib/previewNarrow'

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
  /** 屏幕上的图转过、翻过没有：转了 90° 时「适合视口」要按转完的宽度算。 */
  adjust?: () => Pick<ImageAdjust, 'rotate' | 'flip'>
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
  /** 窄处（手机）四周只留 8px，图铺满宽度。和样式里 `.is-narrow` 的内边距是同一个数。 */
  const { narrow } = usePreviewNarrow(pane)
  const gutter = computed(() => (narrow.value ? 8 : 16))

  /** 转 90°/270° 时，屏幕上那张图的宽高和原图对调。 */
  const turned = computed(() => sideways(options.adjust?.() ?? ORIGINAL))
  /** 屏幕上看到的那张图（没缩放前）的宽高。 */
  const shown = computed(() =>
    turned.value
      ? { width: natural.value.height, height: natural.value.width }
      : { width: natural.value.width, height: natural.value.height }
  )

  const scale = computed(() => {
    if (!fitted.value) return zoom.value
    const width = shown.value.width
    if (!Number.isFinite(width) || width <= 0 || !Number.isFinite(available.value)) return 0
    return Math.min(1, Math.max(0, (available.value - 2 * gutter.value) / width))
  })

  const dimensions = computed(() =>
    natural.value.width
      ? {
          width: `${shown.value.width * scale.value}px`,
          height: `${shown.value.height * scale.value}px`,
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
  /** 缩放时钉住的那一点：原图上的哪个像素，要留在屏幕上的哪儿。 */
  let anchor: { clientX: number; clientY: number; naturalX: number; naturalY: number } | null = null
  /** 屏幕上这一点落在原图的哪个像素上。 */
  function naturalAt(clientX: number, clientY: number) {
    const current = image.value
    const bounds = current?.getBoundingClientRect()
    if (!current || !bounds?.width || !bounds?.height || !natural.value.width) return null
    const shownPoint = { x: (clientX - bounds.left) / bounds.width, y: (clientY - bounds.top) / bounds.height }
    const source = sourcePoint(shownPoint, options.adjust?.() ?? ORIGINAL)
    return { naturalX: source.x * natural.value.width, naturalY: source.y * natural.value.height }
  }
  /** 反过来：原图上的这个像素此刻在屏幕哪儿。 */
  function screenOf(naturalX: number, naturalY: number) {
    const current = image.value
    const bounds = current?.getBoundingClientRect()
    if (!bounds?.width || !bounds?.height || !natural.value.width || !natural.value.height) return null
    const shownPoint = displayPoint(
      { x: naturalX / natural.value.width, y: naturalY / natural.value.height },
      options.adjust?.() ?? ORIGINAL
    )
    return { x: bounds.left + shownPoint.x * bounds.width, y: bounds.top + shownPoint.y * bounds.height }
  }
  /** 缩放到 `value`，原图上 `at` 那一点留在屏幕上 `to` 这一处。 */
  function zoomAt(value: number, at: { naturalX: number; naturalY: number }, to: { x: number; y: number }) {
    anchor = { clientX: to.x, clientY: to.y, ...at }
    setZoom(value)
    void nextTick(applyAnchor)
  }
  /** 滚轮缩放：光标底下那个点不动，缩放才不「跑掉」。 */
  function wheel(event: WheelEvent) {
    // 不缩放时什么都不做，让滚轮去干它本来那件事：滚这段。
    if (!options.zoomOnWheel()) return
    event.preventDefault()
    const at = naturalAt(event.clientX, event.clientY)
    if (!at) return
    zoomAt(scale.value * Math.exp(-event.deltaY / 400), at, { x: event.clientX, y: event.clientY })
  }

  // ---- 两根手指捏 ----
  //
  // 捏的时候钉住的是两指刚放下时中点底下那一处：之后两指一起挪，图跟着挪（捏着就能平移）。
  // 单指在图上是框选（框选层 `touch-action: none`，按下就抓住指针），所以第二根手指落下时
  // 要把框选层手上那一下撤掉，并且这一轮的指针事件都不再给它——否则松手时它把两指之间的
  // 那块当成框出来的区域交出去。
  let pinchAt: { naturalX: number; naturalY: number } | null = null
  const touches = new Set<number>()
  const pinching = ref(false)
  function touchDown(event: PointerEvent) {
    if (event.pointerType !== 'touch') return
    touches.add(event.pointerId)
    if (touches.size < 2) return
    if (!pinching.value) {
      pinching.value = true
      const first = [...touches][0]
      const region = pane.value?.querySelector('.raster-region')
      region?.dispatchEvent(new PointerEvent('pointercancel', { pointerId: first, pointerType: 'touch' }))
    }
    event.stopPropagation()
  }
  function touchMove(event: PointerEvent) {
    if (pinching.value && touches.has(event.pointerId)) event.stopPropagation()
  }
  function touchEnd(event: PointerEvent) {
    // 第二根手指落下时我们自己派发的那个 pointercancel 不是浏览器发的事件：它要走完整个
    // 派发路径才到得了框选层上的 reset，被这里当成抬手（把第一根手指删掉、再
    // stopPropagation）拦下来的话，框选层就撤不掉手上那一下。
    if (!event.isTrusted) return
    if (!touches.delete(event.pointerId)) return
    if (pinching.value) event.stopPropagation()
    if (!touches.size) pinching.value = false
  }
  /** 这一次触摸彻底结束了：收起、失焦、指针被系统截走。`touches` 里留一根没抬起来的手指，
   *  下一根落下来就会被当成第二根，框选从此收不到指针。 */
  function resetTouches() {
    touches.clear()
    pinching.value = false
    pinchAt = null
  }
  usePinchZoom(pane, {
    zoom: () => scale.value,
    min: 0.1,
    max: 3,
    onZoom: (value, origin, phase) => {
      if (phase === 'start' || !pinchAt) pinchAt = naturalAt(origin.x, origin.y)
      if (pinchAt) zoomAt(value, pinchAt, origin)
      if (phase === 'end') pinchAt = null
    },
  })
  function applyAnchor() {
    const pending = anchor
    anchor = null
    const host = pane.value
    if (!pending || !host) return
    const at = screenOf(pending.naturalX, pending.naturalY)
    if (!at) return
    host.scrollLeft += at.x - pending.clientX
    host.scrollTop += at.y - pending.clientY
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
  /** 这一格被收起来（或整页失焦）时，把按着的空格、手上那一次平移和这一轮的手指都放下。 */
  function blur() {
    spaceHeld.value = false
    panEnd()
    resetTouches()
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
    narrow,
    pinching,
    setZoom,
    fit,
    wheel,
    touchDown,
    touchMove,
    touchEnd,
    panStart,
    panMove,
    panEnd,
    blur,
    scheduleMeasure,
  }
}
