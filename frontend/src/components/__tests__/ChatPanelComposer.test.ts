/** 输入栏归聊天栏 (规则 5)。
 *
 * 工作台以前在对话栏和工作面板底下横跨着自己的一份输入栏——和 ChatPanel 自己那份
 * 几乎逐行重复。横跨读起来像「对整个话题说话」，而它发出去的 99% 是只有左边这一
 * 栏显示的聊天消息；重复则意味着两份实现要靠人记着同步。
 *
 * 这里钉的是并完之后仍然成立的三件事：输入栏在对话栏里、话题自己的 chips 能从外面
 * 交进来、以及**这条消息 @ 没 @ 芝士**决定它会不会被叫起来。
 */
import type { Topic } from '../../cx_types'

import { h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    listBlocks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getProgress: vi.fn().mockResolvedValue({ items: [], updated_at: null }),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    // 芝士的座位在**话题**名册上，一个话题一个分身。项目名册上没有它——这正是
    // 「线上 @ 不出芝士」那次的成因，所以这里照真实形状摆：分身 handle 带话题
    // 后缀，而项目名册里只有人。
    listTopicMembers: vi.fn().mockResolvedValue({
      data: [
        { id: 'm1', member_handle: 'alice', name: 'Alice', role: 'owner', agent: false },
        { id: 'm2', member_handle: 'cheese-topica', name: '芝士', role: 'member', agent: true },
      ],
      total: 2,
    }),
    chatWsUrl: () => 'ws://test/ws',
    // 这个地址挂不上 <img src>：附件端点从 Authorization 头认人，浏览器发图片请求
    // 带不了这个头，挂上去的结果是 401。输入框的缩略图得用 attachmentImageUrl 取字节。
    attachmentRawUrl: () => '',
    attachmentImageUrl: vi.fn().mockResolvedValue('blob:composer-thumb'),
    // PDF 缩略图要取的原始字节。jsdom 里画不出一页 PDF，所以让它拿不到——
    // 这一格于是落到「图标 + title 写名字」的兜底上，正是下面断言的那个形状。
    previewFileBytes: vi.fn().mockRejectedValue(new Error('no bytes under test')),
    uploadAttachment: vi.fn(),
    downloadFile: vi.fn(),
  }
})

import ChatPanel from '../ChatPanel.vue'

const sent: { payload: string }[] = []

function topic(id = 'topic-A'): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: null,
    title: '做一件事',
    kind: 'task',
    status: 'active',
    created_at: '2026-08-10T00:00:00Z',
  }
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

// 项目名册：只有人。房间里那位芝士来自话题名册（见上面的 mock）。
const members = [
  { user_handle: 'alice', name: 'Alice', role: 'lead' },
  { user_handle: 'bobby', name: '波比', role: 'member' },
  // 老项目名册上可能还坐着一行共用的芝士。房间自己有分身的时候它不进 @ 名单
  // （两行都叫「芝士」，@芝士 展开成哪一个纯看顺序）。
  { user_handle: 'cheese', name: '共用芝士', role: 'member', agent: true },
]

// 每话题草稿是模块级的（跨挂载留着，这正是它的用途），所以每条用例用自己的
// 话题——共用一个的话，上一条留在发件箱里的消息会在下一次挂载时重发。
function mountPanel(slots: Record<string, () => unknown> = {}, topicId?: string) {
  const vuetify = createVuetify({ components, directives })
  return render(ChatPanel, {
    props: { topic: topic(topicId), showComposer: true, hideHeader: true, members },
    slots,
    global: { plugins: [vuetify] },
  })
}

function composerBox(container: Element): HTMLTextAreaElement | null {
  return container.querySelector<HTMLTextAreaElement>('.composer textarea')
}

