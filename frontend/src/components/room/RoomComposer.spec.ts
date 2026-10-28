/** 输入区自己那几件事的合同。
 *
 * 这一份钉的是 `RoomComposer.vue` 里**原来一条用例都没走过**的几块：@ 候选的两级
 * 菜单（进资料库、挑文件、Esc 退回一级）、待发条上的回复标签、拖文件进来时亮起来
 * 的落区、以及发送键什么时候是灰的。剩下的（键盘导航、草稿归属、@ 芝士的那三个
 * 入口）在 `../ChatPanel.mentionMenu.spec.ts`、`../ChatPanel.composer.spec.ts`、
 * `../ChatPanel.composerDraft.spec.ts` 和 `../__tests__/ChatPanelComposer.test.ts`
 * 里，各自都在。
 *
 * 为什么先写这一份：这个文件正要被拆成 composable 加几个只管画的件（#2143），而
 * 上面那几块行为散在各处、没有任何断言。拆的时候掉一条，整套用例照样全绿。
 */
import type { MentionPoolEntry } from '@/composables/useRoomMentionPicker'
import type { ChatAttachment, Topic } from '../../cx_types'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../lib/libraryApi', async () => {
  const actual = await vi.importActual<typeof import('../../lib/libraryApi')>('../../lib/libraryApi')
  return { ...actual, searchLibraryFiles: vi.fn().mockResolvedValue([]) }
})
vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    // 待发条上的图片先取字节再画：这个地址在测试里给不出东西，返回空串就够了，
    // 这一份问的不是它画成什么样。
    attachmentImageUrl: vi.fn().mockResolvedValue(''),
    // 「拉进频道」走的是名册抽屉那一条加人的接口。
  }
})

import { searchLibraryFiles } from '../../lib/libraryApi'

import RoomComposer from './RoomComposer.vue'

import { setLocale } from '@/i18n'
import { PENDING_NOTES, usePendingNotes } from '@/lib/pendingNotes'
import { fillComposer, placeCaret, typeInComposer } from '@/test/composerInput'

const CHEESE_SEAT = { handle: 'cheese-topica', label: '芝士' }

const POOL = [
  { handle: 'alice', label: 'Alice', avatar: null, agent: false },
  { handle: CHEESE_SEAT.handle, label: CHEESE_SEAT.label, avatar: null, agent: true },
]

function topic(id = 't1'): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: null,
    title: '做一件事',
    kind: 'channel',
    status: 'active',
    created_at: '2026-08-10T00:00:00Z',
  } as Topic
}

function file(path: string) {
  return {
    type: 'file',
    path,
    bytes: 1,
    modified: 0,
    added_by: null,
    added_at: null,
    room: null,
    replaced: 0,
    references: 0,
  }
}

interface Options {
  draft?: string
  /** 注入一份待发篮（有它时输入框上方逐条列出来，发送键带字）。 */
  pending?: ReturnType<typeof usePendingNotes>
  atts?: ChatAttachment[]
  attsUploading?: boolean
  replyLabel?: string | null
  alwaysSummon?: boolean
  handOff?: boolean
  pool?: MentionPoolEntry[]
  lastMate?: string | null
}

/** 回车那条路只在焦点真的在输入框里时才走（走的时候还会问一次
 *  `document.activeElement`），所以每条按键盘的用例都得先把光标放进去。 */
function focusIn(el: HTMLElement) {
  el.focus()
  expect(document.activeElement).toBe(el)
}

function mount(opts: Options = {}) {
  const draft = ref(opts.draft ?? '')
  const onSend = vi.fn()
  const onFiles = vi.fn()
  const onDropFiles = vi.fn()
  const onPaste = vi.fn()
  const onRemoveAtt = vi.fn()
  const onClearReply = vi.fn()
  const onClearQuote = vi.fn()
  const onAddLibraryFile = vi.fn()

  const Wrapper = defineComponent({
    setup: () => () =>
      h(RoomComposer, {
        topic: topic(),
        mentionPool: opts.pool ?? POOL,
        topicList: [],
        refs: { mentionNames: {}, topicTitles: {} },
        agentSeat: CHEESE_SEAT,
        agentName: CHEESE_SEAT.label,
        alwaysSummon: opts.alwaysSummon ?? false,
        handOff: opts.handOff,
        hint: '说点什么',
        atts: opts.atts ?? [],
        attsUploading: opts.attsUploading ?? false,
        replyLabel: opts.replyLabel ?? null,
        lastMate: opts.lastMate ?? null,
        modelValue: draft.value,
        'onUpdate:modelValue': (v: string) => (draft.value = v),
        onSend,
        onFiles,
        onDropFiles,
        onPaste,
        onRemoveAtt,
        onClearReply,
        onClearQuote,
        onAddLibraryFile,
      }),
  })

  const utils = render(Wrapper, {
    global: {
      plugins: [createVuetify({ components, directives })],
      ...(opts.pending ? { provide: { [PENDING_NOTES as symbol]: opts.pending } } : {}),
    },
  })
  const box = () => utils.container.querySelector<HTMLElement>('.ProseMirror')!
  return {
    ...utils,
    draft,
    box,
    onSend,
    onFiles,
    onDropFiles,
    onPaste,
    onRemoveAtt,
    onClearReply,
    onClearQuote,
    onAddLibraryFile,
  }
}

