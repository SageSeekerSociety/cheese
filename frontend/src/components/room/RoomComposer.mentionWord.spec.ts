/** 按词跳、按词选、按词删时，输入框里的一个 `@某某` 是一个词。
 *
 * 草稿里的 `@` 是一段普通文字，浏览器按自己的分词走：`@` 和名字是两站，名字里有空格
 * 还会多一站。这一份问的是用户按下去之后光标、选区落在哪。
 *
 * happy-dom 不会真的按词挪光标，所以「跳」的用例在按键之后把光标放到 Chrome 那一步
 * 会停的地方（`@` 之后、名字中间），再看输入框把它推到了哪；「删」的用例看的是浏览器
 * 删之前选区盖住了哪一段——有选区时按词删，浏览器删的就是选区。
 */
import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../lib/libraryApi', async () => {
  const actual = await vi.importActual<typeof import('../../lib/libraryApi')>('../../lib/libraryApi')
  return { ...actual, searchLibraryFiles: vi.fn().mockResolvedValue([]) }
})

import MessageEditor from './MessageEditor.vue'
import RoomComposer from './RoomComposer.vue'

import { setLocale } from '@/i18n'

const CHEESE_SEAT = { handle: 'cheese-topica', label: '芝士' }
const POOL = [
  { handle: 'mila', label: 'Mila Wu', avatar: null, agent: false },
  { handle: CHEESE_SEAT.handle, label: CHEESE_SEAT.label, avatar: null, agent: true },
]

// 「请 @Mila Wu 看看」：提及占 [2, 10)，后面跟一个空格。
const TEXT = '请 @Mila Wu 看看'
const AT = 2
const END = 10

function mountComposer(text = TEXT) {
  const draft = ref(text)
  const Wrapper = defineComponent({
    setup: () => () =>
      h(RoomComposer, {
        topic: null,
        mentionPool: POOL,
        topicList: [],
        agentSeat: CHEESE_SEAT,
        agentName: CHEESE_SEAT.label,
        alwaysSummon: false,
        hint: '说点什么',
        atts: [],
        attsUploading: false,
        modelValue: draft.value,
        'onUpdate:modelValue': (v: string) => (draft.value = v),
      }),
  })
  const utils = render(Wrapper, { global: { plugins: [createVuetify({ components, directives })] } })
  const box = utils.container.querySelector('textarea')!
  box.focus()
  return box
}

function mountEditor(text: string) {
  const Wrapper = defineComponent({
    setup: () => () =>
      h(MessageEditor, { text, saving: false, mentionPool: POOL, topicList: [], onSave: vi.fn(), onCancel: vi.fn() }),
  })
  const utils = render(Wrapper, { global: { plugins: [createVuetify({ components, directives })] } })
  return utils.container.querySelector('textarea')!
}

const flush = () => new Promise((r) => setTimeout(r, 0))

/** 按一次按词跳：先按键，再把光标放到浏览器那一步停的地方，等输入框看完落点。 */
async function wordStep(
  box: HTMLTextAreaElement,
  key: 'ArrowLeft' | 'ArrowRight',
  mods: { ctrlKey?: boolean; altKey?: boolean; shiftKey?: boolean },
  browserLands: [number, number, ('forward' | 'backward')?]
) {
  await fireEvent.keyDown(box, { key, ...mods })
  box.setSelectionRange(...browserLands)
  await flush()
  return [box.selectionStart, box.selectionEnd]
}

function setPlatform(platform: string) {
  vi.spyOn(navigator, 'platform', 'get').mockReturnValue(platform)
}

beforeEach(() => {
  setLocale('zh-CN')
  setPlatform('Win32')
})
afterEach(() => {
  vi.restoreAllMocks()
})

