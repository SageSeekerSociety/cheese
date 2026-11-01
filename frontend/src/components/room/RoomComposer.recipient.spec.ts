/** 发之前看得见这条交给谁，和任务在打字时关掉之后的那一问。
 *
 * 规矩（没读过代码的人也能判断）：
 * - 频道主线上这条不交给 AI 时，头像旁写着「不交给 AI」；@ 了队友、回复队友的消息就不写。
 *   支线、任务、私聊里不写。
 * - 任务负责人在开着的任务里说话交给队友，只 @ 了人时只给那几个人。
 * - 打字时任务关了：草稿留着；第一次发不悄悄降成讨论，就地问「@ 它」还是「仅发布讨论」。
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
  /** 和一个人私聊：只有 @ 了队友才交给它。 */
  personChat: false,
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
        : backend.personChat
          ? null
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
        place: backend.task ? 'task' : backend.privateChat || backend.personChat ? 'private' : 'channel',
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

function topic(id: string): Topic {
  return { id, project_id: 'p1', title: '开发', kind: 'channel', status: 'active' } as Topic
}

function mount(
  opts: {
    /** 输入框所在的那间房（主线上发的 `place` 就是它）。 */
    room?: string
    place?: string
    replyTo?: string | null
    alwaysSummon?: boolean
    handOff?: boolean
    draft?: string
  } = {}
) {
  const draft = ref(opts.draft ?? '')
  const placeState = ref<string | null>(backend.task)
  const onSend = vi.fn()
  const Wrapper = defineComponent({
    setup: () => () =>
      h(RoomComposer, {
        topic: topic(opts.room ?? 'room-1'),
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
  const avatar = () => utils.container.querySelector<HTMLButtonElement>('.summon__btn')!
  /** 头像旁那句「不交给 AI」此刻在不在。 */
  const notToAi = () => !!screen.queryByText('不交给 AI')
  return { ...utils, draft, placeState, onSend, box, avatar, notToAi }
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
  backend.personChat = false
  backend.asked = []
})

describe('频道主线上不交给 AI 时，头像旁写出来', () => {
  it('没 @ 队友时写「不交给 AI」，@ 了就不写，拿掉 @ 又写回来', async () => {
    const { box, avatar, notToAi } = mount()
    await fillComposer(box(), '整理一下需求')
    await settle()
    expect(notToAi()).toBe(true)

    await fireEvent.click(avatar())
    await settle()
    expect(avatar().getAttribute('aria-pressed')).toBe('true')
    expect(notToAi()).toBe(false)

    await fireEvent.click(avatar())
    await settle()
    expect(notToAi()).toBe(true)
  })

  it('回复队友的消息：交给它，不写', async () => {
    const { box, avatar, notToAi } = mount({ replyTo: AGENT_MESSAGE })
    await fillComposer(box(), '好的')
    await settle()
    expect(avatar().getAttribute('aria-pressed')).toBe('true')
    expect(notToAi()).toBe(false)
  })

  it('支线里没 @ 队友：不写', async () => {
    const { box, avatar, notToAi } = mount({ place: 'thread-1' })
    await fillComposer(box(), '清单我来补')
    await settle()
    expect(avatar().getAttribute('aria-pressed')).toBe('false')
    expect(notToAi()).toBe(false)
  })

  it('任务里只 @ 了人：不写', async () => {
    backend.task = 'open'
    const { box, notToAi } = mount({ place: 'task-1', alwaysSummon: true })
    await fillComposer(box(), '@Alice 你看下这个接口')
    await settle()
    expect(notToAi()).toBe(false)
  })

  it('和一个人私聊、没 @ 队友：不写', async () => {
    backend.personChat = true
    const { box, avatar, notToAi } = mount({ room: 'dm-1', place: 'dm-1' })
    await fillComposer(box(), '周五一起过一下清单？')
    await settle()
    expect(avatar().getAttribute('aria-pressed')).toBe('false')
    expect(notToAi()).toBe(false)
  })
})

describe('发之前看得见这条交给谁', () => {
  it('任务负责人在开着的任务里：交给队友；只 @ 了人时只给那个人', async () => {
    backend.task = 'open'
    const { box, avatar } = mount({ place: 'task-1', alwaysSummon: true })
    await fillComposer(box(), '先做后端两个接口')
    await settle()
    expect(avatar().getAttribute('aria-label')).toBe('任务里你说的话都会交给芝士')

    await fillComposer(box(), '@Alice 你看下这个接口')
    await settle()
    expect(avatar().getAttribute('aria-label')).toBe('这句只说给 @ 到的人')
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
    expect(view.avatar().getAttribute('aria-label')).toBe('任务里你说的话都会交给芝士')
    backend.task = 'closed'
    view.placeState.value = 'closed'
    await settle()
    return view
  }

  it('草稿留着；第一次发不发出去，就地问交给谁', async () => {
    const { box, draft, onSend } = await typedThenClosed()

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

  it('头像还说交给队友时，紧跟着一个字就按发送：发出去的仍是「我要交给队友」', async () => {
    backend.task = 'open'
    const { box, avatar, onSend } = mount({ place: 'task-1', alwaysSummon: true })
    await fillComposer(box(), '再加一个导出接口')
    await settle()
    backend.task = 'closed'

    await fillComposer(box(), '再加一个导出接口，带筛选')
    await enter(box())

    expect(avatar().getAttribute('aria-label')).toBe('任务里你说的话都会交给芝士')
    expect(onSend).toHaveBeenCalledWith({ content: '再加一个导出接口，带筛选', summon: true, expectsAgent: true })
  })

  it('一直在打字、草稿第一次问到之前任务就关了：第一次发同样先问交给谁', async () => {
    backend.task = 'open'
    const { box, avatar, onSend } = mount({ place: 'task-1', alwaysSummon: true })
    await settle()
    expect(avatar().getAttribute('aria-label')).toBe('任务里你说的话都会交给芝士')

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