function menuItems(container: Element): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>('.mention-menu-item'))
}

function labels(container: Element): string[] {
  return menuItems(container).map((el) => el.querySelector('.mention-menu-name')?.textContent?.trim() ?? '')
}

async function flush() {
  for (let i = 0; i < 6; i += 1) await new Promise((r) => setTimeout(r, 0))
}

beforeEach(() => {
  // These assertions read the Chinese copy.
  setLocale('zh-CN')
  // 「+」的菜单和底部面板是 VOverlay：happy-dom 没有 ResizeObserver、visualViewport，不补上浮层挂不起来。
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  vi.stubGlobal('visualViewport', {
    width: 390,
    height: 844,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.mocked(searchLibraryFiles).mockReset().mockResolvedValue([])
})

describe('@ 候选：资料库是往里走一层', () => {
  it('没打字时资料库是一行入口，写着自己有几份文件', async () => {
    vi.mocked(searchLibraryFiles).mockResolvedValue([file('docs/plan.md'), file('img/shot.png')] as never)
    const { container, box } = mount()

    await fillComposer(box(), '@')
    await flush()

    const entry = menuItems(container).find((el) => el.textContent?.includes('资料库'))
    expect(entry, '@ 候选里没有资料库那一行').toBeTruthy()
    expect(entry!.textContent).toContain('2 份文件')
    // 一份文件是一行入口，不是一个候选：项目的文件比房间里的人多得多，平铺进来
    // 会把「@ 一个人」这件事挤掉。
    expect(labels(container)).not.toContain('docs/plan.md')
  })

  it('点进资料库，文件按「文件 / 图片」分组', async () => {
    vi.mocked(searchLibraryFiles).mockResolvedValue([
      file('img/shot.png'),
      file('docs/plan.md'),
      file('img/photo.jpg'),
    ] as never)
    const { container, box } = mount()

    await fillComposer(box(), '@')
    await flush()
    await fireEvent.click(menuItems(container).find((el) => el.textContent?.includes('资料库'))!)
    await flush()

    expect(labels(container)).toEqual(['docs/plan.md', 'img/shot.png', 'img/photo.jpg'])
    const groups = Array.from(container.querySelectorAll('.mention-menu-group')).map((el) => el.textContent?.trim())
    expect(groups).toEqual(['文件', '图片'])
  })

  it('挑一份文件是把它附上，不是把它 @ 进正文', async () => {
    vi.mocked(searchLibraryFiles).mockResolvedValue([file('docs/plan.md')] as never)
    const { container, box, onAddLibraryFile, draft } = mount()

    await fillComposer(box(), '@')
    await flush()
    await fireEvent.click(menuItems(container).find((el) => el.textContent?.includes('资料库'))!)
    await flush()
    await fireEvent.click(menuItems(container)[0])
    await flush()

    expect(onAddLibraryFile).toHaveBeenCalledWith('docs/plan.md')
    // 那个 @ 连同半个名字都从正文里拿掉：文件不是一个能 @ 的人。
    expect(draft.value).toBe('')
  })

  it('打了字就直接搜文件，人、话题、文件一起找', async () => {
    vi.mocked(searchLibraryFiles).mockResolvedValue([file('docs/plan.md'), file('docs/notes.md')] as never)
    const { container, box } = mount()

    await fillComposer(box(), '@not')
    await flush()

    expect(labels(container)).toEqual(['docs/notes.md'])
    // 打了字就不再有那一行入口——这时候人要的是搜索结果。
    expect(labels(container)).not.toContain('资料库')
  })

  it('在资料库里按 Esc 是退回一级，@ 还留在正文里', async () => {
    vi.mocked(searchLibraryFiles).mockResolvedValue([file('docs/plan.md')] as never)
    const { container, box, draft } = mount()

    await fillComposer(box(), '@')
    await flush()
    await fireEvent.click(menuItems(container).find((el) => el.textContent?.includes('资料库'))!)
    await flush()
    expect(container.querySelector('.mention-menu-head')).toBeTruthy()

    await fireEvent.keyDown(box(), { key: 'Escape' })
    await flush()

    // 菜单还开着（那是「往里走了一层」，不是「关掉菜单」），而且回到了一级。
    expect(container.querySelector('.mention-menu')).toBeTruthy()
    expect(container.querySelector('.mention-menu-head')).toBeNull()
    expect(labels(container)).toContain('资料库')
    expect(draft.value).toBe('@')
  })

  it('资料库读不出来时菜单照样开，只是没有文件那一行', async () => {
    vi.mocked(searchLibraryFiles).mockRejectedValue(new Error('offline'))
    const { container, box } = mount()

    await fillComposer(box(), '@')
    await flush()

    expect(container.querySelector('.mention-menu')).toBeTruthy()
    expect(labels(container)).not.toContain('资料库')
    // 挑文件是输入栏里的一个便利，不是这条消息发不出去的理由：人还在名单里。
    expect(labels(container)).toContain('Alice')
  })

  it('AI 队友排在最前，资料库那一行夹在它和群播之间', async () => {
    vi.mocked(searchLibraryFiles).mockResolvedValue([file('docs/plan.md')] as never)
    const { container, box } = mount()

    await fillComposer(box(), '@')
    await flush()

    // 第一格就是回车的默认答案，而「打一个 @ 然后回车」这个产品里压倒性地是「交给
    // 芝士」；群播让位，但资料库那一行是入口不是人，排在群播前面。
    expect(labels(container)).toEqual(['芝士', '资料库', '所有人', '在线成员', 'Alice'])
  })

  /** 按一下键，返回这一下有没有被接住（`preventDefault`）：接住了，浏览器就不会再
   *  拿它去删字、挪光标。 */
  async function press(el: HTMLElement, key: string): Promise<boolean> {
    const ev = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })
    el.dispatchEvent(ev)
    await flush()
    return ev.defaultPrevented
  }

  /** 翻进资料库这一层，确认它真的进去了。 */
  async function intoLibrary(container: Element) {
    await fireEvent.click(menuItems(container).find((el) => el.textContent?.includes('资料库'))!)
    await flush()
    expect(container.querySelector('.mention-menu-head')).toBeTruthy()
  }

  function atRoot(container: Element) {
    expect(container.querySelector('.mention-menu-head')).toBeNull()
    expect(labels(container)).toContain('资料库')
  }

  it('资料库的头上有一颗「返回上一级」，点它回到一级，@ 还留在正文里', async () => {
    vi.mocked(searchLibraryFiles).mockResolvedValue([file('docs/plan.md')] as never)
    const { container, box, draft, getByRole } = mount()

    await fillComposer(box(), '@')
    await flush()
    await intoLibrary(container)

    await fireEvent.click(getByRole('button', { name: '返回上一级' }))
    await flush()

    atRoot(container)
    expect(draft.value).toBe('@')
    // 退回来之后人要接着挑，光标回到输入框。
    expect(document.activeElement).toBe(box())
  })

  it('@ 后面什么都没打时按退格，是退回一级，不是删掉 @', async () => {
    vi.mocked(searchLibraryFiles).mockResolvedValue([file('docs/plan.md')] as never)
    const { container, box, draft } = mount()

    await fillComposer(box(), '@')
    await flush()
    await intoLibrary(container)
    focusIn(box())

    // 这一下退格被菜单接住了：浏览器不会再删掉那个 @。
    expect(await press(box(), 'Backspace')).toBe(true)
    atRoot(container)
    expect(draft.value).toBe('@')
  })

  it('在资料库里打了字再按退格，删的是字，人还在这一层', async () => {
    vi.mocked(searchLibraryFiles).mockResolvedValue([file('docs/plan.md')] as never)
    const { container, box } = mount()

    await fillComposer(box(), '@')
    await flush()
    await intoLibrary(container)
    await fillComposer(box(), '@pl')
    await flush()
    focusIn(box())

    expect(await press(box(), 'Backspace')).toBe(false)
    expect(container.querySelector('.mention-menu-head')).toBeTruthy()
  })

  it('在资料库里按 ← 也是退回一级', async () => {
    vi.mocked(searchLibraryFiles).mockResolvedValue([file('docs/plan.md')] as never)
    const { container, box, draft } = mount()

    await fillComposer(box(), '@')
    await flush()
    await intoLibrary(container)
    focusIn(box())

    expect(await press(box(), 'ArrowLeft')).toBe(true)

    atRoot(container)
    expect(draft.value).toBe('@')
  })

  it('在一级按 ← 和退格不拦：那是挪光标、删字', async () => {
    const { box } = mount()

    await fillComposer(box(), '@')
    await flush()
    focusIn(box())

    expect(await press(box(), 'ArrowLeft')).toBe(false)
    expect(await press(box(), 'Backspace')).toBe(false)
  })
})