describe('Ctrl+←/→（Windows、Linux）把 @ 某某 当一个词', () => {
  it('往右跳：停在 @ 后面或名字中间的那一步，落到整个提及之后', async () => {
    const box = mountComposer()
    box.setSelectionRange(AT, AT)
    expect(await wordStep(box, 'ArrowRight', { ctrlKey: true }, [AT + 1, AT + 1])).toEqual([END, END])
    box.setSelectionRange(AT, AT)
    // 名字里有空格：浏览器停在「Mila」后面，那也还在提及里。
    expect(await wordStep(box, 'ArrowRight', { ctrlKey: true }, [AT + 5, AT + 5])).toEqual([END, END])
  })

  it('往左跳：停在名字中间的那一步，落到 @ 前面', async () => {
    const box = mountComposer()
    box.setSelectionRange(END + 1, END + 1)
    expect(await wordStep(box, 'ArrowLeft', { ctrlKey: true }, [AT + 6, AT + 6])).toEqual([AT, AT])
  })

  it('Shift 按词选：选区的那一头不停在提及中间，起点不动', async () => {
    const box = mountComposer()
    box.setSelectionRange(0, 0)
    expect(await wordStep(box, 'ArrowRight', { ctrlKey: true, shiftKey: true }, [0, AT + 1, 'forward'])).toEqual([
      0,
      END,
    ])
    box.setSelectionRange(TEXT.length, TEXT.length)
    expect(
      await wordStep(box, 'ArrowLeft', { ctrlKey: true, shiftKey: true }, [AT + 6, TEXT.length, 'backward'])
    ).toEqual([AT, TEXT.length])
  })

  it('落点不在提及里时照浏览器的走', async () => {
    const box = mountComposer()
    box.setSelectionRange(END, END)
    expect(await wordStep(box, 'ArrowRight', { ctrlKey: true }, [END + 1, END + 1])).toEqual([END + 1, END + 1])
  })

  it('不按词的 ← / → 一格一格走，提及里也停得下', async () => {
    const box = mountComposer()
    box.setSelectionRange(AT, AT)
    expect(await wordStep(box, 'ArrowRight', {}, [AT + 1, AT + 1])).toEqual([AT + 1, AT + 1])
  })
})

describe('Option+←/→（macOS）把 @ 某某 当一个词', () => {
  beforeEach(() => setPlatform('MacIntel'))

  it('Option+→ 落到整个提及之后', async () => {
    const box = mountComposer()
    box.setSelectionRange(AT, AT)
    expect(await wordStep(box, 'ArrowRight', { altKey: true }, [AT + 5, AT + 5])).toEqual([END, END])
  })

  it('macOS 上 Ctrl+→ 是到行尾，不归这里管', async () => {
    const box = mountComposer()
    box.setSelectionRange(AT, AT)
    expect(await wordStep(box, 'ArrowRight', { ctrlKey: true }, [AT + 5, AT + 5])).toEqual([AT + 5, AT + 5])
  })
})

describe('按词删：一个提及整块删掉', () => {
  it('Ctrl+Backspace 在提及后面（隔着空格也算）：删的是整个提及连同空格', async () => {
    const box = mountComposer()
    box.setSelectionRange(END + 1, END + 1)
    await fireEvent.keyDown(box, { key: 'Backspace', ctrlKey: true })
    expect([box.selectionStart, box.selectionEnd]).toEqual([AT, END + 1])
  })

  it('Ctrl+Delete 在提及前面：删的是整个提及', async () => {
    const box = mountComposer()
    box.setSelectionRange(AT, AT)
    await fireEvent.keyDown(box, { key: 'Delete', ctrlKey: true })
    expect([box.selectionStart, box.selectionEnd]).toEqual([AT, END])
  })

  it('Option+Backspace（macOS）同样整块删 @ 芝士', async () => {
    setPlatform('MacIntel')
    const box = mountComposer('@芝士 ')
    box.setSelectionRange(4, 4)
    await fireEvent.keyDown(box, { key: 'Backspace', altKey: true })
    expect([box.selectionStart, box.selectionEnd]).toEqual([0, 4])
  })

  it('前面是普通的字：不碰选区，照浏览器的按词删', async () => {
    const box = mountComposer()
    box.setSelectionRange(TEXT.length, TEXT.length)
    await fireEvent.keyDown(box, { key: 'Backspace', ctrlKey: true })
    expect([box.selectionStart, box.selectionEnd]).toEqual([TEXT.length, TEXT.length])
  })

  it('不按词的 Backspace 一个字一个字删', async () => {
    const box = mountComposer()
    box.setSelectionRange(END, END)
    await fireEvent.keyDown(box, { key: 'Backspace' })
    expect([box.selectionStart, box.selectionEnd]).toEqual([END, END])
  })
})

describe('改消息的那个框也一样', () => {
  it('Ctrl+→ 跳过整个提及', async () => {
    const box = mountEditor(TEXT)
    await flush()
    box.setSelectionRange(AT, AT)
    expect(await wordStep(box, 'ArrowRight', { ctrlKey: true }, [AT + 1, AT + 1])).toEqual([END, END])
  })

  it('原文里留着的频道引用 <#id> 也是一个词', async () => {
    const box = mountEditor('见 <#t-1> 里')
    await flush()
    box.setSelectionRange(8, 8)
    await fireEvent.keyDown(box, { key: 'Backspace', ctrlKey: true })
    expect([box.selectionStart, box.selectionEnd]).toEqual([2, 8])
  })
})
