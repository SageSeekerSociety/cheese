/** 「提醒我」：从输入区打开，填一个时间和一句话，设好的是给自己的那条提醒。
 *
 * 钉的是三件事：设下的就是人选的那个时刻和那句话；取消什么也不发；已经过去的时间
 * 发不出去。
 */
import type { Topic } from '../../cx_types'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const setReminder = vi.fn()
vi.mock('../../api/reminders', () => ({
  setReminder: (...a: unknown[]) => setReminder(...a),
}))

import RoomComposer from './RoomComposer.vue'

import { setLocale } from '@/i18n'

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.visualViewport) {
    ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = {
      width: 1024,
      height: 768,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent: () => false,
    }
  }
})

afterEach(cleanup)

beforeEach(() => {
  setLocale('zh-CN')
  setReminder.mockReset().mockResolvedValue({ id: 'r1', at: '', to: 'me' })
})

const TOPIC = { id: 'room-1', project_id: 'p1', title: '周会', kind: 'topic', status: 'active' } as Topic

function mount() {
  const draft = ref('')
  const Wrapper = defineComponent({
    setup: () => () =>
      h(RoomComposer, {
        topic: TOPIC,
        mentionPool: [],
        topicList: [],
        agentSeat: null,
        agentName: '芝士',
        alwaysSummon: false,
        hint: '',
        atts: [],
        attsUploading: false,
        modelValue: draft.value,
        'onUpdate:modelValue': (v: string) => (draft.value = v),
      }),
  })
  return render(Wrapper, { global: { plugins: [vuetify] } })
}

async function flush() {
  for (let i = 0; i < 6; i += 1) await new Promise((r) => setTimeout(r, 0))
}

/** `datetime-local` 的值：本地时间，到分钟。 */
function local(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function openDialog() {
  const utils = mount()
  await fireEvent.click(utils.getByTitle('提醒我'))
  await flush()
  const when = document.querySelector<HTMLInputElement>('[data-testid="reminder-when"] input')!
  const content = document.querySelector<HTMLTextAreaElement>('[data-testid="reminder-content"] textarea')!
  return { ...utils, when, content }
}

function submitButton(): HTMLButtonElement {
  return screen.getByRole('button', { name: '设置提醒' }) as HTMLButtonElement
}

describe('提醒我', () => {
  it('设下的是人选的那个时刻和那句话，提醒的是这个房间', async () => {
    const { when, content } = await openDialog()
    const later = new Date(Date.now() + 3 * 60 * 60 * 1000)
    later.setSeconds(0, 0)

    await fireEvent.update(when, local(later))
    await fireEvent.update(content, '  交周报  ')
    await flush()
    await fireEvent.click(submitButton())
    await flush()

    expect(setReminder).toHaveBeenCalledTimes(1)
    const [topicId, at, text] = setReminder.mock.calls[0]
    expect(topicId).toBe('room-1')
    expect((at as Date).getTime()).toBe(later.getTime())
    expect(text).toBe('交周报')
  })

  it('取消什么也不发', async () => {
    const { content } = await openDialog()
    await fireEvent.update(content, '交周报')
    await flush()

    await fireEvent.click(screen.getByRole('button', { name: '取消' }))
    await flush()

    expect(setReminder).not.toHaveBeenCalled()
  })

  it('已经过去的时间和空的内容都发不出去', async () => {
    const { when, content } = await openDialog()

    await fireEvent.update(when, local(new Date(Date.now() + 60 * 60 * 1000)))
    await flush()
    expect(submitButton().disabled, '没写内容也能设').toBe(true)

    await fireEvent.update(content, '交周报')
    await fireEvent.update(when, local(new Date(Date.now() - 60 * 60 * 1000)))
    await flush()
    expect(submitButton().disabled, '过去的时间也能设').toBe(true)
    await fireEvent.click(submitButton())
    await flush()

    expect(setReminder).not.toHaveBeenCalled()
  })
})