describe('@ 候选：没加入频道的人', () => {
  const MIXED: MentionPoolEntry[] = [
    // 顺序故意打乱：排在前面的是没加入的人，候选里他仍然要排到频道里的人后面。
    { handle: 'carol', label: 'Carol', avatar: null, agent: false, outsideTopic: true },
    { handle: 'alice', label: 'Alice', avatar: null, agent: false },
    { handle: CHEESE_SEAT.handle, label: CHEESE_SEAT.label, avatar: null, agent: true },
  ]

  it('频道里的人排在前面，没加入的人跟在后面', async () => {
    const { container, box } = mount({ pool: MIXED })

    await fillComposer(box(), '@')
    await flush()

    expect(labels(container)).toEqual(['芝士', '所有人', '在线成员', 'Alice', 'Carol'])
  })

  it('没加入的人上面挂一条分组标题，频道里的人不挂', async () => {
    const { container, box } = mount({ pool: MIXED })

    await fillComposer(box(), '@')
    await flush()

    const groups = Array.from(container.querySelectorAll('.mention-menu-group')).map((el) => el.textContent?.trim())
    expect(groups).toEqual(['不在频道'])
    // 标题在 Carol 那一行上面，不是在最上头。
    const group = container.querySelector('.mention-menu-group')!
    const carol = menuItems(container).find((el) => el.textContent?.includes('Carol'))!
    expect(group.compareDocumentPosition(carol) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('名单比 7 长也全列出来：后面的人不用再靠「打对名字」才找得到', async () => {
    const many: MentionPoolEntry[] = Array.from({ length: 10 }, (_, i) => ({
      handle: `u${i}`,
      label: `同事${i}`,
      avatar: null,
      agent: false,
      outsideTopic: true,
    }))
    const { container, box } = mount({ pool: many })

    await fillComposer(box(), '@')
    await flush()

    expect(labels(container)).toEqual([
      '所有人',
      '在线成员',
      '同事0',
      '同事1',
      '同事2',
      '同事3',
      '同事4',
      '同事5',
      '同事6',
      '同事7',
      '同事8',
      '同事9',
    ])
  })

  it('没加入的人照样挑得中，写进正文的是他', async () => {
    const { container, box, draft } = mount({ pool: MIXED })

    await fillComposer(box(), '@Car')
    await flush()
    await fireEvent.click(menuItems(container).find((el) => el.textContent?.includes('Carol'))!)
    await flush()

    expect(draft.value).toBe('<@carol> ')
  })
})

describe('@ 写进正文的是那个人', () => {
  it('手打完整的名字，接一个空格就变成他的标签', async () => {
    const { container, box, draft } = mount()
    await typeInComposer(box(), '请 @Alice')
    expect(draft.value).toBe('请 @Alice')
    await typeInComposer(box(), ' ')
    await flush()
    expect(draft.value).toBe('请 <@alice> ')
    expect(container.querySelector('.mention')?.textContent).toBe('@Alice')
  })

  it('在一句话中间也能 @：挑中的人落在光标那里，后面的字不动', async () => {
    const { container, box, draft } = mount({ draft: '请 看一下' })
    placeCaret(box(), '请 '.length)
    await typeInComposer(box(), '@Ali')
    await flush()
    await fireEvent.click(menuItems(container).find((el) => el.textContent?.includes('Alice'))!)
    await flush()
    expect(draft.value).toBe('请 <@alice> 看一下')
  })
})

describe('@ 候选：按 handle 搜，和搜不到时的那句话', () => {
  it('记不住名字、记得住 handle 时，打 handle 也找得到那个人', async () => {
    const { container, box } = mount()

    // 芝士的 label 是「芝士」，一个字都不含 topica；handle 是 cheese-topica。只按
    // 名字搜的话，人默写一遍自己的 handle 也一个候选都搜不出来。
    await fillComposer(box(), '@topica')
    await flush()

    expect(labels(container)).toContain('芝士')
  })

  it('大小写不影响：handle 是 cheese-topica，打 Cheese 也命中', async () => {
    const { container, box } = mount()

    await fillComposer(box(), '@Cheese-')
    await flush()

    expect(labels(container)).toContain('芝士')
  })

  it('一个候选都没有时菜单不消失，说的是「暂无匹配」', async () => {
    const { container, box } = mount()

    await fillComposer(box(), '@zzz')
    await flush()

    // 整块收起来看起来像那个 @ 没生效，人会以为自己打错了。
    expect(container.querySelector('.mention-menu')).toBeTruthy()
    expect(container.querySelector('.mention-menu-item')).toBeNull()
    expect(container.querySelector('.mention-menu')!.textContent).toContain('暂无匹配')
  })

  it('空态里回车不被菜单吃掉：`@` 那半截字原样发出去', async () => {
    const { box, onSend } = mount()

    await fillComposer(box(), '@zzz')
    await flush()
    focusIn(box())
    await fireEvent.keyDown(box(), { key: 'Enter' })

    expect(onSend).toHaveBeenCalledWith({ content: '@zzz', summon: false })
  })

  it('空态里 ↑/↓ 不拦：那时候它们该去挪光标', async () => {
    const { box } = mount()

    await fillComposer(box(), '@zzz')
    await flush()
    focusIn(box())

    const ev = new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true })
    box().dispatchEvent(ev)
    await flush()
    expect(ev.defaultPrevented).toBe(false)
  })

  it('空态里 Esc 把它收起来，再打字又打开', async () => {
    const { container, box } = mount()

    await fillComposer(box(), '@zzz')
    await flush()
    await fireEvent.keyDown(box(), { key: 'Escape' })
    await flush()
    expect(container.querySelector('.mention-menu')).toBeNull()

    await fillComposer(box(), '@zz')
    await flush()
    expect(container.querySelector('.mention-menu')).toBeTruthy()
  })
})

describe('待发的那一行', () => {
  const att: ChatAttachment = { path: 'uploads/id/截图.png', mime: 'image/png' }

  it('什么都没带的时候不占这一行', async () => {
    const { container } = mount()
    expect(container.querySelector('.chip-row')).toBeNull()
  })

  it('待发的附件一枚一个标签，拿掉的说拿掉的是第几枚', async () => {
    const { container, onRemoveAtt } = mount({ atts: [att] })
    await flush()

    const chips = container.querySelectorAll('.chip-list .chip')
    expect(chips).toHaveLength(1)
    const x = chips[0].querySelector<HTMLElement>('.chip__x')!
    // 标签窄，名字放不下就截断，全名在悬停气泡里。
    expect(chips[0].querySelector('.chip__label')?.textContent).toBe('截图.png')

    await fireEvent.click(x)
    expect(onRemoveAtt).toHaveBeenCalledWith(0)
  })

  it('回复的那条也在这一行，× 取消的是回复', async () => {
    const { container, onClearReply } = mount({ replyLabel: '回复 波比：你看下这个' })
    await flush()

    const chip = container.querySelector('.chip-list .chip')!
    expect(chip.classList.contains('reply-chip')).toBe(true)
    expect(chip.querySelector('.chip__label')?.textContent).toBe('回复 波比：你看下这个')

    await fireEvent.click(chip.querySelector<HTMLElement>('.chip__x')!)
    expect(onClearReply).toHaveBeenCalled()
  })

  it('回复在最前，附件跟在它后面', async () => {
    const { container } = mount({ replyLabel: '回复 波比：你看下这个', atts: [att] })
    await flush()

    const classes = Array.from(container.querySelectorAll('.chip-list .chip')).map((el) => el.className)
    expect(classes[0]).toContain('reply-chip')
    expect(classes[1]).toContain('chip')
  })
})

describe('拖文件进来的落区', () => {
  function dragEvent(types: string[], extra: Record<string, unknown> = {}) {
    return { dataTransfer: { types }, ...extra }
  }

  it('真拖着文件才亮：拖一段选中的文字经过不亮', async () => {
    const { container } = mount()
    const root = container.querySelector('.composer')!

    await fireEvent.dragEnter(root, dragEvent(['text/plain']))
    expect(root.classList.contains('composer--drop')).toBe(false)

    await fireEvent.dragEnter(root, dragEvent(['Files']))
    expect(root.classList.contains('composer--drop')).toBe(true)
  })

  it('指针移到自己的子元素上不算离开', async () => {
    const { container } = mount()
    const root = container.querySelector('.composer')!
    const child = container.querySelector('.composer-box')!

    // relatedTarget 是鼠标事件上的属性，happy-dom 里 fireEvent 的 init 传不进去
    // （它是只读的），所以这一条自己造事件。
    function leaveTo(target: Node | null) {
      const ev = new Event('dragleave', { bubbles: true })
      Object.defineProperty(ev, 'relatedTarget', { value: target })
      root.dispatchEvent(ev)
    }

    await fireEvent.dragEnter(root, dragEvent(['Files']))
    leaveTo(child)
    await flush()
    expect(root.classList.contains('composer--drop')).toBe(true)

    leaveTo(null)
    await flush()
    expect(root.classList.contains('composer--drop')).toBe(false)
  })

  it('松手把这件事交给房间，落区自己收起来', async () => {
    const { container, onDropFiles } = mount()
    const root = container.querySelector('.composer')!

    await fireEvent.dragEnter(root, dragEvent(['Files']))
    await fireEvent.drop(root, dragEvent(['Files']))

    expect(onDropFiles).toHaveBeenCalled()
    expect(root.classList.contains('composer--drop')).toBe(false)
  })
})

describe('动作行', () => {
  it('两个藏起来的选择框：一个什么都不挑，一个挑照片和视频', () => {
    const { container } = mount()
    const inputs = Array.from(container.querySelectorAll<HTMLInputElement>('input[type="file"]'))
    expect(inputs).toHaveLength(2)
    expect(inputs[0].accept).toBe('')
    expect(inputs[1].accept).toBe('image/*,video/*')
  })

  it('挑完文件发出去，并且把选择框自己清空——同一个文件再挑一次还得有反应', async () => {
    const { container, onFiles } = mount()
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')!
    const picked = new File(['x'], 'a.txt', { type: 'text/plain' })

    await fireEvent.change(input, { target: { files: [picked] } })
    expect(onFiles).toHaveBeenCalledWith([picked])
    expect(input.value).toBe('')
  })

  it('发送键在空输入框上是灰的，有字才亮', async () => {
    const { container, box } = mount()
    const send = container.querySelector<HTMLButtonElement>('.composer-send')!
    expect(send.disabled).toBe(true)

    await fillComposer(box(), '看看这个')
    expect(send.disabled).toBe(false)
  })

  it('附件还在上传的时候发送键是灰的，回车也发不出去', async () => {
    const { container, box, onSend } = mount({ draft: '看看这个', attsUploading: true })
    expect(container.querySelector<HTMLButtonElement>('.composer-send')!.disabled).toBe(true)

    focusIn(box())
    await fireEvent.keyDown(box(), { key: 'Enter' })
    expect(onSend).not.toHaveBeenCalled()
  })

  it('和 AI 队友私聊时没有交给谁那颗头像：那儿每条都是说给它听的', () => {
    const { container } = mount({ alwaysSummon: true, handOff: false })
    expect(container.querySelector('.summon__btn')).toBeNull()
  })

  it('没输法选字时的回车发送，正文按规范形式展开', async () => {
    const { box, onSend } = mount()
    await fillComposer(box(), '@Alice 你看下')
    focusIn(box())
    await fireEvent.keyDown(box(), { key: 'Enter' })
    expect(onSend).toHaveBeenCalledWith({ content: '<@alice> 你看下', summon: false })
  })

  it('任务里 @ 了人、没 @ AI 队友的一句是说给那个人的，不交给队友', async () => {
    const { box, onSend } = mount({ alwaysSummon: true })
    await fillComposer(box(), '@Alice 你看这个反馈')
    focusIn(box())
    await fireEvent.keyDown(box(), { key: 'Enter' })
    expect(onSend).toHaveBeenCalledWith({ content: '<@alice> 你看这个反馈', summon: false })
  })

  it('任务里不 @ 任何人、或 @ 了人也 @ 了队友，照样交给队友', async () => {
    const { box, onSend } = mount({ alwaysSummon: true })
    focusIn(box())
    await fillComposer(box(), '接着做')
    await fireEvent.keyDown(box(), { key: 'Enter' })
    await fillComposer(box(), '@Alice @芝士 一起看')
    await fireEvent.keyDown(box(), { key: 'Enter' })
    expect(onSend.mock.calls.map(([sent]) => sent.summon)).toEqual([true, true])
  })

  it('输入法正在选字时，回车是上屏那一下，不是发送', async () => {
    const { box, onSend } = mount()
    await fillComposer(box(), '看看这个')
    focusIn(box())

    await fireEvent.compositionStart(box())
    await fireEvent.keyDown(box(), { key: 'Enter' })
    expect(onSend).not.toHaveBeenCalled()
  })

  it('上屏之后紧接着按的回车是发送，不被当成输入法吃掉', async () => {
    // 打中文的人上屏之后紧接着按回车发送，两次按键只隔几十毫秒。曾经这里有一个
    // 「compositionend 之后 100 毫秒内的按键都算输入法」的时间窗，把这一次发送也吃掉了，
    // 输入框里留下来一行换行（真机 2026-10-24 反馈）。判据只能看这一次按键本身。
    const { box, onSend } = mount()
    await fillComposer(box(), '看看这个')
    focusIn(box())

    await fireEvent.compositionEnd(box())
    await fireEvent.keyDown(box(), { key: 'Enter', keyCode: 13 })
    expect(onSend).toHaveBeenCalledTimes(1)
  })

  it('上屏那一下的回车本身不是发送：它带 isComposing 或 229', async () => {
    const { box, onSend } = mount()
    await fillComposer(box(), '看看这个')
    focusIn(box())

    await fireEvent.keyDown(box(), { key: 'Enter', isComposing: true })
    await fireEvent.keyDown(box(), { key: 'Enter', keyCode: 229 })
    expect(onSend).not.toHaveBeenCalled()
  })

  it('Shift+Enter 是换行，不发送', async () => {
    const { box, onSend } = mount()
    await fillComposer(box(), '看看这个')
    focusIn(box())
    await fireEvent.keyDown(box(), { key: 'Enter', shiftKey: true })
    expect(onSend).not.toHaveBeenCalled()
  })

  it('粘贴交给房间——上传和缩略图是它那一侧的事', async () => {
    const { box, onPaste } = mount()
    box().dispatchEvent(pasteText(''))
    expect(onPaste).toHaveBeenCalled()
  })
})

/** happy-dom 里 fireEvent 传不进只读的 clipboardData，所以这一份自己造事件。 */
function pasteText(text: string): Event {
  const ev = new Event('paste', { bubbles: true, cancelable: true })
  Object.defineProperty(ev, 'clipboardData', {
    value: { getData: (type: string) => (type === 'text/plain' ? text : '') },
  })
  return ev
}

describe('贴一大段文字：就地给一条转成附件的路', () => {
  function offerButton(container: Element, text: string): HTMLElement | undefined {
    return Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.trim() === text)
  }

  it('短粘贴就是平常的粘贴：字进正文，不冒出提议', async () => {
    const { container, box, draft, onFiles } = mount()
    box().dispatchEvent(pasteText('一句普通的话'))
    await flush()
    expect(container.querySelector('.composer-strip')).toBeNull()
    expect(onFiles).not.toHaveBeenCalled()
    expect(draft.value).toBe('一句普通的话')
  })

  it('超过字数就冒出一条提议，正文原样留着', async () => {
    const long = 'x'.repeat(4001)
    const { container, box, draft } = mount()
    box().dispatchEvent(pasteText(long))
    await flush()
    const offer = container.querySelector('.composer-strip')
    expect(offer, '贴长文字没有出现转附件的提议').toBeTruthy()
    expect(offer!.textContent).toContain('4001')
    // 忽略它：正文就是粘进来的样子——原来的行为不变。
    expect(draft.value).toBe(long)
  })

  it('一行很短但行数很多，也算长', async () => {
    const manyLines = Array.from({ length: 90 }, () => 'a').join('\n')
    const { container, box } = mount()
    box().dispatchEvent(pasteText(manyLines))
    await flush()
    expect(container.querySelector('.composer-strip')).toBeTruthy()
  })

  it('「存为 .txt」把那一段从正文里拿掉，换成一份 txt 附件', async () => {
    const long = 'x'.repeat(4001)
    const { container, box, draft, onFiles } = mount()
    box().dispatchEvent(pasteText(long))
    await flush()
    await fireEvent.click(offerButton(container, '存为 .txt')!)
    await flush()

    expect(draft.value).toBe('')
    expect(container.querySelector('.composer-strip')).toBeNull()
    expect(onFiles).toHaveBeenCalledTimes(1)
    const [files] = onFiles.mock.calls[0] as [File[]]
    expect(files).toHaveLength(1)
    expect(files[0].name).toMatch(/^粘贴的文字-\d{8}-\d{6}\.txt$/)
    expect(files[0].type).toBe('text/plain')
  })

  it('「存为 .md」给的是 markdown', async () => {
    const long = 'y'.repeat(5000)
    const { container, box, onFiles } = mount()
    box().dispatchEvent(pasteText(long))
    await flush()
    await fireEvent.click(offerButton(container, '存为 .md')!)
    await flush()
    const [files] = onFiles.mock.calls[0] as [File[]]
    expect(files[0].name).toMatch(/\.md$/)
    expect(files[0].type).toBe('text/markdown')
  })

  it('关掉提议只是关掉：正文一个字不动', async () => {
    const long = 'z'.repeat(4001)
    const { container, box, draft, onFiles } = mount()
    box().dispatchEvent(pasteText(long))
    await flush()
    await fireEvent.click(container.querySelector('.composer-strip__x')!)
    await flush()
    expect(container.querySelector('.composer-strip')).toBeNull()
    expect(draft.value).toBe(long)
    expect(onFiles).not.toHaveBeenCalled()
  })

  it('长粘贴仍然交给房间先看一眼——贴截图优先走上传', async () => {
    const { box, onPaste } = mount()
    box().dispatchEvent(pasteText('x'.repeat(4001)))
    await flush()
    expect(onPaste).toHaveBeenCalled()
  })
})

