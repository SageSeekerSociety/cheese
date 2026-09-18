/** 改动 tab 整块跟着语言走。
 *
 * 这一份真的把面板挂起来渲染一遍（不是只读词表）：来源栏、范围开关、文件栏、文件树、
 * 逐文件 diff、二进制那一格、以及只在 hover / 读屏时听得见的 `title`，全都过一遍。
 * 扫的是这块面板自己的字——`.panel-changes` 整块的 `textContent`，加上它下面所有
 * `title` 属性（一个字都不在 textContent 里的那些）。
 *
 * **摘掉的一处**：`.source-status` 里那句是后端算好的 `presentation.display_status`
 * —— `cx_types.ts` 的 `Presentation` 写着它是「可以直接显示的中文」、前端照抄不再映射，
 * 所以它在英文界面上本来就是汉字，不是这块切片能修的东西。摘掉的那几个节点这里单独
 * 断言成后端给的原话，免得这个「摘掉」变成一块能藏东西的地方；除此之外一个汉字都不许
 * 剩。那条债记在报告里。
 *
 * 带计数的 `workspace.changes.fileCount`（总览那一支）走的是词表里记着的
 * `(s)`/三形态写法，这里不摆总览，所以不在这份里钉——它在 catalog.spec.ts 的多形态
 * 契约里。
 */
import type { Component } from 'vue'
import type { FileContent, RoomTask, WorkspaceFile } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

// Monaco 在 happy-dom 下起不来（也不是这份要考的东西）：换成一个说同一套
// v-model / @save 契约的 textarea。
vi.mock('../CodeEditor.vue', () => ({
  default: {
    name: 'CodeEditor',
    props: ['modelValue', 'filename', 'readonly'],
    emits: ['update:modelValue', 'save'],
    template:
      '<textarea class="stub-editor" :readonly="readonly" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
}))

const listRoomTasks = vi.fn()
const listFiles = vi.fn()
const readFile = vi.fn()
const getGitLog = vi.fn()
const getGitDiff = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    listRoomTasks: (...a: unknown[]) => listRoomTasks(...a),
    listFiles: (...a: unknown[]) => listFiles(...a),
    readFile: (...a: unknown[]) => readFile(...a),
    getGitLog: (...a: unknown[]) => getGitLog(...a),
    getGitDiff: (...a: unknown[]) => getGitDiff(...a),
  }
})

import PanelChanges from './PanelChanges.vue'

import { setLocale } from '@/i18n'

const Panel = PanelChanges as unknown as Component

const CJK = /[㐀-䶿一-鿿豈-﫿]/

/** 一条活，后端连它那句中文状态一起给。 */
function roomTask(): RoomTask {
  return {
    id: 'task-1',
    project_id: 'p1',
    room_id: 't1',
    title: 'Task one',
    status: 'open',
    branch_name: 'task/one',
    presentation: { column: 'building', display_status: '施工中' },
    blocks: [],
  } as unknown as RoomTask
}

/** 四段：改过的、新增的图、删掉的、二进制。四种标记各占一个。 */
const DIFF = [
  'diff --git a/src/a.ts b/src/a.ts',
  'index 1111111..2222222 100644',
  '--- a/src/a.ts',
  '+++ b/src/a.ts',
  '@@ -1,2 +1,3 @@',
  ' const a = 1',
  '-const b = 2',
  '+const b = 3',
  '+const c = 4',
  'diff --git a/assets/logo.png b/assets/logo.png',
  'new file mode 100644',
  'index 0000000..3333333',
  '--- /dev/null',
  '+++ b/assets/logo.png',
  '@@ -0,0 +1 @@',
  '+binary',
  'diff --git a/src/gone.ts b/src/gone.ts',
  'deleted file mode 100644',
  'index 4444444..0000000',
  '--- a/src/gone.ts',
  '+++ /dev/null',
  '@@ -1 +0,0 @@',
  '-export const gone = true',
  'diff --git a/data/blob.bin b/data/blob.bin',
  'index 5555555..6666666 100644',
  'Binary files a/data/blob.bin and b/data/blob.bin differ',
].join('\n')

function textFile(path: string, content: string): FileContent {
  return { path, content, version: 'v1', bytes: content.length, binary: false, too_large: false }
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  listRoomTasks.mockReset()
  listFiles.mockReset()
  readFile.mockReset()
  getGitLog.mockReset()
  getGitDiff.mockReset()
  listRoomTasks.mockResolvedValue({ data: [roomTask()], total: 1 })
  getGitLog.mockResolvedValue({ data: [{ hash: 'abcdef1234', author: 'Cheese', message: 'Fix the thing' }], total: 1 })
  getGitDiff.mockResolvedValue({ diff: DIFF })
  // `src/gone.ts` 故意不在列表里：这一支把它删了，那也是一条要验收的改动。
  listFiles.mockResolvedValue({
    data: [
      { path: 'src/a.ts', bytes: 1200 },
      { path: 'assets/logo.png', bytes: 4096 },
      { path: 'data/blob.bin', bytes: 4096 },
      { path: 'README.md', bytes: 40 },
    ] as WorkspaceFile[],
    total: 4,
  })
  readFile.mockImplementation((_pid: string, path: string) =>
    Promise.resolve(
      path === 'data/blob.bin'
        ? ({ path, content: null, version: 'v9', bytes: 4096, binary: true, too_large: false } as FileContent)
        : textFile(path, 'const a = 1\n')
    )
  )
})

