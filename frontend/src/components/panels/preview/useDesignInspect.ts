// 面板里两件「读这张图」的小工具：取色和量距离。
//
// 它们都不改图、也不发东西出去，只是把屏上这一点是什么颜色、两点之间有多少像素读给读者——
// 设计师和前端拿着一张截图问芝士时，问的往往正是这两样。两样都用**原图像素**记：屏幕上
// 缩放过、转过，读出来的仍是文件里的那个数。
import type { Point } from './designRegion'

import { computed, ref, watch } from 'vue'

export type InspectMode = 'off' | 'pick' | 'measure'

/** 取色读出来的那一点：原图坐标 + sRGB 十六进制。 */
export interface ColorReading {
  at: Point
  hex: string
  rgb: [number, number, number]
}

/** 量出来的一条：两端原图坐标 + 长度（原图像素）。 */
export interface MeasureReading {
  from: Point
  to: Point
  length: number
}

export function useDesignInspect(image: () => HTMLImageElement | null) {
  const mode = ref<InspectMode>('off')
  const color = ref<ColorReading | null>(null)
  const measure = ref<MeasureReading | null>(null)

  /** 取色用的小画布：一次建好，读数时重画 1×1。 */
  let scratch: HTMLCanvasElement | null = null

  /**
   * 读原图上这一点的颜色。
   *
   * 画布是逐像素读的唯一一条路；把整张图重画一遍再读一格，大图上太贵——所以画布只开
   * 1×1，把原图那一处缩放着画进去。`imageSmoothingEnabled` 关掉，读到的就是那一个
   * 像素本身，不是它和邻居混出来的。
   */
  function readColor(at: Point): ColorReading | null {
    const source = image()
    if (!source?.naturalWidth || !source.naturalHeight) return null
    const x = Math.round(at.x)
    const y = Math.round(at.y)
    if (x < 0 || y < 0 || x >= source.naturalWidth || y >= source.naturalHeight) return null
    if (!scratch) scratch = document.createElement('canvas')
    scratch.width = 1
    scratch.height = 1
    const context = scratch.getContext('2d')
    if (!context) return null
    context.imageSmoothingEnabled = false
    try {
      context.drawImage(source, x, y, 1, 1, 0, 0, 1, 1)
      const [r, g, b] = context.getImageData(0, 0, 1, 1).data
      const hex = `#${[r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('')}`
      return { at: { x, y }, hex, rgb: [r, g, b] }
    } catch {
      // 图是跨域的：画布被污染，`getImageData` 抛。预览里的图是同一份字节转的，走不到
      // 这里，但真走到了就老实说读不出来，别装作读了个黑色。
      return null
    }
  }

  function pick(at: Point) {
    color.value = readColor(at)
  }
  /** 拖量距离时先落一头，抬起时落另一头。 */
  function startMeasure(at: Point) {
    measure.value = { from: at, to: at, length: 0 }
  }
  function dragMeasure(at: Point) {
    const current = measure.value
    if (!current) return
    measure.value = { ...current, to: at, length: Math.hypot(at.x - current.from.x, at.y - current.from.y) }
  }
  function clear() {
    color.value = null
    measure.value = null
  }
  function setMode(next: InspectMode) {
    mode.value = mode.value === next ? 'off' : next
    clear()
  }

  // 换了一张图（或换了一版）：上一个数是从上一张上读的，原图坐标也是它的，留着就是错的。
  // 换图时 `<img>` 会整个换掉，这里跟着它清。
  watch(image, clear)

  const active = computed(() => mode.value !== 'off')

  return { mode, active, color, measure, pick, startMeasure, dragMeasure, clear, setMode }
}