describe('⌘/Ctrl+Enter 和输入框的按键提示', () => {
  it('普通频道里提示能交给队友，⌘+Enter 发出去的那条带着 @', async () => {
    const { box, onSend } = mount({ draft: '看一下' })
    expect(box().getAttribute('title')).toContain('发送并交给芝士')

    focusIn(box())
    await fireEvent.keyDown(box(), { key: 'Enter', metaKey: true })

    expect(onSend).toHaveBeenCalledWith(expect.objectContaining({ summon: true }))
    expect(onSend.mock.calls[0][0].content).toContain('@')
  })

  it('和 AI 队友私聊：提示不说交给谁，⌘+Enter 就是发送，正文不加 @', async () => {
    const { box, onSend } = mount({ draft: '看一下', alwaysSummon: true, handOff: false })
    expect(box().getAttribute('title')).toBe('Enter 发送，Shift+Enter 换行，可直接粘贴图片')

    focusIn(box())
    await fireEvent.keyDown(box(), { key: 'Enter', metaKey: true })

    expect(onSend).toHaveBeenCalledWith({ content: '看一下', summon: true })
  })
})

// 发送键旁边那颗队友头像：这条发出去谁会接手。它读的是正文，点它、换一位写的都是正文里的 @。
describe('交给谁', () => {
  const OWN = { handle: 'cheese-0a1b2c3d', label: '我的 Claude Code' }
  const TWO = [...POOL, { handle: OWN.handle, label: OWN.label, avatar: null, agent: true }]
  const summon = (container: Element) => container.querySelector<HTMLButtonElement>('.summon__btn')!

  it('任务负责人那里是规则不是开关：点头像不改正文', async () => {
    const { container, draft } = mount({ alwaysSummon: true, draft: '看看这个' })
    await fireEvent.click(summon(container))
    expect(draft.value).toBe('看看这个')
  })

  it('任务里只 @ 了人：头像跟着说这句不交给队友', async () => {
    const { container, box } = mount({ alwaysSummon: true })
    await fillComposer(box(), '@Alice 你看下')
    expect(summon(container).getAttribute('aria-label')).toBe('这句只说给 @ 到的人')
  })

  it('右键换一位：正文里那枚标签换成那一位，原来那位拿掉', async () => {
    const { container, draft } = mount({ pool: TWO, draft: '<@cheese-topica> 看看这个' })
    await fireEvent.contextMenu(summon(container))
    await flush()
    const row = Array.from(document.querySelectorAll<HTMLElement>('[role="menuitemradio"]')).find((el) =>
      el.textContent?.includes(OWN.label)
    )
    expect(row, '右键没有弹出交给哪位队友').toBeTruthy()
    await fireEvent.click(row!)
    expect(draft.value).toBe(`<@${OWN.handle}> 看看这个`)
  })

  it('换过一位之后，再点头像交给的也是它', async () => {
    const { container, draft } = mount({ pool: TWO, draft: '看看这个' })
    await fireEvent.contextMenu(summon(container))
    await flush()
    const row = Array.from(document.querySelectorAll<HTMLElement>('[role="menuitemradio"]')).find((el) =>
      el.textContent?.includes(OWN.label)
    )
    await fireEvent.click(row!)
    await fireEvent.click(summon(container))
    expect(draft.value).toBe('看看这个')
    await fireEvent.click(summon(container))
    expect(draft.value).toBe(`<@${OWN.handle}> 看看这个`)
  })

  it('平时点一下交给的是这个频道里上次交给的那位', async () => {
    const { container, draft } = mount({ pool: TWO, lastMate: OWN.handle, draft: '看看这个' })
    await fireEvent.click(summon(container))
    expect(draft.value).toBe(`<@${OWN.handle}> 看看这个`)
  })

  it('只有一位队友时右键不弹选单', async () => {
    const { container } = mount({ draft: '看看这个' })
    await fireEvent.contextMenu(summon(container))
    await flush()
    expect(document.querySelector('[role="menuitemradio"]')).toBeNull()
  })
})

