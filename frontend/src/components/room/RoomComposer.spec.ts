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

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    listProjectLibrary: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    // 待发条上的图片先取字节再画：这个地址在测试里给不出东西，返回空串就够了，
    // 这一份问的不是它画成什么样。
    attachmentImageUrl: vi.fn().mockResolvedValue(''),
    // 「拉进话题」走的是名册抽屉那一条加人的接口。
    addTopicMember: vi.fn().mockResolvedValue({}),
  }
})

import { addTopicMember, listProjectLibrary } from '../../api'

import RoomComposer from './RoomComposer.vue'

import { setLocale } from '@/i18n'

const CHEESE_SEAT = { handle: 'cheese-topica', label: '芝士' }

const POOL = [
  { handle: 'alice', label: 'Alice', agent: false },
  { handle: CHEESE_SEAT.handle, label: CHEESE_SEAT.label, agent: true },
]

function topic(id = 't1'): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: null,
    title: '做一件事',
    kind: 'topic',
    status: 'active',
    created_at: '2026-08-10T00:00:00Z',
  } as Topic
}

function file(path: string) {
  return { path, bytes: 1, modified: 0, added_by: null, added_at: null, room: null, replaced: 0, references: 0 }
}

interface Options {
  draft?: string
  atts?: ChatAttachment[]
  attsUploading?: boolean
  replyLabel?: string | null
  alwaysSummon?: boolean
  pool?: MentionPoolEntry[]
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
  const onAddLibraryFile = vi.fn()

  const Wrapper = defineComponent({
    setup: () => () =>
      h(RoomComposer, {
        topic: topic(),
        mentionPool: opts.pool ?? POOL,
        topicList: [],
        agentSeat: CHEESE_SEAT,
        agentName: CHEESE_SEAT.label,
        alwaysSummon: opts.alwaysSummon ?? false,
        hint: '说点什么',
        atts: opts.atts ?? [],
        attsUploading: opts.attsUploading ?? false,
        replyLabel: opts.replyLabel ?? null,
        modelValue: draft.value,
        'onUpdate:modelValue': (v: string) => (draft.value = v),
        onSend,
        onFiles,
        onDropFiles,
        onPaste,
        onRemoveAtt,
        onClearReply,
        onAddLibraryFile,
      }),
  })

  const utils = render(Wrapper, { global: { plugins: [createVuetify({ components, directives })] } })
  const box = () => utils.container.querySelector('textarea')!
  return { ...utils, draft, box, onSend, onFiles, onDropFiles, onPaste, onRemoveAtt, onClearReply, onAddLibraryFile }
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
  vi.mocked(listProjectLibrary)
    .mockReset()
    .mockResolvedValue({ data: [], total: 0 } as never)
})

