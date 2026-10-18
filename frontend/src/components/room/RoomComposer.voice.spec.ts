/** 输入框的语音输入：说一段，整理好的字落在光标处。
 *
 * 钉的是人看得到的几件事：说完的话落在开口时光标所在的地方，前后的字不动，「@名字」
 * 和手打的 @ 一样成了标签；「还原原话」换成一字不差的转写、再点换回来；录着按 Esc
 * 什么都不发；录着按 Enter 是说完而不是发送；麦克风没给、听写失败，都就地说一句，
 * 正文不动；发上去的是 16 kHz 的 WAV，长度就是说了多久。
 *
 * 麦克风和音频管线换成假的：`speak(秒)` 就是对着麦克风说了这么久。
 */
import type { Topic } from '../../cx_types'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import RoomComposer from './RoomComposer.vue'

import { setLocale } from '@/i18n'
import { placeCaret, pressInComposer } from '@/test/composerInput'

const CHEESE = { handle: 'cheese-topica', label: '芝士' }
const POOL = [
  { handle: 'alice', label: 'Alice', avatar: null, agent: false },
  { handle: CHEESE.handle, label: CHEESE.label, avatar: null, agent: true },
]
const TOPIC = {
  id: 't1',
  project_id: 'p1',
  parent_id: null,
  title: '频道',
  kind: 'channel',
  status: 'active',
} as unknown as Topic

const hearVoice = vi.fn<(wav: Blob) => Promise<{ text: string; raw: string }>>()
let processor: { onaudioprocess: ((e: unknown) => void) | null } | null = null
const getUserMedia = vi.fn()

class FakeAudioContext {
  sampleRate = 16000
  destination = {}
  createMediaStreamSource() {
    return { connect() {} }
  }
  createScriptProcessor() {
    processor = { onaudioprocess: null, connect() {}, disconnect() {} } as never
    return processor
  }
  close() {
    return Promise.resolve()
  }
}

