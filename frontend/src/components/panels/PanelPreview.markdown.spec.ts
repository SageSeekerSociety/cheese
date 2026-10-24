/**
 * markdown 那一支的指认：位置来自渲染出来的正文（哪个标题之下、原文、两侧各 32 字），
 * 而屏幕上换了另一份文件时这条指认必须自己撤掉——它说的是文件 A 里的位置，发出去时
 * 名字写的却是文件 B。
 *
 * 三分支走的是同一套事件（`PanelPreview.document.spec.ts` 里另有端到端的一条），
 * 这里只盯这两条规则，所以直接挂 `PanelPreviewView`、把 props 递进去。
 */
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
  return waitFor(() => md.querySelector('p')!.firstChild!)
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

/** 在「配置」那一节里选中「重试 3 次」这一段，和上面两条一样。 */
async function selectPassage(ui: ReturnType<typeof mount>) {
  const md = ui.getByTestId('markdown')
  const node = await firstLine(md)
  const range = document.createRange()
  range.setStart(node, 4)
  range.setEnd(node, 10)
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
    post: () => new Promise<void>((resolve) => (release = resolve)),
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

  // 直接另选一处（不按取消）：条上那颗取消本来就清这句话，所以只有「指认自己变了就清」
  // 才盖得住这条路——上一版正是只清了取消那条，这里会留着上一处的失败。
  await selectPassage(ui)
  expect(ui.getByPlaceholderText('就这一处说点什么').closest('.locator')!.querySelector('[role="alert"]')).toBeNull()
})

it('发失败时读者已经换了一处：那句失败不落在新的一处上', async () => {
  // 失败是发生在 await 之后的，这期间读者完全可能已经去指别处了。这句话说的是上一处，
  // 落在新的这一处下面，会让人以为新指的这处也发失败了。
  let reject: (e: Error) => void = () => {}
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
    post: () => new Promise<void>((_resolve, rej) => (reject = rej)),
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

  // 还没回来，读者就换了一处。
  await selectPassage(ui)
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