function mount() {
  return render(Panel, {
    props: { topicId: 't1', projectId: 'p1', taskId: 'task-1', active: true },
    global: { plugins: [vuetify] },
  })
}

/** 让面板串起来的几段 await（任务 → diff/文件 → 读文件）走完。 */
async function flush() {
  for (let i = 0; i < 12; i += 1) await new Promise((r) => setTimeout(r, 0))
}

const panel = (c: Element) => c.querySelector('.panel-changes') as HTMLElement
const text = (el: Element | null | undefined) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()
/** 一整块的 textContent，空白折掉：模板里的换行给的空白不是这里要钉的东西。 */
const flat = (el: Element) => (el.textContent ?? '').replace(/\s+/g, '')

function buttons(c: Element): HTMLButtonElement[] {
  return Array.from(c.querySelectorAll('button')) as HTMLButtonElement[]
}
function buttonByText(c: Element, label: string): HTMLButtonElement | undefined {
  return buttons(c).find((b) => text(b) === label)
}
function buttonTitles(c: Element): string[] {
  return buttons(c)
    .map((b) => b.getAttribute('title') ?? '')
    .filter(Boolean)
}
/** 范围开关上那两个字。件数那个 span 跟在「改动」后面，不是这个词的一部分。 */
function scopes(c: Element): string[] {
  return Array.from(c.querySelectorAll('.changes-bar .seg__btn')).map((b) => {
    const count = text(b.querySelector('.seg__count'))
    const full = text(b)
    return count ? full.replace(count, '').trim() : full
  })
}
/** 文件树上每一个文件行（夹着 +N −M 和「新增 / 删除」标记）；目录行不算。 */
function fileRows(c: Element): { name: string; body: string }[] {
  return Array.from(c.querySelectorAll('.file-item:not(.file-item--dir)')).map((row) => ({
    name: text(row.querySelector('.file-item__name')),
    body: flat(row),
  }))
}
function rowByName(c: Element, name: string): HTMLElement {
  const row = Array.from(c.querySelectorAll('.file-item:not(.file-item--dir)')).find(
    (r) => text(r.querySelector('.file-item__name')) === name
  )
  if (!row) throw new Error(`文件树上没有 ${name}`)
  return row as HTMLElement
}
/** 面板自己写的字：把后端算好的那句摘掉之后剩下的全部。 */
function ownText(c: Element): string {
  const copy = panel(c).cloneNode(true) as HTMLElement
  copy.querySelectorAll('.source-status').forEach((n) => n.remove())
  return copy.textContent ?? ''
}
const backendStatus = (c: Element) => Array.from(c.querySelectorAll('.source-status')).map((n) => text(n))

/** 打开面板、等第一个改过的文件落到右边那一格。 */
async function open(view: ReturnType<typeof mount>) {
  await waitFor(() => expect(text(view.container.querySelector('.file-bar__path'))).toBe('src/a.ts'))
  await flush()
}

describe('讲中文', () => {
  it('来源栏、范围开关、文件栏、文件树都是中文', async () => {
    setLocale('zh-CN')
    const view = mount()
    await open(view)
    const c = view.container

    expect(text(panel(c).querySelector('.source-heading h3'))).toBe('Task one')
    expect(backendStatus(c)).toEqual(['施工中'])
    expect(text(c.querySelector('.source-current'))).toBe('当前查看：Task one')
    expect(scopes(c)).toEqual(['改动', '全部文件'])
    expect(text(c.querySelector('.file-bar__path'))).toBe('src/a.ts')
    expect(Array.from(c.querySelectorAll('.seg--sm .seg__btn')).map((b) => text(b))).toEqual(['差异', '编辑'])
    expect(c.querySelector('.diff-view')).not.toBeNull()
    expect(buttonTitles(c)).toContain('刷新')
    expect(buttonTitles(c)).toContain('文件列表')
    expect(fileRows(c)).toEqual([
      { name: 'logo.png', body: 'logo.png新增' },
      { name: 'blob.bin', body: 'blob.bin' },
      { name: 'a.ts', body: 'a.ts+2−1' },
      { name: 'gone.ts', body: 'gone.ts删除' },
    ])

    view.unmount()
  })
})

