/** 交给过 AI 队友的支线里，发送键旁那颗头像。
 *
 * 规矩（没读过代码的人也能判断）：
 * - 支线里交给过队友之后，每句都交给它：头像一打开就睁着眼，是规则不是开关，点它不往正文里
 *   写 @；不 @ 任何人就发出去，交给的是它。
 * - 只 @ 了人、没 @ AI 队友的一句只给那几个人：头像闭眼，发出去不交给队友。
 *
 * - 输入框空着时，提示语写「回复芝士…」，说清这里的话交给谁；还没交给过任何队友的支线照旧写
 *   「在支线中回复」。
 *
 * 交给谁由后端答（`/messages/addressing` 的 `reason: 'thread'`，规矩本身由后端的测试钉着）。
 * 这里的替身按那几条规矩答，钉的是头像画的是它的回答。
 */
import type { Topic } from '../../cx_types'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const CHEESE = { handle: 'cheese-topica', label: '芝士' }

/** 这条支线交给过队友没有（后端 `thread_teammate` 空不空）。 */
const backend = vi.hoisted(() => ({ handed: true }))

vi.mock('../../api/messages', async () => {
  const actual = await vi.importActual<typeof import('../../api/messages')>('../../api/messages')
  return {
    ...actual,
    previewAddressing: vi.fn(async (_place: string, draft: { content: string }) => {
      const named = [...draft.content.matchAll(/<@([\w-]+)>/g)].map((m) => m[1])
      const people = named.filter((handle) => handle !== CHEESE.handle)
      const reason = named.includes(CHEESE.handle) ? 'mention' : people.length || !backend.handed ? null : 'thread'
      return {
        agent: reason ? { handle: CHEESE.handle, name: CHEESE.label } : null,
        reason,
        people,
        place: 'channel',
        task_status: null,
      }
    }),
  }
})

import RoomComposer from './RoomComposer.vue'

import { setLocale } from '@/i18n'
import { fillComposer } from '@/test/composerInput'

const POOL = [
  { handle: 'alice', label: 'Alice', avatar: null, agent: false },
  { handle: CHEESE.handle, label: CHEESE.label, avatar: null, agent: true },
]

function mount() {
  const draft = ref('')
  const onSend = vi.fn()
  const Wrapper = defineComponent({
    setup: () => () =>
      h(RoomComposer, {
        topic: { id: 'room-1', project_id: 'p1', title: '开发', kind: 'channel', status: 'active' } as Topic,
        mentionPool: POOL,
        topicList: [],
        refs: { mentionNames: {}, topicTitles: {} },
        agentSeat: CHEESE,
        agentName: CHEESE.label,
        alwaysSummon: false,
        hint: '在支线中回复',
        atts: [],
        attsUploading: false,
        place: 'thread-1',
        replyTo: null,
        placeState: null,
        modelValue: draft.value,
        'onUpdate:modelValue': (v: string) => (draft.value = v),
        onSend,
      }),
  })
  const utils = render(Wrapper, { global: { plugins: [createVuetify({ components, directives })] } })
  const box = () => utils.container.querySelector<HTMLElement>('.ProseMirror')!
  const avatar = () => utils.container.querySelector<HTMLButtonElement>('.summon__btn')!
  /** 输入框空着时那句提示语，读屏读到的也是它。 */
  const placeholder = () => box().getAttribute('aria-label')
  const shows = (text: string) => !!utils.queryByText(text)
  return { draft, onSend, box, avatar, placeholder, shows }
}

// 打字停下之后输入框才问后端「交给谁」（useAddressing 等 150ms），再等它答完。
async function settle() {
  await new Promise((r) => setTimeout(r, 200))
  for (let i = 0; i < 6; i += 1) await new Promise((r) => setTimeout(r, 0))
}

beforeEach(() => {
  setLocale('zh-CN')
  backend.handed = true
})

describe('交给过队友的支线里', () => {
  it('一打开头像就睁着眼，是规则：点它不往正文里写 @', async () => {
    const { draft, avatar } = mount()
    await settle()
    expect(avatar().getAttribute('aria-label')).toBe('在这里你说的话都会交给芝士')

    await fireEvent.click(avatar())
    await settle()

    expect(draft.value).toBe('')
  })

  it('不 @ 任何人就发：交给队友', async () => {
    const { box, onSend } = mount()
    await fillComposer(box(), '再查一下出处')
    await settle()
    box().focus()
    await fireEvent.keyDown(box(), { key: 'Enter' })

    expect(onSend).toHaveBeenCalledWith(expect.objectContaining({ content: '再查一下出处', summon: true }))
  })

  it('只 @ 了人：头像闭眼，发出去不交给队友', async () => {
    const { box, avatar, onSend } = mount()
    await fillComposer(box(), '@Alice 你看下这个出处')
    await settle()
    expect(avatar().getAttribute('aria-pressed')).toBe('false')

    box().focus()
    await fireEvent.keyDown(box(), { key: 'Enter' })

    expect(onSend).toHaveBeenCalledWith(expect.objectContaining({ content: '<@alice> 你看下这个出处', summon: false }))
  })
})

describe('支线输入框空着时的提示语', () => {
  it('交给过队友的支线：写「回复芝士…」', async () => {
    const { placeholder, shows } = mount()
    await settle()
    expect(shows('回复芝士…')).toBe(true)
    expect(placeholder()).toBe('回复芝士…')
  })

  it('只 @ 了人、又删干净：提示语仍是「回复芝士…」', async () => {
    const { box, shows } = mount()
    await settle()
    await fillComposer(box(), '@Alice 你看下')
    await settle()
    await fillComposer(box(), '')
    await settle()
    expect(shows('回复芝士…')).toBe(true)
  })

  it('还没交给过任何队友的支线：照旧写「在支线中回复」', async () => {
    backend.handed = false
    const { placeholder, shows } = mount()
    await settle()
    expect(shows('在支线中回复')).toBe(true)
    expect(placeholder()).toBe('在支线中回复')
  })

  it('英文界面：Reply to 芝士…', async () => {
    setLocale('en')
    const { shows } = mount()
    await settle()
    expect(shows('Reply to 芝士…')).toBe(true)
  })
})
