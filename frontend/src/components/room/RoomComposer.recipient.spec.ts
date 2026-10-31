/** 输入框顶上「这条交给谁」那一行，和任务在打字时关掉之后的那一问。
 *
 * 规矩（没读过代码的人也能判断）：
 * - 发之前看得见这条交给谁：@ 了队友就写那位，拿掉 @ 就回到「频道讨论」；回复队友的消息
 *   写那位；任务负责人在开着的任务里说话写那位，只 @ 了人时写那几个人。
 * - 打字时任务关了：草稿留着，那一行跟着变；第一次发不悄悄降成讨论，就地问「@ 它」还是
 *   「仅发布讨论」。
 *
 * 交给谁由后端答（`/messages/addressing`，规矩本身由后端的测试钉着）。这里的替身按那几条
 * 规矩答，钉的是输入框问的是发出去的那一份、画的是它的回答、发的时候照它说的做。
 */
import type { Topic } from '../../cx_types'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const CHEESE = { handle: 'cheese-topica', label: '芝士' }
const AGENT_MESSAGE = 'block-from-cheese'

const backend = vi.hoisted(() => ({
  task: null as 'open' | 'closed' | null,
  privateChat: false,
  asked: [] as unknown[],
}))
vi.mock('../../api/messages', async () => {
  const actual = await vi.importActual<typeof import('../../api/messages')>('../../api/messages')
  return {
    ...actual,
    previewAddressing: vi.fn(async (_place: string, draft: { content: string; reply_to?: string }) => {
      backend.asked.push(draft)
      const named = [...draft.content.matchAll(/<@([\w-]+)>/g)].map((m) => m[1])
      const people = named.filter((h) => h !== CHEESE.handle)
      const reason = named.includes(CHEESE.handle)
        ? 'mention'
        : draft.reply_to === AGENT_MESSAGE
          ? 'reply'
          : backend.privateChat
            ? 'private'
            : backend.task === 'open' && !people.length
              ? 'task'
              : null
      return {
        agent: reason ? { handle: CHEESE.handle, name: CHEESE.label } : null,
        reason,
        people,
        place: backend.task ? 'task' : backend.privateChat ? 'private' : 'channel',
        task_status: backend.task,
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

function topic(): Topic {
  return { id: 'room-1', project_id: 'p1', title: '开发', kind: 'channel', status: 'active' } as Topic
}

function mount(
  opts: { place?: string; replyTo?: string | null; alwaysSummon?: boolean; handOff?: boolean; draft?: string } = {}
) {
  const draft = ref(opts.draft ?? '')
  const placeState = ref<string | null>(backend.task)
  const onSend = vi.fn()
  const Wrapper = defineComponent({
    setup: () => () =>
      h(RoomComposer, {
        topic: topic(),
        mentionPool: POOL,
        topicList: [],
        refs: { mentionNames: {}, topicTitles: {} },
        agentSeat: CHEESE,
        agentName: CHEESE.label,
        alwaysSummon: opts.alwaysSummon ?? false,
        handOff: opts.handOff,
        hint: '说点什么',
        atts: [],
        attsUploading: false,
        place: opts.place ?? 'room-1',
        replyTo: opts.replyTo ?? null,
        placeState: placeState.value,
        modelValue: draft.value,
        'onUpdate:modelValue': (v: string) => (draft.value = v),
        onSend,
      }),
  })
  const utils = render(Wrapper, { global: { plugins: [createVuetify({ components, directives })] } })
  const box = () => utils.container.querySelector<HTMLElement>('.ProseMirror')!
  const line = () => utils.container.querySelector('.recipient')?.textContent?.replace(/\s+/g, '') ?? ''
  const avatar = () => utils.container.querySelector<HTMLButtonElement>('.summon__btn')!
  return { ...utils, draft, placeState, onSend, box, line, avatar }
}

// 打字停下之后输入框才问后端「交给谁」（useAddressing 等 150ms），再等它答完。
const ANSWERED_MS = 200

async function settle() {
  await new Promise((r) => setTimeout(r, ANSWERED_MS))
  for (let i = 0; i < 6; i += 1) await new Promise((r) => setTimeout(r, 0))
}

async function enter(box: HTMLElement) {
  box.focus()
  await fireEvent.keyDown(box, { key: 'Enter' })
}

beforeEach(() => {
  setLocale('zh-CN')
  backend.task = null
  backend.privateChat = false
  backend.asked = []
})

describe('发之前看得见这条交给谁', () => {
  it('@ 了队友写那位，拿掉 @ 回到频道讨论', async () => {
    const { box, line, avatar } = mount()
    await fillComposer(box(), '整理一下需求')
    await settle()
    expect(line()).toBe('本条：频道讨论')

    await fireEvent.click(avatar())
    await settle()
    expect(line()).toBe('交给：芝士')

    await fireEvent.click(avatar())
    await settle()
    expect(line()).toBe('本条：频道讨论')
  })

  it('回复队友的消息：写的是那位', async () => {
    const { box, line } = mount({ replyTo: AGENT_MESSAGE })
    await fillComposer(box(), '好的')
    await settle()
    expect(line()).toBe('交给：芝士')
  })

  it('任务负责人在开着的任务里：交给队友；只 @ 了人时写那个人', async () => {
    backend.task = 'open'
    const { box, line } = mount({ place: 'task-1', alwaysSummon: true })
    await fillComposer(box(), '先做后端两个接口')
    await settle()
    expect(line()).toBe('任务进行中｜交给：芝士')

    await fillComposer(box(), '@Alice 你看下这个接口')
    await settle()
    expect(line()).toBe('任务进行中｜交给：Alice')
  })

  it('问的是发出去的那一份：@名字 已经展开成 <@handle>', async () => {
    const { box } = mount()
    await fillComposer(box(), '@Alice 明天开会')
    await settle()
    expect(backend.asked.at(-1)).toMatchObject({ content: '<@alice> 明天开会' })
  })
})

describe('发出去时照后端说的交给谁', () => {
  it('任务里 @ 了人、没 @ AI 队友的一句是说给那个人的：头像和发出去的那条都不交给队友', async () => {
    backend.task = 'open'
    const { box, avatar, onSend } = mount({ place: 'task-1', alwaysSummon: true })
    await fillComposer(box(), '@Alice 你看这个反馈')
    await settle()
    expect(avatar().getAttribute('aria-label')).toBe('这句只说给 @ 到的人')

    await enter(box())

    expect(onSend).toHaveBeenCalledWith(expect.objectContaining({ content: '<@alice> 你看这个反馈', summon: false }))
  })

  it('任务里不 @ 任何人、或 @ 了人也 @ 了队友，照样交给队友', async () => {
    backend.task = 'open'
    const { box, onSend } = mount({ place: 'task-1', alwaysSummon: true })
    await fillComposer(box(), '接着做')
    await settle()
    await enter(box())
    await fillComposer(box(), '@Alice @芝士 一起看')
    await settle()
    await enter(box())
    expect(onSend.mock.calls.map(([sent]) => sent.summon)).toEqual([true, true])
  })

  it('和 AI 队友私聊：⌘+Enter 就是发送，正文不加 @，照样交给它', async () => {
    backend.privateChat = true
    const { box, onSend } = mount({ place: 'dm-1', alwaysSummon: true, handOff: false, draft: '看一下' })
    await settle()
    box().focus()
    await fireEvent.keyDown(box(), { key: 'Enter', metaKey: true })
    expect(onSend).toHaveBeenCalledWith(expect.objectContaining({ content: '看一下', summon: true }))
  })
})

describe('打字时任务关了', () => {
  async function typedThenClosed() {
    backend.task = 'open'
    const view = mount({ place: 'task-1', alwaysSummon: true })
    await fillComposer(view.box(), '再加一个导出接口')
    await settle()
    expect(view.line()).toBe('任务进行中｜交给：芝士')
    backend.task = 'closed'
    view.placeState.value = 'closed'
    await settle()
    return view
  }

  it('那一行跟着变，草稿留着；第一次发不发出去，就地问交给谁', async () => {
    const { box, line, draft, onSend } = await typedThenClosed()
    expect(line()).toBe('任务已关闭｜本条：讨论')

    await enter(box())

    expect(onSend).not.toHaveBeenCalled()
    expect(draft.value).toBe('再加一个导出接口')
    expect(screen.getByText('任务已关闭，这条不会再自动交给芝士')).toBeTruthy()
  })

  it('选「仅发布讨论」：原话照发，不再说要交给队友', async () => {
    const { box, onSend } = await typedThenClosed()
    await enter(box())

    await fireEvent.click(screen.getByRole('button', { name: '仅发布讨论' }))

    expect(onSend).toHaveBeenCalledWith({ content: '再加一个导出接口', summon: false, expectsAgent: false })
  })

  it('选「@芝士」：发出去的那条 @ 了它', async () => {
    const { box, onSend } = await typedThenClosed()
    await enter(box())

    await fireEvent.click(screen.getByRole('button', { name: '@芝士' }))

    expect(onSend).toHaveBeenCalledTimes(1)
    expect(onSend.mock.calls[0][0].content).toBe(`<@${CHEESE.handle}> 再加一个导出接口`)
    expect(onSend.mock.calls[0][0].summon).toBe(true)
  })

  it('那一行还写着交给队友时，紧跟着一个字就按发送：发出去的仍是「我要交给队友」', async () => {
    backend.task = 'open'
    const { box, line, onSend } = mount({ place: 'task-1', alwaysSummon: true })
    await fillComposer(box(), '再加一个导出接口')
    await settle()
    backend.task = 'closed'

    await fillComposer(box(), '再加一个导出接口，带筛选')
    await enter(box())

    expect(line()).toBe('任务进行中｜交给：芝士')
    expect(onSend).toHaveBeenCalledWith({ content: '再加一个导出接口，带筛选', summon: true, expectsAgent: true })
  })

  it('一直在打字、草稿第一次问到之前任务就关了：第一次发同样先问交给谁', async () => {
    backend.task = 'open'
    const { box, line, onSend } = mount({ place: 'task-1', alwaysSummon: true })
    await settle()
    expect(line()).toBe('任务进行中｜交给：芝士')

    await fillComposer(box(), '再加一个导出接口')
    backend.task = 'closed'
    await settle()
    await enter(box())

    expect(onSend).not.toHaveBeenCalled()
    expect(screen.getByText('任务已关闭，这条不会再自动交给芝士')).toBeTruthy()
  })

  it('任务还开着时发出去，说的是「我要交给队友」：送到时不交给了，后端会退回来', async () => {
    backend.task = 'open'
    const { box, onSend } = mount({ place: 'task-1', alwaysSummon: true })
    await fillComposer(box(), '再加一个导出接口')
    await settle()

    await enter(box())

    expect(onSend).toHaveBeenCalledWith({ content: '再加一个导出接口', summon: true, expectsAgent: true })
  })
})
