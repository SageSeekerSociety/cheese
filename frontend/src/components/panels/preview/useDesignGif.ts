// 动图：把每一帧摆出来，能暂停、能前后翻。
//
// 浏览器自己会把 GIF 一帧帧放着走，但它不停、也不能往回翻——一张「芝士，第 3 帧这里
// 闪一下」的报错图，读者截不到那一帧。这里用 `ImageDecoder` 自己解帧：暂停时把选中的
// 那一帧画到画布上盖住动画，翻帧就是换一张画布内容。
//
// 解码器只在安全上下文（https 或 localhost）里有；没有就老老实实说不支持，让浏览器
// 照常放它的动画，别装作能翻。
import { computed, ref, shallowRef, watch } from 'vue'

export interface GifFrames {
  /** 这一份是不是能翻帧的动图。 */
  available: boolean
  /** 一共几帧。 */
  count: number
  /** 此刻摆的是第几帧（0 起）。 */
  index: number
  /** 正在暂停着看某一帧没有；false 时交给浏览器自己放。 */
  paused: boolean
  /** 这一帧画成的画布；暂停时才有的摆。 */
  canvas: HTMLCanvasElement | null
  /** 尺寸和第一帧一致：翻帧不改变图的大小。 */
  width: number
  height: number
}

/** ImageDecoder 只在安全上下文里有，而且要 `tracks.ready` 之后才读得到帧数。 */
function supported(): boolean {
  return typeof globalThis.ImageDecoder === 'function' && globalThis.isSecureContext === true
}

export function useDesignGif(identity: () => string, bytes: () => ArrayBuffer | null) {
  const reset = () => ({
    available: false,
    count: 0,
    index: 0,
    paused: false,
    canvas: null,
    width: 0,
    height: 0,
  })
  const state = ref<GifFrames>(reset())
  const decoder = shallowRef<{ decode: (o: { frameIndex: number }) => Promise<unknown>; close: () => void } | null>(
    null
  )
  const canvas = ref<HTMLCanvasElement | null>(null)

  async function draw(index: number) {
    const current = decoder.value
    const target = canvas.value
    if (!current || !target) return
    const frame = (await current.decode({ frameIndex: index })) as {
      image: { displayWidth: number; displayHeight: number; close: () => void }
    }
    target.width = frame.image.displayWidth
    target.height = frame.image.displayHeight
    const context = target.getContext('2d')
    if (context) {
      context.clearRect(0, 0, target.width, target.height)
      context.drawImage(frame.image as unknown as CanvasImageSource, 0, 0)
    }
    state.value = { ...state.value, width: target.width, height: target.height }
    frame.image.close?.()
  }

  async function load() {
    const data = bytes()
    if (!data || !supported()) return
    try {
      const created = new (globalThis.ImageDecoder as new (init: { data: ArrayBuffer; type: string }) => {
        tracks: { ready: Promise<void>; selectedTrack: { frameCount: number } | null }
        decode: (o: { frameIndex: number }) => Promise<unknown>
        close: () => void
      })({ data, type: 'image/gif' })
      await created.tracks.ready
      const track = created.tracks.selectedTrack
      if (!track || track.frameCount <= 1) {
        created.close()
        return
      }
      decoder.value = created
      state.value = { ...state.value, available: true, count: track.frameCount }
    } catch {
      // 解不动这一份（损坏、或者不是真的 GIF）：交回给浏览器，能放就放。
      decoder.value = null
    }
  }

  /** 暂停在某一帧上看。 */
  async function pause() {
    if (!state.value.available) return
    state.value = { ...state.value, paused: true }
    await draw(state.value.index)
  }
  /** 交回给浏览器自己放。 */
  function play() {
    state.value = { ...state.value, paused: false }
  }
  async function step(delta: number) {
    if (!state.value.available) return
    const count = state.value.count
    const index = (state.value.index + delta + count) % count
    state.value = { ...state.value, index, paused: true }
    await draw(index)
  }

  function dispose() {
    decoder.value?.close()
    decoder.value = null
    state.value = reset()
  }

  // 换了图（或换了版本）：上一张的解码器和那一帧都作废。
  watch(
    [identity, bytes],
    () => {
      dispose()
      void load()
    },
    { immediate: true }
  )

  const index = computed(() => state.value.index)
  return { gif: state, gifCanvas: canvas, pause, play, step, index, dispose }
}