beforeAll(() => {
  // happy-dom 少两个东西，而 Vuetify 的浮层定位正好都要：visualViewport，以及
  // **裸的** devicePixelRatio（它不是 window.devicePixelRatio ?? 1，取不到就抛）。
  // 少了任何一个，tooltip 一弹就是一条未捕获异常。照这个文件既有的做法补，不是
  // 替——真有的时候不动它。
  if (!('devicePixelRatio' in globalThis)) {
    ;(globalThis as unknown as { devicePixelRatio: number }).devicePixelRatio = 1
  }
  if (!('visualViewport' in globalThis)) {
    ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = {
      offsetLeft: 0,
      offsetTop: 0,
      width: 1280,
      height: 800,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
    }
  }
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  // 一个会「连上」的假 socket，这样输入栏不是 disabled 状态，而且发出去的东西
  // 能被读到——这条用例问的正是「发出去的那条消息带没带 @芝士」。
  ;(globalThis as unknown as { WebSocket: unknown }).WebSocket = class {
    static OPEN = 1
    readyState = 1
    onopen: (() => void) | null = null
    constructor() {
      setTimeout(() => this.onopen?.(), 0)
    }
    close() {}
    send(payload: string) {
      sent.push({ payload })
    }
  }
})

beforeEach(() => {
  vi.clearAllMocks()
  sent.length = 0
})

