// 输入框的麦克风：录一段、编成 WAV、交给后端听写，回来一段字。
//
// 录音直接在浏览器里取 PCM、自己编 16 kHz 单声道 WAV：各家浏览器的 MediaRecorder 给出
// 的格式不一样（webm / mp4 / ogg），统一成 WAV 后端不用转码，听写那边也认。
// 声波读的是同一路音频的音量，不等服务器就动——人一开口就知道麦克风在收。
//
// 这里只管录和发，字落到哪、怎么画，归输入框。
import { onBeforeUnmount, ref, shallowRef } from 'vue'

export type VoiceState = 'idle' | 'recording' | 'thinking'

export interface VoiceHeard {
  text: string
  raw: string
}

/** 出了什么事：麦克风没给、这段没听出来（`message` 是后端的原话）。 */
export type VoiceTrouble = { kind: 'denied' } | { kind: 'failed'; message: string }

export const VOICE_RATE = 16000
/** 声波条有几根。 */
export const VOICE_BARS = 56

/** 把几段浮点采样编成 16 位单声道 WAV。 */
export function encodeWav(chunks: Float32Array[], rate: number): Blob {
  const length = chunks.reduce((n, c) => n + c.length, 0)
  const view = new DataView(new ArrayBuffer(44 + length * 2))
  const ascii = (at: number, s: string) => [...s].forEach((ch, i) => view.setUint8(at + i, ch.charCodeAt(0)))
  ascii(0, 'RIFF')
  view.setUint32(4, 36 + length * 2, true)
  ascii(8, 'WAVE')
  ascii(12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, rate, true)
  view.setUint32(28, rate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  ascii(36, 'data')
  view.setUint32(40, length * 2, true)
  let at = 44
  for (const chunk of chunks)
    for (const sample of chunk) {
      const s = Math.max(-1, Math.min(1, sample))
      view.setInt16(at, s < 0 ? s * 0x8000 : s * 0x7fff, true)
      at += 2
    }
  return new Blob([view.buffer], { type: 'audio/wav' })
}

/** 采样率不是 16 kHz 时按比例取样降下来（说话的频段远低于 8 kHz，够用）。 */
export function resample(input: Float32Array, from: number, to: number): Float32Array {
  if (from === to) return input
  const out = new Float32Array(Math.floor((input.length * to) / from))
  const step = from / to
  for (let i = 0; i < out.length; i++) {
    const at = i * step
    const lo = Math.floor(at)
    const hi = Math.min(lo + 1, input.length - 1)
    out[i] = input[lo] + (input[hi] - input[lo]) * (at - lo)
  }
  return out
}

export function useVoiceInput(opts: {
  /** 最长录多少秒，到了自己结束。 */
  maxSeconds: number
  /** 把录好的 WAV 交给后端听写。 */
  hear: (wav: Blob) => Promise<VoiceHeard>
  /** 听写回来了。 */
  onHeard: (heard: VoiceHeard) => void
}) {
  const state = ref<VoiceState>('idle')
  const elapsed = ref(0)
  const levels = shallowRef<number[]>(new Array(VOICE_BARS).fill(0))
  const trouble = ref<VoiceTrouble | null>(null)

  let stream: MediaStream | null = null
  let context: AudioContext | null = null
  let processor: ScriptProcessorNode | null = null
  let chunks: Float32Array[] = []
  let startedAt = 0
  let timer = 0
  // 每一次录音一个号：取消、换了一次之后，晚到的回调认得出自己已经过时了。
  let take = 0

  function release() {
    window.clearInterval(timer)
    processor?.disconnect()
    processor = null
    stream?.getTracks().forEach((track) => track.stop())
    stream = null
    void context?.close().catch(() => {})
    context = null
  }

  async function start() {
    if (state.value !== 'idle') return
    trouble.value = null
    const mine = ++take
    state.value = 'recording'
    elapsed.value = 0
    levels.value = new Array(VOICE_BARS).fill(0)
    chunks = []
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      })
    } catch {
      if (mine !== take) return
      state.value = 'idle'
      trouble.value = { kind: 'denied' }
      return
    }
    // 按住说话时手指已经松开了：麦克风刚拿到就该收起。
    if (mine !== take || state.value !== 'recording') {
      release()
      return
    }
    context = new AudioContext()
    const source = context.createMediaStreamSource(stream)
    processor = context.createScriptProcessor(4096, 1, 1)
    const rate = context.sampleRate
    processor.onaudioprocess = (event) => {
      const input = event.inputBuffer.getChannelData(0)
      chunks.push(resample(new Float32Array(input), rate, VOICE_RATE))
      let sum = 0
      for (const s of input) sum += s * s
      const level = Math.min(1, Math.sqrt(sum / input.length) * 5)
      levels.value = [...levels.value.slice(1), level]
    }
    source.connect(processor)
    processor.connect(context.destination)
    startedAt = performance.now()
    timer = window.setInterval(() => {
      elapsed.value = (performance.now() - startedAt) / 1000
      if (elapsed.value >= opts.maxSeconds) void finish()
    }, 200)
  }

  async function finish() {
    if (state.value !== 'recording') return
    const mine = take
    release()
    const recorded = chunks
    chunks = []
    if (!recorded.length) {
      state.value = 'idle'
      return
    }
    state.value = 'thinking'
    try {
      const heard = await opts.hear(encodeWav(recorded, VOICE_RATE))
      if (mine !== take) return
      state.value = 'idle'
      opts.onHeard(heard)
    } catch (err) {
      if (mine !== take) return
      state.value = 'idle'
      trouble.value = { kind: 'failed', message: err instanceof Error ? err.message : '' }
    }
  }

  /** 不要了：录着的丢掉，识别中的回来也不落字。 */
  function cancel() {
    if (state.value === 'idle') return
    take++
    release()
    chunks = []
    state.value = 'idle'
  }

  onBeforeUnmount(cancel)

  return { state, elapsed, levels, trouble, start, finish, cancel }
}