describe('@ 候选：资料库是往里走一层', () => {
  it('没打字时资料库是一行入口，写着自己有几份文件', async () => {
    vi.mocked(listProjectLibrary).mockResolvedValue({
      data: [file('docs/plan.md'), file('img/shot.png')],
      total: 2,
    } as never)
    const { container, box } = mount()

    await fireEvent.update(box(), '@')
    await flush()

    const entry = menuItems(container).find((el) => el.textContent?.includes('资料库'))
    expect(entry, '@ 候选里没有资料库那一行').toBeTruthy()
    expect(entry!.textContent).toContain('2 份文件')
    // 一份文件是一行入口，不是一个候选：项目的文件比房间里的人多得多，平铺进来
    // 会把「@ 一个人」这件事挤掉。
    expect(labels(container)).not.toContain('docs/plan.md')
  })

  it('点进资料库，文件按「文件 / 图片」分组', async () => {
    vi.mocked(listProjectLibrary).mockResolvedValue({
      data: [file('img/shot.png'), file('docs/plan.md'), file('img/photo.jpg')],
      total: 3,
    } as never)
    const { container, box } = mount()

    await fireEvent.update(box(), '@')
    await flush()
    await fireEvent.click(menuItems(container).find((el) => el.textContent?.includes('资料库'))!)
    await flush()

    expect(labels(container)).toEqual(['docs/plan.md', 'img/shot.png', 'img/photo.jpg'])
    const groups = Array.from(container.querySelectorAll('.mention-menu-group')).map((el) => el.textContent?.trim())
    expect(groups).toEqual(['文件', '图片'])
  })

  it('挑一份文件是把它附上，不是把它 @ 进正文', async () => {
    vi.mocked(listProjectLibrary).mockResolvedValue({ data: [file('docs/plan.md')], total: 1 } as never)
    const { container, box, onAddLibraryFile, draft } = mount()

    await fireEvent.update(box(), '@')
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
    vi.mocked(listProjectLibrary).mockResolvedValue({
      data: [file('docs/plan.md'), file('docs/notes.md')],
      total: 2,
    } as never)
    const { container, box } = mount()

    await fireEvent.update(box(), '@not')
    await flush()

    expect(labels(container)).toEqual(['docs/notes.md'])
    // 打了字就不再有那一行入口——这时候人要的是搜索结果。
    expect(labels(container)).not.toContain('资料库')
  })

  it('在资料库里按 Esc 是退回一级，@ 还留在正文里', async () => {
    vi.mocked(listProjectLibrary).mockResolvedValue({ data: [file('docs/plan.md')], total: 1 } as never)
    const { container, box, draft } = mount()

    await fireEvent.update(box(), '@')
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
    vi.mocked(listProjectLibrary).mockRejectedValue(new Error('offline'))
    const { container, box } = mount()

    await fireEvent.update(box(), '@')
    await flush()

    expect(container.querySelector('.mention-menu')).toBeTruthy()
    expect(labels(container)).not.toContain('资料库')
    // 挑文件是输入栏里的一个便利，不是这条消息发不出去的理由：人还在名单里。
    expect(labels(container)).toContain('Alice')
  })

  it('AI 队友排在最前，资料库那一行夹在它和群播之间', async () => {
    vi.mocked(listProjectLibrary).mockResolvedValue({ data: [file('docs/plan.md')], total: 1 } as never)
    const { container, box } = mount()

    await fireEvent.update(box(), '@')
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
    vi.mocked(listProjectLibrary).mockResolvedValue({ data: [file('docs/plan.md')], total: 1 } as never)
    const { container, box, draft, getByRole } = mount()

    await fireEvent.update(box(), '@')
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
    vi.mocked(listProjectLibrary).mockResolvedValue({ data: [file('docs/plan.md')], total: 1 } as never)
    const { container, box, draft } = mount()

    await fireEvent.update(box(), '@')
    await flush()
    await intoLibrary(container)
    focusIn(box())

    // 这一下退格被菜单接住了：浏览器不会再删掉那个 @。
    expect(await press(box(), 'Backspace')).toBe(true)
    atRoot(container)
    expect(draft.value).toBe('@')
  })

  it('在资料库里打了字再按退格，删的是字，人还在这一层', async () => {
    vi.mocked(listProjectLibrary).mockResolvedValue({ data: [file('docs/plan.md')], total: 1 } as never)
    const { container, box } = mount()

    await fireEvent.update(box(), '@')
    await flush()
    await intoLibrary(container)
    await fireEvent.update(box(), '@pl')
    await flush()
    focusIn(box())

    expect(await press(box(), 'Backspace')).toBe(false)
    expect(container.querySelector('.mention-menu-head')).toBeTruthy()
  })

  it('在资料库里按 ← 也是退回一级', async () => {
    vi.mocked(listProjectLibrary).mockResolvedValue({ data: [file('docs/plan.md')], total: 1 } as never)
    const { container, box, draft } = mount()

    await fireEvent.update(box(), '@')
    await flush()
    await intoLibrary(container)
    focusIn(box())

    expect(await press(box(), 'ArrowLeft')).toBe(true)

    atRoot(container)
    expect(draft.value).toBe('@')
  })

  it('在一级按 ← 和退格不拦：那是挪光标、删字', async () => {
    const { box } = mount()

    await fireEvent.update(box(), '@')
    await flush()
    focusIn(box())

    expect(await press(box(), 'ArrowLeft')).toBe(false)
    expect(await press(box(), 'Backspace')).toBe(false)
  })
})

describe('@ 候选：不在话题里的人', () => {
  const MIXED: MentionPoolEntry[] = [
    // 顺序故意打乱：排在前面的是话题外的人，候选里他仍然要排到话题里的人后面。
    { handle: 'carol', label: 'Carol', agent: false, outsideTopic: true },
    { handle: 'alice', label: 'Alice', agent: false },
    { handle: CHEESE_SEAT.handle, label: CHEESE_SEAT.label, agent: true },
  ]

  it('话题里的人排在前面，不在话题里的人跟在后面，右边挂「不在话题中」', async () => {
    const { container, box } = mount({ pool: MIXED })

    await fireEvent.update(box(), '@')
    await flush()

    expect(labels(container)).toEqual(['芝士', '所有人', '在线成员', 'Alice', 'Carol'])
    const row = (name: string) => menuItems(container).find((el) => el.textContent?.includes(name))!
    expect(row('Carol').textContent).toContain('不在话题中')
    expect(row('Alice').textContent).not.toContain('不在话题中')
    expect(row('芝士').textContent).not.toContain('不在话题中')
  })

  it('不在话题里的人照样挑得中，@ 写进正文', async () => {
    const { container, box, draft } = mount({ pool: MIXED })

    await fireEvent.update(box(), '@Car')
    await flush()
    await fireEvent.click(menuItems(container).find((el) => el.textContent?.includes('Carol'))!)
    await flush()

    expect(draft.value).toBe('@Carol ')
  })
})

