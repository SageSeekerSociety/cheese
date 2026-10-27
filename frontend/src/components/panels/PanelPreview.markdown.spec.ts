/**
 * markdown 那一支的指认：位置来自渲染出来的正文（哪个标题之下、原文、两侧各 32 字），
 * 而屏幕上换了另一份文件时这条指认必须自己撤掉——它说的是文件 A 里的位置，发出去时
 * 名字写的却是文件 B。
 *
 * 三分支走的是同一套事件（`PanelPreview.document.spec.ts` 里另有端到端的一条），
 * 这里只盯这两条规则，所以直接挂 `PanelPreviewView`、把 props 递进去。
 */
import type { Ref } from 'vue'

import { computed, reactive, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import PanelPreviewView from './PanelPreviewView.vue'

import { setLocale } from '@/i18n'
import { previewBundles } from '@/test/panelBundles'

vi.mock('./preview/RevisionList.vue', () => ({ default: { template: '<div />' } }))

const SOURCE = '# 配置\n\n失败以后重试 3 次。\n'

function props(path = 'output/说明.md', content = SOURCE) {
  return {
    ...previewBundles(),
    topicId: 'room',
    projectId: 'project',
    frameName: 'frame',
    loading: false,
    refreshing: false,
    previewFile: {
      path,
      content,
      version: 'v1',
      bytes: content.length,
      binary: false,
      too_large: false,
    },
    previewMime: 'text/markdown',
    previewNamed: true,
    previewUrl: null,
    previewAppNote: '',
    previewTunnelUp: false,
    previewNamedPath: path,
    previewError: null,
    previewReadError: null,
    documentSuffix: 'md',
    documentType: { label: 'Markdown', icon: 'mdi-language-markdown-outline', view: 'markdown' as const },
    documentName: '说明.md',
    isImageArtifact: false,
    downloadError: '',
    docBytes: null,
    docLoading: false,
    docError: '',
    docRendererMissing: false,
    // 这一份文件在屏幕上的身份：发出去的结构化引用要带上它（path/source/version）。
    docIdentity: { topicId: 'room', path, taskId: null, source: 'live' as const, version: 'v1' },
  }
}

beforeEach(() => setLocale('zh-CN'))
afterEach(() => {
  cleanup()
  window.getSelection()?.removeAllRanges()
})

function mount(overrides: Record<string, unknown> = {}) {
  return render(PanelPreviewView, {
    props: { ...props(), ...overrides },
    global: {
      plugins: [createVuetify({ components, directives })],
      stubs: { VBtn: { template: '<button><slot /></button>' }, VIcon: true, VSpacer: true, VAlert: true },
    },
  })
}

/** 正文第一段的字。正文的读法第一次用到才加载，画出来要等一下。 */
function firstLine(md: HTMLElement): Promise<ChildNode> {
  return waitFor(() => md.querySelector('p')!.firstChild!, { timeout: 5000 })
}

it('选中一段原文，定位条上写的是它在哪一节、原文是什么', async () => {
  const ui = mount()
  const md = ui.getByTestId('markdown')
  const node = await firstLine(md)
  const range = document.createRange()
  range.setStart(node, 4)
  range.setEnd(node, 10)
  const selection = window.getSelection()!
  selection.removeAllRanges()
  selection.addRange(range)
  await fireEvent.mouseUp(md)

  expect(ui.getByText('标题「配置」')).toBeTruthy()
  expect(ui.getByText('重试 3 次')).toBeTruthy()
  // 选中只是把输入框叫出来，还没有一句话要说，所以什么都没有发出去。
  expect(ui.emitted().locate).toBeUndefined()
})

it('屏幕上换成另一份文件时，这条指认自己撤掉', async () => {
  const ui = mount()
  const md = ui.getByTestId('markdown')
  const node = await firstLine(md)
  const range = document.createRange()
  range.setStart(node, 4)
  range.setEnd(node, 10)
  const selection = window.getSelection()!
  selection.removeAllRanges()
  selection.addRange(range)
  await fireEvent.mouseUp(md)
  expect(ui.queryByPlaceholderText('就这一处说点什么')).toBeTruthy()

  await ui.rerender(props('output/另一份.md', '# 配置\n\n另一句话。\n'))

  expect(ui.queryByPlaceholderText('就这一处说点什么')).toBeNull()
  expect(ui.emitted().locate).toBeUndefined()
})

/** 在「配置」那一节里选中「重试 3 次」这一段，和上面两条一样。
 *
 *  `from`／`to` 换一对数就是另一处（同一段正文里换一段）。「读者换了一处」那两条要
 *  的是「另一处」这个事实，用同一段重选一遍只是又指了同一处，测不到那个场景。 */
async function selectPassage(ui: ReturnType<typeof mount>, from = 4, to = 10) {
  const md = ui.getByTestId('markdown')
  const node = await firstLine(md)
  const range = document.createRange()
  range.setStart(node, from)
  range.setEnd(node, to)
  const selection = window.getSelection()!
  selection.removeAllRanges()
  selection.addRange(range)
  await fireEvent.mouseUp(md)
}

it('选中一段之后「评论」这条出口把它交给评论，而不是发进对话', async () => {
  // 这一处指认不该被当成「叫队友改」：同一句话，读者点「评论」就是留一条挂在这儿的评论。
  // 挂在面板这一层，因为出事的就是这里——指认条把位置和原文递给评论栏那一段接线。
  const posted: { content: string; quote: string | null; anchor: unknown }[] = []
  const submit = vi.fn().mockReturnValue(true)
  const open = ref(false)
  const bundle = {
    target: computed(() => ({ topicId: 'room', path: 'output/说明.md', version: 'v1' })),
    open,
    toggle: () => (open.value = !open.value),
    author: 'me',
    threads: {
      state: reactive({ threads: [], activity: {}, errors: {}, busy: false, unknown: null }),
      actions: {},
      refresh: async () => {},
    },
    post: async (payload: { content: string; quote: string | null; anchor: unknown }) => void posted.push(payload),
    nameOf: (h: string) => h,
    avatarOf: () => '',
  }
  const ui = mount({ submitQuestion: submit, comments: bundle })
  await selectPassage(ui)
  await fireEvent.update(ui.getByPlaceholderText('就这一处说点什么'), '这里数字不对')
  // 两条出口都在指认条上；页面另有一颗开评论栏的按钮，也叫「评论」，所以按条找。
  const bar = ui.getByPlaceholderText('就这一处说点什么').closest('.locator')!
  const commentBtn = [...bar.querySelectorAll('button')].find((b) => b.textContent?.trim() === '评论')
  expect(commentBtn).toBeTruthy()
  await fireEvent.click(commentBtn!)

  await waitFor(() => expect(posted).toHaveLength(1))
  expect(posted[0].content).toBe('这里数字不对')
  // 发的是评论，不是对话里那句「在 X 的 Y：…」。
  expect(submit).not.toHaveBeenCalled()
  expect(ui.emitted().locate ?? []).toHaveLength(0)
  // 这一处的原文跟着进去，评论才落在选中的那几个字上。
  expect(posted[0].quote).toContain('重试 3 次')
  // 锚点也一起进去，而且是带这一版文件身份的那个：评论栏靠 `anchor.version` 判这一串
  // 评论「还在原处」还是「原文已改动」。丢了它，这条评论就再也说不清挂在哪一版上。
  expect(posted[0].anchor).toMatchObject({
    kind: 'text-range',
    path: 'output/说明.md',
    version: 'v1',
    text: '重试 3 次',
  })
})

it('评论还没回来时再点一次，不会挂出第二条一样的评论', async () => {
  // 指认条那个 busy 只反映页上钉一处那条路（`pageLocator.sending`），评论这条它照不到；
  // 所以这条路自己挡一道。发一条要等一会儿，这期间连点两下是很正常的动作。
  let release: () => void = () => {}
  const posted: unknown[] = []
  const open = ref(false)
  const bundle = {
    target: computed(() => ({ topicId: 'room', path: 'output/说明.md', version: 'v1' })),
    open,
    toggle: () => (open.value = !open.value),
    author: 'me',
    threads: {
      state: reactive({ threads: [], activity: {}, errors: {}, busy: false, unknown: null }),
      actions: {},
      refresh: async () => {},
    },
    post: (payload: unknown) =>
      new Promise<void>((resolve) => {
        posted.push(payload)
        release = resolve
      }),
    nameOf: (h: string) => h,
    avatarOf: () => '',
  }
  const ui = mount({ comments: bundle })
  await selectPassage(ui)
  await fireEvent.update(ui.getByPlaceholderText('就这一处说点什么'), '这里数字不对')
  const bar = ui.getByPlaceholderText('就这一处说点什么').closest('.locator')!
  const commentBtn = [...bar.querySelectorAll('button')].find((b) => b.textContent?.trim() === '评论')!
  await fireEvent.click(commentBtn)
  await fireEvent.click(commentBtn)
  expect(posted).toHaveLength(1)
  release()
})

it('资料库原件挂不了评论：条上不露「评论」那一条出口', async () => {
  // `comments.target` 对 `library/` 和 `uploads/` 是 null（那不是这个房间的文件），
  // 接线要把这件事传到条上。接错了读者会看到「评论」，按下去评论那一包悄悄返回、
  // 一条线程都不挂，还不报错。
  const bundle = {
    target: computed(() => null),
    open: ref(false),
    toggle: () => {},
    author: 'me',
    threads: {
      state: reactive({ threads: [], activity: {}, errors: {}, busy: false, unknown: null }),
      actions: {},
      refresh: async () => {},
    },
    post: async () => {},
    nameOf: (h: string) => h,
    avatarOf: () => '',
  }
  const ui = mount({ comments: bundle })
  await selectPassage(ui)
  const bar = ui.getByPlaceholderText('就这一处说点什么').closest('.locator')!
  const labels = [...bar.querySelectorAll('button')].map((b) => b.textContent?.trim())
  expect(labels).toContain('发送')
  expect(labels).not.toContain('评论')
})

it('评论挂不上去时，条上说出来，不是点了没反应', async () => {
  // 这条出口不经过评论栏，栏自己那句错误提示收不到；不说的话读者按了「评论」看起来
  // 什么都没发生，而输入框里的字还在，他多半会再按一次。
  const open = ref(false)
  const bundle = {
    target: computed(() => ({ topicId: 'room', path: 'output/说明.md', version: 'v1' })),
    open,
    toggle: () => (open.value = !open.value),
    author: 'me',
    threads: {
      state: reactive({ threads: [], activity: {}, errors: {}, busy: false, unknown: null }),
      actions: {},
      refresh: async () => {},
    },
    post: async () => {
      throw new Error('评论失败')
    },
    nameOf: (h: string) => h,
    avatarOf: () => '',
  }
  const ui = mount({ comments: bundle })
  await selectPassage(ui)
  const input = ui.getByPlaceholderText('就这一处说点什么')
  await fireEvent.update(input, '这里数字不对')
  const bar = input.closest('.locator')!
  const commentBtn = [...bar.querySelectorAll('button')].find((b) => b.textContent?.trim() === '评论')!
  await fireEvent.click(commentBtn)

  const alert = await waitFor(() => {
    const found = bar.querySelector('[role="alert"]')
    expect(found).toBeTruthy()
    return found as HTMLElement
  })
  expect(alert.textContent).toContain('评论失败')
  // 指认和那句话都还在：读者能就地再试，不用重新选一次、重打一遍。
  expect((ui.getByPlaceholderText('就这一处说点什么') as HTMLInputElement).value).toBe('这里数字不对')
})

it('评论发着的时候读者按了取消：回来时不再替他打开评论栏', async () => {
  // 他刚按了取消，明确表示不想留在这儿；这时把评论栏弹出来会盖住他正在做的事。
  // 评论本身已经挂上去了（那条请求没有中断），所以只是别动他的界面。
  let release: () => void = () => {}
  let posted = 0
  const open = ref(false)
  const bundle = {
    target: computed(() => ({ topicId: 'room', path: 'output/说明.md', version: 'v1' })),
    open,
    toggle: () => (open.value = !open.value),
    author: 'me',
    threads: {
      state: reactive({ threads: [], activity: {}, errors: {}, busy: false, unknown: null }),
      actions: {},
      refresh: async () => {},
    },
    post: () =>
      new Promise<void>((resolve) => {
        posted += 1
        release = resolve
      }),
    nameOf: (h: string) => h,
    avatarOf: () => '',
  }
  const ui = mount({ comments: bundle })
  await selectPassage(ui)
  const input = ui.getByPlaceholderText('就这一处说点什么')
  await fireEvent.update(input, '这里数字不对')
  const bar = input.closest('.locator')!
  const commentBtn = [...bar.querySelectorAll('button')].find((b) => b.textContent?.trim() === '评论')!
  await fireEvent.click(commentBtn)
  // 先确认这一按真的发出去了：没有这一步，一个「按了评论什么都不发」的退化也能让下面绿。
  await waitFor(() => expect(posted).toBe(1))
  // 还没回来，读者按了条上的取消。
  const closeBtn = [...bar.querySelectorAll('button')].at(-1)!
  await fireEvent.click(closeBtn)
  release()
  await Promise.resolve()
  await Promise.resolve()
  expect(open.value).toBe(false)
})

it('换了另一处指认，上一次那句「评论失败」不跟着过来', async () => {
  // 读者发失败之后，多半会去选另一段再来一次。上一处那句话说的是上一处，留着会让人
  // 以为新指的这处也失败了；而撤掉指认的路不止一条（条上的取消、Escape、换文件），
  // 所以清它挂在「指认变了」这一件事上，而不是挂在某一个按钮上。
  const open = ref(false)
  const bundle = {
    target: computed(() => ({ topicId: 'room', path: 'output/说明.md', version: 'v1' })),
    open,
    toggle: () => (open.value = !open.value),
    author: 'me',
    threads: {
      state: reactive({ threads: [], activity: {}, errors: {}, busy: false, unknown: null }),
      actions: {},
      refresh: async () => {},
    },
    post: async () => {
      throw new Error('评论失败')
    },
    nameOf: (h: string) => h,
    avatarOf: () => '',
  }
  const ui = mount({ comments: bundle })
  await selectPassage(ui)
  const input = ui.getByPlaceholderText('就这一处说点什么')
  await fireEvent.update(input, '这里数字不对')
  const bar = input.closest('.locator')!
  const commentBtn = [...bar.querySelectorAll('button')].find((b) => b.textContent?.trim() === '评论')!
  await fireEvent.click(commentBtn)
  await waitFor(() => expect(bar.querySelector('[role="alert"]')).toBeTruthy())

  // 直接另选一处（不按取消，而且换的是另一段）：条上那颗取消本来就清这句话，所以只有
  // 「指认自己变了就清」才盖得住这条路——上一版正是只清了取消那条，这里会留着上一处的失败。
  await selectPassage(ui, 0, 4)
  expect(ui.getByPlaceholderText('就这一处说点什么').closest('.locator')!.querySelector('[role="alert"]')).toBeNull()
})

it('发失败时读者已经换了一处：那句失败不落在新的一处上', async () => {
  // 失败是发生在 await 之后的，这期间读者完全可能已经去指别处了。这句话说的是上一处，
  // 落在新的这一处下面，会让人以为新指的这处也发失败了。
  let reject: (e: Error) => void = () => {}
  let posted = 0
  const open = ref(false)
  const bundle = {
    target: computed(() => ({ topicId: 'room', path: 'output/说明.md', version: 'v1' })),
    open,
    toggle: () => (open.value = !open.value),
    author: 'me',
    threads: {
      state: reactive({ threads: [], activity: {}, errors: {}, busy: false, unknown: null }),
      actions: {},
      refresh: async () => {},
    },
    post: () =>
      new Promise<void>((_resolve, rej) => {
        posted += 1
        reject = rej
      }),
    nameOf: (h: string) => h,
    avatarOf: () => '',
  }
  const ui = mount({ comments: bundle })
  await selectPassage(ui)
  const input = ui.getByPlaceholderText('就这一处说点什么')
  await fireEvent.update(input, '这里数字不对')
  const commentBtn = [...input.closest('.locator')!.querySelectorAll('button')].find(
    (b) => b.textContent?.trim() === '评论'
  )!
  await fireEvent.click(commentBtn)
  await waitFor(() => expect(posted).toBe(1))

  // 还没回来，读者就换了一处——真的换一处（另一段文字），不是把同一段再选一遍。
  await selectPassage(ui, 0, 4)
  reject(new Error('评论失败'))
  await Promise.resolve()
  await Promise.resolve()
  expect(ui.getByPlaceholderText('就这一处说点什么').closest('.locator')!.querySelector('[role="alert"]')).toBeNull()
})

it('选中一段发出去时带的是带文件身份的 text-range 引用', async () => {
  const submit = vi.fn().mockReturnValue(true)
  const ui = mount({ submitQuestion: submit })
  await selectPassage(ui)
  await fireEvent.update(ui.getByPlaceholderText('就这一处说点什么'), '改这句')
  await fireEvent.click(ui.getByText('发送'))

  const request = submit.mock.calls[0]?.[0]
  expect(request).toMatchObject({ topicId: 'room', intent: 'ask-agent', content: '改这句' })
  expect(request.quotedContext).toMatchObject({
    kind: 'text-range',
    path: 'output/说明.md',
    source: 'live',
    version: 'v1',
    task_id: null,
    text: '重试 3 次',
    heading: '配置',
  })
  // 两侧各带一段前后文，受话人靠它分辨同一句话的哪一处出现。
  expect(request.quotedContext.prefix.endsWith('失败以后')).toBe(true)
  expect(request.quotedContext.suffix.startsWith('。')).toBe(true)
  // 走的是结构化那条路，不再额外拼一句话。
  expect(ui.emitted().locate).toBeUndefined()
})

it('拿不到文件身份时退回拼一句话那条老路', async () => {
  const submit = vi.fn().mockReturnValue(true)
  const ui = mount({ submitQuestion: submit, docIdentity: null })
  await selectPassage(ui)
  await fireEvent.update(ui.getByPlaceholderText('就这一处说点什么'), '改这句')
  await fireEvent.click(ui.getByText('发送'))

  expect(submit).not.toHaveBeenCalled()
  const [payload] = ui.emitted().locate as { message: string }[][]
  expect(payload[0].message).toContain('重试 3 次')
  expect(payload[0].message).toContain('改这句')
})

it('支持把多处指认先加入待发列表，再一键合并成一条消息发出', async () => {
  const ui = mount()
  await selectPassage(ui, 4, 10)
  await fireEvent.update(ui.getByPlaceholderText('就这一处说点什么'), '改成 5 次')
  await fireEvent.click(ui.getByText('加入待发'))

  expect(ui.emitted().locate).toBeUndefined()
  expect(ui.getByText('待发批注 1 处')).toBeTruthy()

  await selectPassage(ui, 0, 4)
  await fireEvent.update(ui.getByPlaceholderText('就这一处说点什么'), '补充超时原因')
  await fireEvent.click(ui.getByText('加入待发'))

  expect(ui.getByText('待发批注 2 处')).toBeTruthy()
  await fireEvent.click(ui.getByText('全部发送（2）'))

  const [payload] = ui.emitted().locate as { message: string }[][]
  expect(payload[0].message).toContain('重试 3 次')
  expect(payload[0].message).toContain('改成 5 次')
  expect(payload[0].message).toContain('失败以后')
  expect(payload[0].message).toContain('补充超时原因')
})

it('支持在 Markdown 渲染视图与看原文之间切换，换文件时恢复为渲染视图', async () => {
  const ui = mount()
  const md = ui.getByTestId('markdown')
  await firstLine(md)
  expect(ui.queryByTestId('markdown-source')).toBeNull()

  await fireEvent.click(ui.getByText('看原文'))
  expect(ui.getByTestId('markdown-source').textContent).toContain('# 配置')
  expect(ui.queryByTestId('markdown')).toBeNull()

  await ui.rerender(props('output/另一份.md', '# 另一节\n\n正文。\n'))
  expect(ui.queryByTestId('markdown-source')).toBeNull()
  expect(ui.getByTestId('markdown')).toBeTruthy()
})

// ---- 已有的评论画在文件上 ----
//
// 哪几条画得出来，是在面板这一层定的：锚点里那段原文要找得回来，而且记的得是**这一版**。
// 找位置那一步在 `markdownAnchors` 里（那一份自己有一串用例），这里盯的是选哪些锚点、
// 画出来之后点它开到哪一条。

const TEXT_RANGE = {
  kind: 'text-range',
  path: 'output/说明.md',
  source: 'live',
  version: 'v1',
  task_id: null,
  text: '重试 3 次',
  heading: '配置',
  prefix: '失败以后',
  suffix: '。',
}

/** 评论那一包：几条线程各挂一个锚点，栏本身关着（读者点标记才该把它打开）。 */
function commentsWithOpen(
  anchors: (Record<string, unknown> | null)[],
  open: Ref<boolean>,
  resolved: readonly number[] = []
) {
  return {
    target: computed(() => ({ topicId: 'room', path: 'output/说明.md', version: 'v1' })),
    open,
    toggle: () => (open.value = !open.value),
    author: 'me',
    threads: {
      state: reactive({
        threads: anchors.map((anchor, at) => ({
          comment: {
            id: `c${at + 1}`,
            author: 'li',
            content: `第 ${at + 1} 条评论`,
            anchor_quote: null,
            anchor,
            created_at: '2026-10-24T00:00:00Z',
          },
          revision: 1,
          state: resolved.includes(at) ? 'resolved' : 'open',
          replies: [],
        })),
        activity: {},
        errors: {},
        busy: false,
        unknown: null,
      }),
      actions: {},
      refresh: async () => {},
    },
    post: async () => {},
    nameOf: (h: string) => h,
    avatarOf: () => '',
  }
}

/** 正文上那一颗标记，画出来要等正文自己加载完。 */
function markIn(md: HTMLElement, id: string): Promise<HTMLElement> {
  // 正文的读法是懒加载的：整目录并发跑时那一次 import 可能超过默认的 1 秒。
  return waitFor(
    () => {
      const found = md.querySelector<HTMLElement>(`mark[data-file-comment="${id}"]`)
      expect(found).toBeTruthy()
      return found!
    },
    { timeout: 5000 }
  )
}

it('只画这一版上记的正文锚点，别的版本记的和不是正文的那几种都不画', async () => {
  // 三条线程：一条记在这一版上（画），一条记在上一版（不画——那一段话在这一版里可能
  // 已经挪走或改过，照旧位置画会指到另一句话上），一条是另一种锚点（不画——幻灯片的
  // 整页文字也带 `text`，但那是「第几页」，不是正文里的一处；拿它在正文里找会画出个
  // 不相干的地方）。
  const open = ref(false)
  const onOtherVersion = { ...TEXT_RANGE, version: 'v0' }
  const onASlide = {
    kind: 'slide-page',
    path: 'output/说明.md',
    source: 'live',
    version: 'v1',
    task_id: null,
    page: 1,
    text: '重试 3 次',
  }
  const ui = mount({ comments: commentsWithOpen([TEXT_RANGE, onOtherVersion, onASlide], open) })

  const md = ui.getByTestId('markdown')
  const drawn = await markIn(md, 'c1')
  expect(drawn.textContent).toBe('重试 3 次')
  expect(md.querySelectorAll('mark[data-file-comment]')).toHaveLength(1)
})

it('点正文上的标记，把那一串评论打开，而且开的就是这一条', async () => {
  // 标记画在正文里，评论栏在另一侧。点它得有反应，而且是开到**被点的那一条**：
  // 只把栏打开、不指出是哪条，一屏好几条评论时读者还得自己找。
  const open = ref(false)
  const ui = mount({ comments: commentsWithOpen([TEXT_RANGE], open) })
  const drawn = await markIn(ui.getByTestId('markdown'), 'c1')
  expect(open.value).toBe(false)

  await fireEvent.click(drawn)

  await waitFor(() => expect(open.value).toBe(true))
  const column = await ui.findByLabelText('评论')
  expect(column.querySelector('.doc-thread-card.is-active')?.textContent).toContain('第 1 条评论')
})

it('在栏里换了另一条在看，正文上画重的那一处跟着换', async () => {
  // 两边说的是同一件事「现在看的是哪一条」。栏里换了卡、正文还亮着上一条，读者会以为
  // 点差了；反过来也一样——所以这条是双向的，不是只从正文往栏里传一次。
  const open = ref(false)
  const second = { ...TEXT_RANGE, text: '失败以后', heading: '配置', prefix: '', suffix: '重试 3 次' }
  const ui = mount({ comments: commentsWithOpen([TEXT_RANGE, second], open) })
  const md = ui.getByTestId('markdown')
  await fireEvent.click(await markIn(md, 'c1'))
  const column = await ui.findByLabelText('评论')
  await waitFor(() => expect(column.querySelector('[data-thread="c1"].is-active')).toBeTruthy())

  await fireEvent.click(column.querySelector<HTMLElement>('[data-thread="c2"]')!)

  await waitFor(() =>
    expect(md.querySelector('mark[data-file-comment="c2"]')?.classList.contains('is-active')).toBe(true)
  )
  expect(md.querySelector('mark[data-file-comment="c1"]')?.classList.contains('is-active')).toBe(false)
  expect(column.querySelector('[data-thread="c2"].is-active')).toBeTruthy()
})

it('已经解决的评论不画在正文上：那一条说完了，不该还压着正文', async () => {
  // 和概览文档一致：那边也只标 open 的那几条。一条说完的评论还亮着，读者会以为它还等着
  // 人回；栏里仍然能在「已解决」里找到它，只是不再压着正文。
  //
  // 同一个文件上还留着一条没解决的：等它那颗标记画出来，才说明这一轮画过了——只等
  // 正文 <p> 出现就断言「没有标记」，一个根本还没画的机会也会让这条绿。
  const open = ref(false)
  const stillOpen = { ...TEXT_RANGE, text: '失败以后', prefix: '', suffix: '重试 3 次' }
  const ui = mount({ comments: commentsWithOpen([TEXT_RANGE, stillOpen], open, [0]) })
  const md = ui.getByTestId('markdown')
  await markIn(md, 'c2')
  expect(md.querySelector('mark[data-file-comment="c1"]')).toBeNull()
})