/** 对着麦克风说了 `seconds` 秒。 */
function speak(seconds: number) {
  const samples = new Float32Array(Math.round(16000 * seconds)).fill(0.2)
  processor!.onaudioprocess!({ inputBuffer: { getChannelData: () => samples } })
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function mount(initial = '') {
  const draft = ref(initial)
  const onSend = vi.fn()
  const Wrapper = defineComponent({
    setup: () => () =>
      h(RoomComposer, {
        topic: TOPIC,
        mentionPool: POOL,
        topicList: [],
        refs: { mentionNames: {}, topicTitles: {} },
        agentSeat: CHEESE,
        agentName: CHEESE.label,
        alwaysSummon: false,
        hint: '说点什么',
        atts: [],
        attsUploading: false,
        modelValue: draft.value,
        'onUpdate:modelValue': (v: string) => (draft.value = v),
        onSend,
        hearVoice,
      }),
  })
  const utils = render(Wrapper, { global: { plugins: [createVuetify({ components, directives })] } })
  const box = () => utils.container.querySelector<HTMLElement>('.ProseMirror')!
  return { ...utils, draft, box, onSend }
}

async function startTalking() {
  const mic = screen.getByRole('button', { name: '语音输入' })
  await fireEvent.pointerDown(mic, { button: 0, pointerId: 1 })
  await fireEvent.pointerUp(mic, { button: 0, pointerId: 1 })
  await flush()
}

async function done() {
  await fireEvent.click(screen.getByRole('button', { name: '说完了（Enter）' }))
  await flush()
}

beforeEach(() => {
  setLocale('zh-CN')
  processor = null
  hearVoice.mockReset()
  getUserMedia.mockReset().mockResolvedValue({ getTracks: () => [{ stop() {} }] })
  vi.stubGlobal('AudioContext', FakeAudioContext)
  Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia }, configurable: true })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('语音输入', () => {
  it('说完的话落在光标处，前后的字不动，@名字成了标签', async () => {
    hearVoice.mockResolvedValue({ text: '@芝士 帮我看一下。', raw: '嗯艾特芝士帮我看一下' })
    const { draft, box } = mount('开头结尾')
    placeCaret(box(), 2)

    await startTalking()
    speak(1.5)
    await done()

    expect(draft.value).toBe(`开头<@${CHEESE.handle}> 帮我看一下。结尾`)
    // 发上去的是 16 kHz、16 位单声道 WAV：44 字节头加每秒 32000 字节。
    const [wav] = hearVoice.mock.calls[0]
    expect(wav.type).toBe('audio/wav')
    expect(wav.size).toBe(44 + 1.5 * 32000)
  })

  it('按住说话：松手就说完，哪怕按钮已经换成了录音条', async () => {
    hearVoice.mockResolvedValue({ text: '好的', raw: '好的' })
    const { draft } = mount('')

    const mic = screen.getByRole('button', { name: '语音输入' })
    await fireEvent.pointerDown(mic, { button: 0, pointerId: 1 })
    await new Promise((r) => setTimeout(r, 300))
    await flush()
    speak(1)
    // 松手落在页面别处：按着的那颗麦克风这时已经不在了。
    window.dispatchEvent(new PointerEvent('pointerup', { button: 0, pointerId: 1 }))
    await flush()

    expect(hearVoice).toHaveBeenCalledTimes(1)
    expect(draft.value).toBe('好的')
  })

  it('按住说话：别的手指抬起不算松手；取消之后，那次松手不会结束下一段录音', async () => {
    hearVoice.mockResolvedValue({ text: '好的', raw: '好的' })
    const { box } = mount('')

    const mic = screen.getByRole('button', { name: '语音输入' })
    await fireEvent.pointerDown(mic, { button: 0, pointerId: 1 })
    await new Promise((r) => setTimeout(r, 300))
    await flush()
    speak(1)
    window.dispatchEvent(new PointerEvent('pointerup', { button: 0, pointerId: 2 }))
    await flush()
    expect(hearVoice).not.toHaveBeenCalled()

    // 按着不放、用 Esc 取消，再开一段新的：原来那根手指抬起，不该把新的这段结束。
    expect(pressInComposer(box(), 'Escape')).toBe(true)
    await flush()
    pressInComposer(box(), 'v', { altKey: true, code: 'KeyV' })
    await flush()
    speak(1)
    window.dispatchEvent(new PointerEvent('pointerup', { button: 0, pointerId: 1 }))
    await flush()
    expect(hearVoice).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '说完了（Enter）' })).toBeTruthy()
  })

  it('「还原原话」换成一字不差的转写，再点换回来', async () => {
    hearVoice.mockResolvedValue({ text: '周四之前改好。', raw: '嗯周三不对周四之前改好' })
    const { draft } = mount('前面')

    await startTalking()
    speak(1)
    await done()
    expect(draft.value).toBe('前面周四之前改好。')

    await fireEvent.click(screen.getByRole('button', { name: /还原原话/ }))
    expect(draft.value).toBe('前面嗯周三不对周四之前改好')

    await fireEvent.click(screen.getByRole('button', { name: /用整理后的/ }))
    expect(draft.value).toBe('前面周四之前改好。')
  })

  it('录着按 Esc：什么都不发，正文不动，回到平常的那一行', async () => {
    const { draft, box } = mount('原来的字')

    await startTalking()
    speak(1)
    expect(pressInComposer(box(), 'Escape')).toBe(true)
    await flush()

    expect(hearVoice).not.toHaveBeenCalled()
    expect(draft.value).toBe('原来的字')
    expect(screen.getByRole('button', { name: '语音输入' })).toBeTruthy()
  })

  it('录着按 Enter 是说完，不是发送', async () => {
    hearVoice.mockResolvedValue({ text: '好的', raw: '好的' })
    const { draft, box, onSend } = mount('')

    await startTalking()
    speak(0.5)
    expect(pressInComposer(box(), 'Enter')).toBe(true)
    await flush()

    expect(hearVoice).toHaveBeenCalledTimes(1)
    expect(draft.value).toBe('好的')
    expect(onSend).not.toHaveBeenCalled()
  })

  it('麦克风没给：就地说一句怎么打开，什么都不发', async () => {
    getUserMedia.mockRejectedValue(new DOMException('denied', 'NotAllowedError'))
    const { draft } = mount('原来的字')

    await startTalking()

    expect(screen.getByRole('status').textContent).toContain('麦克风没打开')
    expect(hearVoice).not.toHaveBeenCalled()
    expect(draft.value).toBe('原来的字')
  })

  it('听写失败：后端的话就地说出来，正文不动', async () => {
    hearVoice.mockRejectedValue(new Error('今天的语音输入已经用完，明天再来。'))
    const { draft } = mount('原来的字')

    await startTalking()
    speak(1)
    await done()

    expect(screen.getByRole('status').textContent).toContain('今天的语音输入已经用完')
    expect(draft.value).toBe('原来的字')
  })
})