describe('@ 候选：按 handle 搜，和搜不到时的那句话', () => {
  it('记不住名字、记得住 handle 时，打 handle 也找得到那个人', async () => {
    const { container, box } = mount()

    // 芝士的 label 是「芝士」，一个字都不含 topica；handle 是 cheese-topica。只按
    // 名字搜的话，人默写一遍自己的 handle 也一个候选都搜不出来。
    await fireEvent.update(box(), '@topica')
    await flush()

    expect(labels(container)).toContain('芝士')
  })

  it('大小写不影响：handle 是 cheese-topica，打 Cheese 也命中', async () => {
    const { container, box } = mount()

    await fireEvent.update(box(), '@Cheese-')
    await flush()

    expect(labels(container)).toContain('芝士')
  })

  it('一个候选都没有时菜单不消失，说的是「暂无匹配」', async () => {
    const { container, box } = mount()

    await fireEvent.update(box(), '@zzz')
    await flush()

    // 整块收起来看起来像那个 @ 没生效，人会以为自己打错了。
    expect(container.querySelector('.mention-menu')).toBeTruthy()
    expect(container.querySelector('.mention-menu-item')).toBeNull()
    expect(container.querySelector('.mention-menu')!.textContent).toContain('暂无匹配')
  })

  it('空态里回车不被菜单吃掉：`@` 那半截字原样发出去', async () => {
    const { box, onSend } = mount()

    await fireEvent.update(box(), '@zzz')
    await flush()
    focusIn(box())
    await fireEvent.keyDown(box(), { key: 'Enter' })

    expect(onSend).toHaveBeenCalledWith({ content: '@zzz', summon: false })
  })

  it('空态里 ↑/↓ 不拦：那时候它们该去挪光标', async () => {
    const { box } = mount()

    await fireEvent.update(box(), '@zzz')
    await flush()
    focusIn(box())

    const ev = new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true })
    box().dispatchEvent(ev)
    await flush()
    expect(ev.defaultPrevented).toBe(false)
  })

  it('空态里 Esc 把它收起来，再打字又打开', async () => {
    const { container, box } = mount()

    await fireEvent.update(box(), '@zzz')
    await flush()
    await fireEvent.keyDown(box(), { key: 'Escape' })
    await flush()
    expect(container.querySelector('.mention-menu')).toBeNull()

    await fireEvent.update(box(), '@zz')
    await flush()
    expect(container.querySelector('.mention-menu')).toBeTruthy()
  })
})