describe('对话栏自己的输入栏', () => {
  it('offers editable starter drafts in an empty project and sends only on confirmation', async () => {
    const { container, rerender, getByRole, queryByRole } = mountPanel({}, 'starter-project')
    await rerender({ topic: { ...topic('starter-project'), kind: 'root' } })
    await flush()
    await fireEvent.click(getByRole('button', { name: '起草文档' }))
    const box = composerBox(container)!
    expect(box.value).toContain('@芝士')
    expect(box.value).toContain('文档')
    expect(document.activeElement).toBe(box)
    expect(sent).toHaveLength(0)
    expect(queryByRole('button', { name: '起草文档' })).toBeNull()
    await fireEvent.update(box, '@芝士 帮我起草一份项目介绍')
    await fireEvent.keyDown(box, { key: 'Enter' })
    await flush()
    expect(JSON.parse(sent[0].payload)).toMatchObject({
      content: '<@cheese-topica> 帮我起草一份项目介绍',
      summon: true,
    })
  })

  it('does not offer starter drafts in an archived project or a regular conversation', async () => {
    const { rerender, queryByRole } = mountPanel({}, 'starter-archived')
    await flush()
    expect(queryByRole('button', { name: '起草文档' })).toBeNull()
    await rerender({ topic: { ...topic('starter-archived'), kind: 'root', status: 'archived' } })
    await flush()
    expect(queryByRole('button', { name: '起草文档' })).toBeNull()
  })

  // 退休判据是「芝士在这个房间里说过话」，不是「房间里有没有东西」。新用户常常先
  // 自己说一句（而且往往忘了 @），那句话落在房间里，却没有任何一行字替他说明下一
  // 步该说什么——入口正是在这个时刻最该还在。
  it('keeps the starter drafts in a room 芝士 has not answered yet', async () => {
    const api = await import('../../api')
    vi.mocked(api.listBlocks).mockResolvedValueOnce({
      data: [
        {
          id: 'm1',
          topic_id: 'starter-talked',
          kind: 'message',
          content: '我打算把这学期的课程材料整理成一份大纲',
          author: 'alice',
          author_type: 'human',
          created_at: '2026-09-16T10:00:00Z',
        } as never,
      ],
      total: 1,
      has_more: false,
      oldest_id: 'm1',
    })
    const { rerender, queryByRole } = mountPanel({}, 'starter-talked')
    await rerender({ topic: { ...topic('starter-talked'), kind: 'root' } })
    await flush()
    expect(queryByRole('button', { name: '起草文档' })).toBeTruthy()
  })

  it('retires the starter drafts once 芝士 has spoken in the room', async () => {
    const api = await import('../../api')
    vi.mocked(api.listBlocks).mockResolvedValueOnce({
      data: [
        {
          id: 'm2',
          topic_id: 'starter-answered',
          kind: 'message',
          content: '好，我先把材料归拢一下，再跟你确认大纲的结构。',
          author: 'cheese-topica',
          author_type: 'ai',
          created_at: '2026-09-16T10:01:00Z',
        } as never,
      ],
      total: 1,
      has_more: false,
      oldest_id: 'm2',
    })
    const { rerender, queryByRole } = mountPanel({}, 'starter-answered')
    await rerender({ topic: { ...topic('starter-answered'), kind: 'root' } })
    await flush()
    expect(queryByRole('button', { name: '起草文档' })).toBeNull()
  })

  it('previews a document and sends its uploaded path', async () => {
    const api = await import('../../api')
    vi.mocked(api.uploadAttachment).mockResolvedValue({
      path: 'uploads/id/需求 文档.pdf',
      mime: 'application/pdf',
    })
    const { container } = mountPanel({}, 'topic-files')
    await flush()
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')!
    expect(input.accept).toBe('')
    const file = new File(['%PDF'], '需求 文档.pdf', { type: 'application/pdf' })
    await fireEvent.change(input, { target: { files: [file] } })
    await flush()
    // 每一格都是同一个块：左边那个方格说明它是什么，右边一直写着名字。PDF 在
    // 方格里画首页——发之前要确认的是「附的是哪一份」，光有名字答不了。
    const card = container.querySelector('.att-strip .att-card')!
    expect(card.querySelector('.att-card__name')?.textContent).toBe('需求 文档.pdf')
    expect(card.querySelector('canvas')).toBeTruthy()
    expect(container.querySelector('.att-strip img')).toBeNull()
    await fireEvent.click(container.querySelector('[title="发送"]')!)
    await flush()
    expect(JSON.parse(sent[0].payload).attachments).toEqual([
      { path: 'uploads/id/需求 文档.pdf', mime: 'application/pdf' },
    ])
  })

  // 一条消息里常常同时有图片和文档。两种形状并排是两样不相干的东西，而上传完成
  // 的那一刻形状一换，整条会跳——所以图片和一份 .docx 占的是同一个块，区别只在
  // 方格里画的是缩略图还是这个类型的图标。名字两种都写着。
  it('gives an image and a document the same block, and always the name', async () => {
    const api = await import('../../api')
    vi.mocked(api.uploadAttachment)
      .mockResolvedValueOnce({ path: 'uploads/id/截图.png', mime: 'image/png' })
      .mockResolvedValueOnce({
        path: 'uploads/id/Writing替换词.docx',
        mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      })
    const { container } = mountPanel({}, 'topic-mixed')
    await flush()
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')!
    await fireEvent.change(input, {
      target: {
        files: [
          new File(['png'], '截图.png', { type: 'image/png' }),
          new File(['doc'], 'Writing替换词.docx', {
            type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
          }),
        ],
      },
    })
    await flush()

    const cards = Array.from(container.querySelectorAll('.att-strip .att-card'))
    expect(cards.map((c) => c.querySelector('.att-card__name')?.textContent)).toEqual([
      '截图.png',
      'Writing替换词.docx',
    ])
    // 同一个块、同一个方格；里面一个是图，一个是这个类型的图标。
    expect(cards.every((c) => c.querySelector('.att-face'))).toBe(true)
    expect(cards[0].querySelector('img')).toBeTruthy()
    expect(cards[1].querySelector('img')).toBeNull()
    expect(cards[1].querySelector('.mdi-file-word-outline')).toBeTruthy()
  })

  // 名字在块边缘就截断了，所以悬停是拿到全名的唯一出口——它得真的弹出来。原来
  // 用的是 title 属性：系统气泡要鼠标停住约一秒才出现，读者多半以为没有。
  it('gives the whole filename on hover', async () => {
    const api = await import('../../api')
    vi.mocked(api.uploadAttachment).mockResolvedValue({
      path: 'uploads/id/一份名字长得放不进那一格的说明文档.docx',
      mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    })
    const { container } = mountPanel({}, 'topic-hover')
    await flush()
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')!
    await fireEvent.change(input, {
      target: {
        files: [
          new File(['d'], '一份名字长得放不进那一格的说明文档.docx', {
            type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
          }),
        ],
      },
    })
    await flush()

    const card = container.querySelector('.att-strip .att-card')!
    expect(card.getAttribute('title')).toBeNull()
    await fireEvent.mouseEnter(card)
    await flush()
    expect(document.querySelector('.v-tooltip .v-overlay__content')?.textContent?.trim()).toBe(
      '一份名字长得放不进那一格的说明文档.docx'
    )
  })

  // 输入框里那张图曾经是一张裂图：它被挂上了一个只认 Authorization 头的地址，而
  // 浏览器发图片请求时带不了头。缩略图现在跟消息里的图走同一条路——先取字节。
  it('shows a pending image from its bytes, not from the address that only trusts a header', async () => {
    const api = await import('../../api')
    vi.mocked(api.uploadAttachment).mockResolvedValue({
      path: 'uploads/id/截图.png',
      mime: 'image/png',
    })
    const { container } = mountPanel({}, 'topic-image')
    await flush()
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')!
    const file = new File(['png'], '截图.png', { type: 'image/png' })
    await fireEvent.change(input, { target: { files: [file] } })
    await flush()
    const img = container.querySelector<HTMLImageElement>('.att-strip img')!
    expect(img.getAttribute('src')).toBe('blob:composer-thumb')
    expect(img.getAttribute('src')).not.toContain('/attachments/raw')
    expect(api.attachmentImageUrl).toHaveBeenCalledWith('topic-image', 'uploads/id/截图.png')
  })

  it('renders a received document with a download action', async () => {
    const api = await import('../../api')
    vi.mocked(api.listBlocks).mockResolvedValueOnce({
      data: [
        {
          id: 'doc-1',
          topic_id: 'topic-download',
          kind: 'attachment',
          content: 'uploads/id/report.docx',
          mime_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
          author: 'alice',
          author_type: 'human',
          created_at: '2026-09-07T12:00:00Z',
        } as never,
      ],
      total: 1,
      has_more: false,
      oldest_id: 'doc-1',
    })
    const { container } = mountPanel({}, 'topic-download')
    await flush()
    const download = container.querySelector('[title="下载 report.docx"]')
    expect(download).toBeTruthy()
    await fireEvent.click(download!)
    expect(api.downloadFile).toHaveBeenCalledWith('', 'report.docx')
  })

  it('输入栏就在对话栏里，不再横跨到工作面板底下', async () => {
    const { container } = mountPanel()
    await flush()

    const box = composerBox(container)
    expect(box, '对话栏里没有输入栏').toBeTruthy()
    expect(box!.closest('.chat'), '输入栏跑到对话栏外面去了').toBeTruthy()
  })

  it('话题自己的 chips 从外面交进来——输入栏不必认识算力池', async () => {
    const { container } = mountPanel({
      'composer-chips': () => h('span', { class: 'probe-chip' }, '已归档'),
    })
    await flush()

    const chip = container.querySelector('.probe-chip')
    expect(chip, 'composer-chips 插槽没渲染').toBeTruthy()
    expect(chip!.closest('.composer'), 'chips 没落在输入栏那一行里').toBeTruthy()
  })

  it('@ 了芝士的那条消息才召唤它', async () => {
    const { container } = mountPanel()
    await flush()

    const box = composerBox(container)!
    box.focus()
    await fireEvent.update(box, '看看这个')
    await fireEvent.keyDown(box, { key: 'Enter' })
    await flush()
    expect(sent.map((s) => JSON.parse(s.payload).summon)).toEqual([false])

    box.focus()
    await fireEvent.update(box, '@芝士 再看看')
    await fireEvent.keyDown(box, { key: 'Enter' })
    await flush()

    expect(sent.map((s) => JSON.parse(s.payload).summon)).toEqual([false, true])
    // 发出去的是规范形式，和 @ 一个人完全一样。
    expect(JSON.parse(sent[1].payload).content).toBe('<@cheese-topica> 再看看')
  })

  // 线上真实形状：项目名册里只有人，芝士只在话题名册上。名单少了它，@ 补全里
  // 就没有它，而 @ 它是叫它干活的唯一方式——整条路就断了。
  it('项目名册里没有芝士，@ 补全里照样有', async () => {
    const { container } = mountPanel({}, 'topic-C')
    await flush()

    const box = composerBox(container)!
    box.focus()
    await fireEvent.update(box, '@')
    await flush()

    const menu = container.querySelector('.mention-menu')
    expect(menu, '@ 补全没弹出来').toBeTruthy()
    expect(menu!.textContent).toContain('芝士')
    expect(menu!.textContent).toContain('AI 队友')
  })

  // 老话题的名册里可能没有芝士的座位（座位是后来才有的）。那种房间里，项目名册上
  // 那行共用的芝士得顶上，否则这个话题永远叫不动它。
  it('房间名册里没有芝士时，退回项目名册上那一行', async () => {
    const api = await import('../../api')
    vi.mocked(api.listTopicMembers).mockResolvedValueOnce({
      data: [{ id: 'm1', member_handle: 'alice', name: 'Alice', role: 'owner', agent: false }],
      total: 1,
    } as Awaited<ReturnType<typeof api.listTopicMembers>>)

    const { container } = mountPanel({}, 'topic-D')
    await flush()

    const box = composerBox(container)!
    box.focus()
    await fireEvent.update(box, '@')
    await flush()
    expect(container.querySelector('.mention-menu')!.textContent).toContain('共用芝士')
  })

  // 「打一个 @ 然后回车」是这个输入框里最短的一条路，而它当时通向 @all——把整个
  // 话题的所有人叫起来。最短的路得通向最常见的意图：交给芝士。
  it('@ 之后直接回车，选中的是芝士，不是群播', async () => {
    const { container } = mountPanel({}, 'topic-E')
    await flush()

    const box = composerBox(container)!
    box.focus()
    await fireEvent.update(box, '@')
    await flush()
    await fireEvent.keyDown(box, { key: 'Enter' })
    await flush()

    expect(box.value).toBe('@芝士 ')
    // 挑完人就是接着打字的时刻——焦点不该被那次回车带走。
    expect(document.activeElement).toBe(box)
  })

  // 「交给芝士」这颗按钮唯一被允许做的事，就是替你打那五个字。它自己不存状态：
  // 一个能和正文说不一样的话的开关（亮着、正文里却没有 @），会让「这条到底算不
  // 算叫了它」变成没人答得上来的问题——上一版正是因为这个被整颗删掉的。
  it('点「交给芝士」把 @ 写进正文，再点一下拿掉', async () => {
    const { container, getByRole } = mountPanel({}, 'topic-summon-btn')
    await flush()

    const box = composerBox(container)!
    await fireEvent.update(box, '看看这个')
    const btn = getByRole('button', { name: /交给芝士/ })
    expect(btn.getAttribute('aria-pressed')).toBe('false')

    await fireEvent.click(btn)
    expect(box.value, '按钮没把 @ 写进正文——那它就是个只有它自己知道的开关').toBe('@芝士 看看这个')
    expect(btn.getAttribute('aria-pressed')).toBe('true')

    await fireEvent.click(btn)
    expect(box.value).toBe('看看这个')
    expect(btn.getAttribute('aria-pressed')).toBe('false')
  })

  it('手打 @芝士，按钮自己亮起来', async () => {
    const { container, getByRole } = mountPanel({}, 'topic-summon-mirror')
    await flush()

    const box = composerBox(container)!
    const btn = getByRole('button', { name: /交给芝士/ })
    expect(btn.getAttribute('aria-pressed')).toBe('false')
    await fireEvent.update(box, '@芝士 看看这个')
    expect(btn.getAttribute('aria-pressed'), '正文里 @ 了它，按钮却没亮——两边说的不是同一件事').toBe('true')
  })

  // 切进一个房间的头几百毫秒里，房间名册还没到。那一瞬间名单里唯一带 AI 标记的是
  // **项目**名册上那行共用的芝士——照它把 @ 写进正文，写出来的是另一个 handle：
  // 消息照发、房间里会动的那位不动，而时间线上那条消息写着「叫了它」。
  it('房间名册还没到的时候，按钮不认项目名册上那行共用的芝士', async () => {
    const api = await import('../../api')
    let release!: () => void
    vi.mocked(api.listTopicMembers).mockReturnValueOnce(
      new Promise((resolve) => {
        release = () =>
          resolve({
            data: [
              { id: 'm1', member_handle: 'alice', name: 'Alice', role: 'owner', agent: false },
              { id: 'm2', member_handle: 'cheese-topicL', name: '芝士', role: 'member', agent: true },
            ],
            total: 2,
          } as Awaited<ReturnType<typeof api.listTopicMembers>>)
      })
    )

    const { container, getByRole } = mountPanel({}, 'topic-late-roster')
    await flush()

    const before = getByRole('button', { name: /交给/ }) as HTMLButtonElement
    expect(before.disabled, '房间名册还没到，按钮却已经能点了——这时候它认的是项目名册上那行共用的芝士').toBe(true)

    release()
    await flush()

    const box = composerBox(container)!
    await fireEvent.update(box, '看看这个')
    await fireEvent.click(getByRole('button', { name: /交给芝士/ }))
    expect(box.value).toBe('@芝士 看看这个')

    box.focus()
    await fireEvent.keyDown(box, { key: 'Enter' })
    await flush()
    // 时间线上那条消息里的 @ 得指向**这个房间**那位，不是项目名册上共用的那位。
    expect(JSON.parse(sent[0].payload)).toMatchObject({
      content: '<@cheese-topicL> 看看这个',
      summon: true,
    })
  })

  // ⌘/Ctrl+Enter 是键盘上的同一个入口。它把 @ 写进正文再发，而不是在帧上偷偷把
  // summon 置真：时间线上那条消息得自己说明它叫了谁，否则读的人看到的是一条谁也
  // 没 @ 的消息、芝士却动了。
  it('⌘/Ctrl+Enter 不用打 @ 也召唤，且发出去的正文里看得见那个 @', async () => {
    const { container } = mountPanel({}, 'topic-summon-key')
    await flush()

    const box = composerBox(container)!
    box.focus()
    await fireEvent.update(box, '看看这个')
    await fireEvent.keyDown(box, { key: 'Enter', metaKey: true })
    await flush()

    expect(JSON.parse(sent[0].payload)).toMatchObject({
      content: '<@cheese-topica> 看看这个',
      summon: true,
    })
  })

  // 空输入框上按下这个快捷键，最坏的结果是发出一条光秃秃的 @——它把芝士叫起来，
  // 而它手上一句话都没有。
  it('输入框是空的时候，⌘/Ctrl+Enter 什么也不发', async () => {
    const { container } = mountPanel({}, 'topic-summon-empty')
    await flush()

    const box = composerBox(container)!
    box.focus()
    await fireEvent.keyDown(box, { key: 'Enter', metaKey: true })
    await flush()

    expect(sent).toHaveLength(0)
  })

  // 同一条规矩对快捷键也成立：名册还没到就别假装召唤，那一下发出去的消息里没有
  // 任何能解析成 handle 的东西，芝士不会动，而按的人以为自己叫了它。
  it('还不知道芝士是谁的时候，⌘/Ctrl+Enter 只是普通发送', async () => {
    const api = await import('../../api')
    vi.mocked(api.listTopicMembers).mockResolvedValue({
      data: [{ id: 'm1', member_handle: 'alice', name: 'Alice', role: 'owner', agent: false }],
      total: 1,
    } as Awaited<ReturnType<typeof api.listTopicMembers>>)

    const vuetify = createVuetify({ components, directives })
    const { container } = render(ChatPanel, {
      props: {
        topic: topic('topic-summon-unknown'),
        showComposer: true,
        hideHeader: true,
        // 项目名册上也没有芝士那一行——这个房间此刻确实不知道它是谁。
        members: [{ user_handle: 'alice', name: 'Alice', role: 'lead' }],
      },
      global: { plugins: [vuetify] },
    })
    await flush()

    const box = composerBox(container)!
    box.focus()
    await fireEvent.update(box, '看看这个')
    await fireEvent.keyDown(box, { key: 'Enter', metaKey: true })
    await flush()

    expect(JSON.parse(sent[0].payload)).toMatchObject({ content: '看看这个', summon: false })
  })

  it('@ 一个人不会把芝士叫起来', async () => {
    const { container } = mountPanel({}, 'topic-B')
    await flush()

    const box = composerBox(container)!
    box.focus()
    await fireEvent.update(box, '@波比 你看下')
    await fireEvent.keyDown(box, { key: 'Enter' })
    await flush()

    expect(sent.map((s) => JSON.parse(s.payload).summon)).toEqual([false])
  })
})
