/** 芝士面板的语音输入：和频道的输入框同一套。说完的话落进输入框，按发送才问出去；
 * 录着按 Enter 是说完而不是发送；面板没给听写就没有麦克风。
 *
 * 麦克风和音频管线换成假的：`speak(秒)` 就是对着麦克风说了这么久。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AssistantPanel from './AssistantPanel.vue'

import i18n, { setLocale } from '@/i18n'
import { composerText } from '@/test/composerInput'

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

function speak(seconds: number) {
  const samples = new Float32Array(Math.round(16000 * seconds)).fill(0.2)
  processor!.onaudioprocess!({ inputBuffer: { getChannelData: () => samples } })
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function mount(withVoice = true) {
  return render(AssistantPanel, {
    props: {
      messages: [],
      streaming: null,
      tool: null,
      notice: null,
      busy: false,
      ...(withVoice ? { hearVoice } : {}),
    },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
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

describe('芝士面板的语音输入', () => {
  it('说完的话落进输入框，录着按 Enter 是说完，再按才问出去', async () => {
    hearVoice.mockResolvedValue({ text: '帮我看一下第 3 份报告。', raw: '嗯帮我看一下第三份报告' })
    const view = mount()

    const mic = view.getByRole('button', { name: '语音输入' })
    await fireEvent.pointerDown(mic, { button: 0, pointerId: 1 })
    await fireEvent.pointerUp(mic, { button: 0, pointerId: 1 })
    await flush()
    speak(1)
    await fireEvent.keyDown(view.getByRole('textbox'), { key: 'Enter' })
    await flush()

    expect(composerText(view.container)).toBe('帮我看一下第 3 份报告。')
    expect(view.emitted('send')).toBeUndefined()

    await fireEvent.keyDown(view.getByRole('textbox'), { key: 'Enter' })
    expect(view.emitted('send')).toEqual([['帮我看一下第 3 份报告。']])
  })

  it('没给听写就没有麦克风', () => {
    const view = mount(false)
    expect(view.queryByRole('button', { name: '语音输入' })).toBeNull()
  })
})