describe('讲英文', () => {
  it('整块一个汉字都不剩（后端那句除外），title 也扫一遍', async () => {
    setLocale('en')
    const view = mount()
    await open(view)
    const c = view.container

    expect(text(panel(c).querySelector('.source-heading h3'))).toBe('Task one')
    expect(text(c.querySelector('.source-current'))).toBe('Viewing Task one')
    expect(scopes(c)).toEqual(['Changed', 'All files'])
    expect(Array.from(c.querySelectorAll('.seg--sm .seg__btn')).map((b) => text(b))).toEqual(['Diff', 'Edit'])
    expect(c.querySelector('.diff-view')).not.toBeNull()
    expect(buttonTitles(c)).toContain('Refresh')
    expect(buttonTitles(c)).toContain('File list')
    expect(fileRows(c)).toEqual([
      { name: 'logo.png', body: 'logo.pngAdded' },
      { name: 'blob.bin', body: 'blob.bin' },
      { name: 'a.ts', body: 'a.ts+2−1' },
      { name: 'gone.ts', body: 'gone.tsDeleted' },
    ])

    const own = ownText(c)
    expect(CJK.test(own), own).toBe(false)
    const titles = buttonTitles(c).join(' | ')
    expect(CJK.test(titles), titles).toBe(false)
    // 摘掉的那个节点是后端给的中文，原文照抄 —— 不是这块面板漏翻的字。
    expect(backendStatus(c)).toEqual(['施工中'])

    view.unmount()
  })

  it('二进制那一格：只读、说明和下载原文件都是英文，两半句之间只有一个空格', async () => {
    setLocale('en')
    const view = mount()
    await open(view)
    const c = view.container

    await fireEvent.click(rowByName(c, 'blob.bin'))
    await flush()
    expect(text(c.querySelector('.file-bar__path'))).toBe('data/blob.bin')
    expect(text(c.querySelector('.file-bar__ro'))).toBe('Read-only')
    expect(buttonByText(c, 'Save')).toBeUndefined()

    // 这一格没有 diff 可看时才有意义——它改过，所以先切到文本那一面。
    await fireEvent.click(buttonByText(c, 'Edit')!)
    await flush()
    expect(text(c.querySelector('.file-blob__title'))).toBe("Binary file — it can't be edited as text")
    expect(text(c.querySelector('.file-blob__note'))).toBe(
      'data/blob.bin · 4.0 KB — opening it as text would corrupt it, so this one is read-only'
    )
    expect(buttonByText(c, 'Download the original')).toBeTruthy()

    const own = ownText(c)
    expect(CJK.test(own), own).toBe(false)

    view.unmount()
  })

  it('改一笔、换一个文件：未保存的点和草稿提示也都是英文', async () => {
    setLocale('en')
    const view = mount()
    await open(view)
    const c = view.container

    await fireEvent.click(buttonByText(c, 'Edit')!)
    await flush()
    const editor = c.querySelector('.stub-editor') as HTMLTextAreaElement
    await fireEvent.update(editor, 'const a = 2\n')
    await flush()
    expect(buttonByText(c, 'Save')?.disabled).toBe(false)
    expect(c.querySelector('.file-bar__dot')?.getAttribute('title')).toBe('Unsaved')

    // 换一个文件：这一笔存成草稿，页面底下那句提示出来。
    await fireEvent.click(rowByName(c, 'gone.ts'))
    await flush()
    expect(text(c.querySelector('.source-drafts'))).toBe(
      'Unsaved edits are kept on this page — switch back to that file to keep editing'
    )

    const own = ownText(c)
    expect(CJK.test(own), own).toBe(false)

    view.unmount()
  })

  it('没有提交、没有文件的空场也说的是英文', async () => {
    setLocale('en')
    getGitLog.mockResolvedValue({ data: [], total: 0 })
    getGitDiff.mockResolvedValue({ diff: '' })
    listFiles.mockResolvedValue({ data: [], total: 0 })
    const view = mount()
    await flush()
    // 没有改动的文件，右边那一格是空的，`openPath` 不落 —— 提交列表这一支在场。
    expect(text(view.container.querySelector('.changes-scroll .t-eyebrow'))).toBe('Commits')
    expect(text(view.container.querySelector('.changes-scroll .text-medium-emphasis'))).toBe('No commits')
    expect(text(view.container.querySelector('.file-bar__path'))).toBe('No file open')
    expect(text(view.container.querySelector('.file-list .text-center'))).toBe('No changes')

    const own = ownText(view.container)
    expect(CJK.test(own), own).toBe(false)

    view.unmount()
  })
})

describe('切一次语言', () => {
  it('已经画出来的来源栏、范围开关和文件树当场跟着换', async () => {
    setLocale('zh-CN')
    const view = mount()
    await open(view)
    expect(scopes(view.container)).toEqual(['改动', '全部文件'])
    expect(buttonByText(view.container, '房间改动')).toBeTruthy()

    setLocale('en')
    await waitFor(() => expect(scopes(view.container)).toEqual(['Changed', 'All files']))
    expect(buttonByText(view.container, 'Room changes')).toBeTruthy()
    expect(buttonByText(view.container, 'Diff')).toBeTruthy()
    expect(text(view.container.querySelector('.source-current'))).toBe('Viewing Task one')
    // 一行字都没重挂，切的是语言不是面板。
    expect(fileRows(view.container).map((r) => r.name)).toEqual(['logo.png', 'blob.bin', 'a.ts', 'gone.ts'])

    const own = ownText(view.container)
    expect(CJK.test(own), own).toBe(false)

    view.unmount()
  })
})
