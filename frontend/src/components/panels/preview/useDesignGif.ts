// 动图：把每一帧摆出来，能暂停、能前后翻。
//
// 浏览器自己会把 GIF 一帧帧放着走，但它不停、也不能往回翻——一张「芝士，第 3 帧这里
// 闪一下」的报错图，读者截不到那一帧。这里用 `ImageDecoder` 自己解帧：暂停时把选中的
// 那一帧画到画布上盖住动画，翻帧就是换一张画布内容。
//
// 解码器只在安全上下文（https 或 localhost）里有；没有就老老实实说不支持，让浏览器
// 照常放它的动画，别装作能翻。
import { computed, onScopeDispose, ref, shallowRef, watch } from 'vue'

export interface GifFrames {
  /** 这一份是不是能翻帧的动图。 */
  available: boolean
  /** 一共几帧。 */
  count: number
  /** 此刻摆的是第几帧（0 起）。 */
  index: number
  /** 正在暂停着看某一帧没有；false 时交给浏览器自己放。 */
  paused: boolean
  /** 尺寸和第一帧一致：翻帧不改变图的大小。 */
  width: number
  height: number
}

/** 解出来的一帧：`image` 是一张要自己关掉的位图（`ImageBitmap` 或 `VideoFrame`）。 */
interface DecodedFrame {
  image: { displayWidth: number; displayHeight: number; close?: () => void }
}

/** WebCodecs 的 `ImageDecoder`：这里只用到解一帧、读帧数、关掉。 */
interface GifDecoder {
  tracks: { ready: Promise<void>; selectedTrack: { frameCount: number } | null }
  decode: (options: { frameIndex: number }) => Promise<DecodedFrame>
  close: () => void
}

/** ImageDecoder 只在安全上下文里有，而且要 `tracks.ready` 之后才读得到帧数。 */
function supported(): boolean {
  return typeof globalThis.ImageDecoder === 'function' && globalThis.isSecureContext === true
}

export function useDesignGif(identity: () => string, bytes: () => ArrayBuffer | null) {
  const reset = (): GifFrames => ({
    available: false,
    count: 0,
    index: 0,
    paused: false,
    width: 0,
    height: 0,
  })
  const state = ref<GifFrames>(reset())
  const decoder = shallowRef<GifDecoder | null>(null)
  const canvas = ref<HTMLCanvasElement | null>(null)

  async function draw(index: number) {
    const current = decoder.value
    const target = canvas.value
    if (!current || !target) return
    let frame: DecodedFrame | null = null
    try {
      frame = await current.decode({ frameIndex: index })
      target.width = frame.image.displayWidth
      target.height = frame.image.displayHeight
      const context = target.getContext('2d')
      if (context) {
        context.clearRect(0, 0, target.width, target.height)
        context.drawImage(frame.image as unknown as CanvasImageSource, 0, 0)
      }
      state.value = { ...state.value, width: target.width, height: target.height }
    } catch {
      // 解码器在解到一半时被关掉（换图、卸载），或者这一帧本来就解不动：画布留着上一帧，
      // 不装作翻了。`frame` 已经拿到的话仍然要关，不然那张位图一直挂着。
    } finally {
      frame?.image.close?.()
    }
  }

  /**
   * 每一次装载有一个号：换了图之后，上一轮还没 `await` 完的那次装载回来时号已经不对，
   * 它手里那个解码器就得自己关掉走人——不认号的话它会盖掉新一轮的 `decoder.value`，
   * 把新的那一个漏掉，帧数也短暂显示成旧的。
   */
  let generation = 0

  async function load() {
    const data = bytes()
    if (!data || !supported()) return
    const mine = ++generation
    let created: GifDecoder | null = null
    try {
      created = new (globalThis.ImageDecoder as unknown as new (init: {
        data: ArrayBuffer
        type: string
      }) => GifDecoder)({
        data,
        type: 'image/gif',
      })
      await created.tracks.ready
      if (mine !== generation) {
        created.close()
        return
      }
      const track = created.tracks.selectedTrack
      if (!track || track.frameCount <= 1) {
        created.close()
        return
      }
      decoder.value = created
      state.value = { ...state.value, available: true, count: track.frameCount }
    } catch {
      // 解不动这一份（损坏、或者不是真的 GIF）：交回给浏览器，能放就放。
      created?.close()
      if (mine === generation) decoder.value = null
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
    generation += 1
    decoder.value?.close()
    decoder.value = null
    state.value = reset()
  }

  // 这一格收起来的时候自己收摊：调用方忘了管，解码器和解出来的那几帧就一直挂着。
  onScopeDispose(dispose)

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