describe('发出去的消息 @ 了不在话题里的人', () => {
  /** 自己是这个话题的 `role`；Carol 在项目里、不在这个话题里。 */
  function pool(role: string): MentionPoolEntry[] {
    return [
      { handle: 'alice', label: 'Alice', agent: false, role },
      { handle: 'dave', label: 'Dave', agent: false, role: 'member' },
      { handle: 'carol', label: 'Carol', agent: false, outsideTopic: true },
      { handle: CHEESE_SEAT.handle, label: CHEESE_SEAT.label, agent: true, role: 'member' },
    ]
  }

  function notice(container: Element): HTMLElement | null {
    return container.querySelector<HTMLElement>('.outside-notice')
  }

  async function send(utils: ReturnType<typeof mount>, text: string) {
    await fireEvent.update(utils.box(), text)
    await flush()
    await fireEvent.click(utils.getByRole('button', { name: '发送' }))
    await flush()
  }

  beforeEach(() => {
    localStorage.setItem('user', JSON.stringify({ id: 1, username: 'alice' }))
    vi.mocked(addTopicMember)
      .mockReset()
      .mockResolvedValue({} as never)
  })

  it('只 @ 了话题里的人，不提示', async () => {
    const utils = mount({ pool: pool('owner') })

    await send(utils, '@Dave 看一下')

    expect(utils.onSend).toHaveBeenCalled()
    expect(notice(utils.container)).toBeNull()
  })

  it('@ 了不在话题里的人，发出去之后说一句他收不到通知', async () => {
    const utils = mount({ pool: pool('owner') })

    await send(utils, '@Carol @Dave 看一下')

    expect(notice(utils.container)?.textContent).toContain('Carol 不在话题中，他们不会收到通知')
    expect(notice(utils.container)?.textContent).not.toContain('Dave')
  })

  it('能管名册的人点「拉进话题」把他加进来，提示随之消失', async () => {
    const utils = mount({ pool: pool('admin') })
    await send(utils, '@Carol 看一下')

    await fireEvent.click(utils.getByRole('button', { name: '拉进话题' }))
    await flush()

    expect(addTopicMember).toHaveBeenCalledWith('t1', 'carol', 'member')
    expect(notice(utils.container)).toBeNull()
  })

  it('不能管名册的人只看到那句话，没有「拉进话题」', async () => {
    const utils = mount({ pool: pool('member') })

    await send(utils, '@Carol 看一下')

    expect(notice(utils.container)?.textContent).toContain('不在话题中')
    expect(utils.queryByRole('button', { name: '拉进话题' })).toBeNull()
  })

  it('加人失败时提示留着，并说出为什么', async () => {
    vi.mocked(addTopicMember).mockRejectedValue(new Error('只能添加项目成员'))
    const utils = mount({ pool: pool('owner') })
    await send(utils, '@Carol 看一下')

    await fireEvent.click(utils.getByRole('button', { name: '拉进话题' }))
    await flush()

    expect(notice(utils.container)?.textContent).toContain('只能添加项目成员')
    expect(notice(utils.container)?.textContent).toContain('Carol')
  })

  it('点 × 收起提示，谁也不加', async () => {
    const utils = mount({ pool: pool('owner') })
    await send(utils, '@Carol 看一下')

    await fireEvent.click(utils.getByRole('button', { name: '知道了' }))
    await flush()

    expect(notice(utils.container)).toBeNull()
    expect(addTopicMember).not.toHaveBeenCalled()
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
  it('两个藏起来的选择框：一个什么都不挑，一个只挑图片', () => {
    const { container } = mount()
    const inputs = Array.from(container.querySelectorAll<HTMLInputElement>('input[type="file"]'))
    expect(inputs).toHaveLength(2)
    expect(inputs[0].accept).toBe('')
    expect(inputs[1].accept).toBe('image/*')
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

    await fireEvent.update(box(), '看看这个')
    expect(send.disabled).toBe(false)
  })

  it('附件还在上传的时候发送键是灰的，回车也发不出去', async () => {
    const { container, box, onSend } = mount({ draft: '看看这个', attsUploading: true })
    expect(container.querySelector<HTMLButtonElement>('.composer-send')!.disabled).toBe(true)

    focusIn(box())
    await fireEvent.keyDown(box(), { key: 'Enter' })
    expect(onSend).not.toHaveBeenCalled()
  })

  // 带触摸屏的笔记本两样都对，所以按输入方式判断，不按视口宽度。
  it('桌面上没有「照片」那一颗，触摸屏上才有', () => {
    const { container } = mount()
    // happy-dom 的窗口是 1024 宽，也就是 mdAndUp。
    expect(container.querySelector('.mdi-image-outline')).toBeNull()
    expect(container.querySelector('.mdi-paperclip')).toBeTruthy()
  })

  it('私聊里没有「交给芝士」这颗按钮：那儿每条都是说给它听的', () => {
    const { container } = mount({ alwaysSummon: true })
    expect(container.querySelector('.summon-btn')).toBeNull()
  })

  it('没输法选字时的回车发送，正文按规范形式展开', async () => {
    const { box, onSend } = mount()
    await fireEvent.update(box(), '@Alice 你看下')
    focusIn(box())
    await fireEvent.keyDown(box(), { key: 'Enter' })
    expect(onSend).toHaveBeenCalledWith({ content: '<@alice> 你看下', summon: false })
  })

  it('输入法正在选字时，回车是上屏那一下，不是发送', async () => {
    const { box, onSend } = mount()
    await fireEvent.update(box(), '看看这个')
    focusIn(box())

    await fireEvent.compositionStart(box())
    await fireEvent.keyDown(box(), { key: 'Enter' })
    expect(onSend).not.toHaveBeenCalled()
  })

  it('输入法上屏留下的时间戳也算：那一下回车不是发送', async () => {
    const { box, onSend } = mount()
    await fireEvent.update(box(), '看看这个')
    focusIn(box())

    await fireEvent.compositionEnd(box())
    await fireEvent.keyDown(box(), { key: 'Enter' })
    expect(onSend).not.toHaveBeenCalled()
  })

  it('Shift+Enter 是换行，不发送', async () => {
    const { box, onSend } = mount()
    await fireEvent.update(box(), '看看这个')
    focusIn(box())
    await fireEvent.keyDown(box(), { key: 'Enter', shiftKey: true })
    expect(onSend).not.toHaveBeenCalled()
  })

  it('粘贴交给房间——上传和缩略图是它那一侧的事', async () => {
    const { box, onPaste } = mount()
    await fireEvent.paste(box())
    expect(onPaste).toHaveBeenCalled()
  })
})
