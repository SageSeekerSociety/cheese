/** 改一条消息时，框里的 `@` 和发消息时一样有候选。
 *
 * 这一份钉的是一条产品规则：改一条消息和发一条消息，对「@ 到了谁」是同一件事。
 * 编辑时不弹候选，人就得自己拼 handle——拼错了这条消息看起来点到了人，其实点到
 * 的是一个不存在的名字，而被点的人什么都没收到。
 *
 * 所以这里问的是行为，不是长相：打一个 `@`，名册上那个人在不在候选里；从候选里
 * 挑中他，框里写进去的是不是他的名字；框里已经有一个 `@` 时，回车是挑人还是保存。
 */
import type { MentionPoolEntry } from '@/composables/useRoomMentionPicker'

import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// 这个框不该认识资料库。这里把它钉成一个间谍：真有人往后给它接上 `listLibrary`，
// 这一行会红，而不是让那次取数悄悄发生。
vi.mock('../../lib/libraryApi', async () => {
  const actual = await vi.importActual<typeof import('../../lib/libraryApi')>('../../lib/libraryApi')
  return { ...actual, searchLibraryFiles: vi.fn().mockResolvedValue([]) }
})

import { searchLibraryFiles } from '../../lib/libraryApi'

import MessageEditor from './MessageEditor.vue'

import { setLocale } from '@/i18n'

const POOL: MentionPoolEntry[] = [
  { handle: 'alice', label: 'Alice', avatar: null, agent: false },
  { handle: 'cheese-topica', label: '芝士', avatar: null, agent: true },
]

interface Options {
  text?: string
  saving?: boolean
}

function mount(opts: Options = {}) {
  const onSave = vi.fn()
  const onCancel = vi.fn()

  const Wrapper = defineComponent({
    setup: () => () =>
      h(MessageEditor, {
        text: opts.text ?? '周五交初稿',
        saving: opts.saving ?? false,
        mentionPool: POOL,
        topicList: [],
        onSave,
        onCancel,
      }),
  })

  const utils = render(Wrapper, { global: { plugins: [createVuetify({ components, directives })] } })
  const box = () => utils.container.querySelector('textarea')!
  return { ...utils, box, onSave, onCancel }
}

function menuLabels(container: Element): string[] {
  return Array.from(container.querySelectorAll<HTMLElement>('.mention-menu-item')).map(
    (el) => el.querySelector('.mention-menu-name')?.textContent?.trim() ?? ''
  )
}

function named(container: Element, label: string): HTMLElement {
  const item = Array.from(container.querySelectorAll<HTMLElement>('.mention-menu-item')).find(
    (el) => el.querySelector('.mention-menu-name')?.textContent?.trim() === label
  )
  if (!item) throw new Error(`候选里没有 ${label}`)
  return item
}

async function flush() {
  for (let i = 0; i < 6; i += 1) await new Promise((r) => setTimeout(r, 0))
}

async function type(box: HTMLTextAreaElement, value: string) {
  await fireEvent.update(box, value)
  await flush()
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.mocked(searchLibraryFiles).mockClear()
})

describe('编辑一条消息时的 @ 候选', () => {
  it('打一个 @ 就列出这个房间里的人', async () => {
    const { box, container } = mount()
    await type(box(), '周五交初稿 @')

    expect(menuLabels(container)).toContain('芝士')
    expect(menuLabels(container)).toContain('Alice')
  })

  it('候选里挑中一个人，写进框里的是他的名字', async () => {
    const { box, container } = mount()
    await type(box(), '看一下 @')

    await fireEvent.click(named(container, '芝士'))
    await flush()

    expect(box().value).toBe('看一下 @芝士 ')
  })

  it('有候选时回车是挑人，不是保存', async () => {
    const { box, onSave } = mount()
    await type(box(), '看一下 @')

    const boxEl = box()
    boxEl.focus()
    await fireEvent.keyDown(boxEl, { key: 'Enter' })
    await flush()

    expect(onSave).not.toHaveBeenCalled()
    expect(box().value).toBe('看一下 @芝士 ')
  })

  it('编辑框不 @ 资料库：打一个 @ 不去取项目的文件', async () => {
    // 一份文件的 `@` 是把它附在这条消息上，而这个框只改正文、没有待发条，收下的
    // 文件无处可去。所以它连资料库都不取——不是取回来再藏起来。
    const { box, container } = mount()
    await type(box(), '看一下 @')

    expect(searchLibraryFiles).not.toHaveBeenCalled()
    expect(menuLabels(container)).not.toContain('资料库')
  })

  it('没有候选时回车照旧保存', async () => {
    const { box, onSave } = mount()
    await type(box(), '周六交初稿')

    const boxEl = box()
    boxEl.focus()
    await fireEvent.keyDown(boxEl, { key: 'Enter' })
    await flush()

    expect(onSave).toHaveBeenCalledWith('周六交初稿')
  })

  it('菜单开着时 Esc 收起候选，再按一下才是放弃这次编辑', async () => {
    const { box, container, onCancel } = mount()
    await type(box(), '看一下 @')

    const boxEl = box()
    boxEl.focus()
    await fireEvent.keyDown(boxEl, { key: 'Escape' })
    await flush()

    expect(onCancel).not.toHaveBeenCalled()
    expect(container.querySelector('.mention-menu')).toBeNull()
    // `@` 还留在正文里——那只是一次临时收起。
    expect(box().value).toBe('看一下 @')

    await fireEvent.keyDown(boxEl, { key: 'Escape' })
    expect(onCancel).toHaveBeenCalled()
  })

  it('菜单没开时 Esc 就是放弃这次编辑', async () => {
    const { box, onCancel } = mount()
    const boxEl = box()
    boxEl.focus()
    await fireEvent.keyDown(boxEl, { key: 'Escape' })

    expect(onCancel).toHaveBeenCalled()
  })
})