// 攒着待发批注时，发送键发的是那一批：正文里的字当作总体说明并进同一条消息。
describe('攒着待发批注时的发送键', () => {
  function noteMsg(message: string) {
    return {
      file: 'a.md',
      version: '',
      where: '开头',
      quote: 'q',
      text: 't',
      audience: 'cheese' as const,
      message,
    }
  }

  it('按发送键发的是那一批：正文里的字并进同一条，乱序那条不发', async () => {
    const send = vi.fn(() => true)
    const pending = usePendingNotes(send)
    pending.add(noteMsg('甲'))
    const { box, draft, onSend } = mount({ draft: '都按这个来', pending })
    focusIn(box())
    await fireEvent.keyDown(box(), { key: 'Enter' })
    expect(send).toHaveBeenCalledWith('都按这个来\n\n甲')
    expect(onSend).not.toHaveBeenCalled()
    expect(draft.value).toBe('')
  })

  it('这一批发出去之后引用条也清掉：它是那条消息带着的东西', async () => {
    const pending = usePendingNotes(() => true)
    pending.add(noteMsg('甲'))
    const { box, onClearQuote } = mount({ draft: '说明', pending })
    focusIn(box())
    await fireEvent.keyDown(box(), { key: 'Enter' })
    expect(onClearQuote).toHaveBeenCalled()
  })

  it('对话栏没收下时走老路：正文照发，这一批留着', async () => {
    const pending = usePendingNotes(() => false)
    pending.add(noteMsg('甲'))
    const { box, onSend, draft } = mount({ draft: '说明', pending })
    focusIn(box())
    await fireEvent.keyDown(box(), { key: 'Enter' })
    expect(onSend).toHaveBeenCalledWith({ content: '说明', summon: false })
    expect(pending.notes.value).toHaveLength(1)
    expect(draft.value).toBe('说明')
  })
})
