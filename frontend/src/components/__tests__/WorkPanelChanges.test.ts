/** 改动 tab · 文件半边: what is shown is the file of the task the person is
 * actually looking at.
 *
 * The panel kept `openPath` and the editor text across a topic switch, so one
 * task's file could show under another. Pinned at the component level: mount
 * the real panel, drive it through the DOM, assert on what is shown.
 *
 * Also pinned: binary and oversized files open with no editing, and a task
 * whose machine is not there leaves its committed files readable. Editing a
 * task's files is the 文件 panel's (`panels/PanelTaskFiles.spec.ts`).
 */
import type { PropType } from 'vue'
import type { FileContent, Topic } from '../../cx_types'

import { defineComponent } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '@/i18n'

// 断言读的是中文界面上的那一行字，语言钉在中文上。
beforeEach(() => setLocale('zh-CN'))

// Monaco does not load under happy-dom (and is not what is under test): stand in
// a textarea that speaks the same v-model / @save contract.
// The document's version history: the last edit is read on open; none here.
vi.mock('../../api/docThreads', () => ({
  listDocThreads: async () => ({ data: [], total: 0 }),
  writeDocThread: async () => ({}),
}))
vi.mock('../../api/docHistory', () => ({
  getDocVersions: async () => ({ versions: [], cursor: null }),
  restoreDocVersion: async () => ({}),
}))
vi.mock('../CodeEditor.vue', () => ({
  default: {
    name: 'CodeEditor',
    props: ['modelValue', 'filename', 'readonly'],
    emits: ['update:modelValue', 'save'],
    template:
      '<textarea class="stub-editor" :readonly="readonly" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
}))

const listFiles = vi.fn()
const readFile = vi.fn()
const writeFile = vi.fn()
const getGitDiff = vi.fn()

vi.mock('../../api/docCollab', async () => ({
  ...(await vi.importActual<typeof import('../../api/docCollab')>('../../api/docCollab')),
  // 测试里房间的文档就用房间的 id 来认：fakeDocCollab 按它预置文档。
  getRoomDocument: async (topicId: string) => ({ id: topicId }),
}))
vi.mock('../../composables/useDocCollab', async () => ({
  useDocCollab: (await import('../../test/fakeDocCollab')).useFakeDocCollab,
}))
vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    // 总览底下那一行「这个房间里的东西」一挂上就读一次。
    listRoomOutputs: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    // 总览里「进度」那一段会读它；这里不关心它，给一份空的。
    getProgress: vi.fn().mockResolvedValue({ items: [], updated_at: null }),
    listRoomTasks: vi.fn().mockImplementation((room: string) =>
      Promise.resolve({
        data: ['one', 'two'].map((name, index) => ({
          id: index === 0 ? `task-${room}` : `task-${room}-two`,
          title: name,
          status: 'open',
          branch_name: `task/${name}`,
          presentation: { column: 'building', phrase: 'running' },
          blocks: [],
        })),
        total: 2,
      })
    ),
    listFiles: (...a: unknown[]) => listFiles(...a),
    readFile: (...a: unknown[]) => readFile(...a),
    // 点一枚文件 chip 时先问房间里有没有这一份（`useRoomFileProbe`）；这些用例里的
    // a.py / first.py 都在任务的工作树上，房间自己没有，按「读不到」回答。
    readPreviewFile: vi.fn().mockRejectedValue(new Error('file not found')),
    writeFile: (...a: unknown[]) => writeFile(...a),
    // Everything else the panel calls on mount — quiet, empty answers.
    getDocNodes: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getTranscript: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getAgentControl: vi.fn().mockResolvedValue({ id: null, connected: false, tasks: {} }),
    getForgeConnection: vi.fn().mockResolvedValue({ kind: 'forgejo', connected: true, repo: 'o/r', url: null }),
    getGitDiff: (...a: unknown[]) => getGitDiff(...a),
    getPreview: vi.fn().mockResolvedValue(null),
    getTopicUsage: vi.fn().mockResolvedValue(null),
    getProjectUsage: vi.fn().mockResolvedValue(null),
    // 规则 1: the tabs a topic offers follow what it actually holds. These suites
    // are about the tabs' CONTENT, so they mount a topic that holds everything.
    getTopicWorkSummary: vi.fn().mockResolvedValue({ changed_files: ['a.py'], has_run: true }),
  }
})

import { provideTopicMemory } from '@/composables/useTopicMemory'

import WorkPanel from '../WorkPanel.vue'

function topic(id: string): Topic {
  return { id, project_id: 'p1', title: `话题 ${id}`, status: 'active' } as Topic
}

function textFile(path: string, content: string, version = 'v1'): FileContent {
  return { path, content, version, bytes: content.length, binary: false, too_large: false }
}

// 改动只长在任务上：面板画的是房间 `id` 里的第一件任务（`task-<房间>`）。
function mountPanel(id: string) {
  const vuetify = createVuetify({ components, directives })
  return render(WorkPanel, {
    props: { topic: topic(id), taskId: `task-${id}`, activityTick: 0 },
    global: {
      plugins: [vuetify, i18n],
    },
  })
}

/** The panel as the task page holds it: rebuilt for every page (ProjectShell keys
 *  the page on it), with the memory that outlives pages provided above. Switching
 *  the room here also switches the task it shows. */
function mountSwitchable(id: string) {
  const vuetify = createVuetify({ components, directives })
  const Page = defineComponent({
    components: { WorkPanel },
    props: { topic: { type: Object as PropType<Topic>, required: true } },
    setup() {
      provideTopicMemory()
    },
    template: '<WorkPanel :key="topic.id" :topic="topic" :task-id="`task-${topic.id}`" :activity-tick="0" />',
  })
  return render(Page, { props: { topic: topic(id) }, global: { plugins: [vuetify, i18n] } })
}

/** Let the panel's chained awaits (list → read) settle. */
async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function buttons(container: Element): HTMLButtonElement[] {
  return Array.from(container.querySelectorAll('button'))
}
function buttonByText(container: Element, text: string): HTMLButtonElement | undefined {
  return buttons(container).find((b) => b.textContent?.trim() === text)
}
function editor(container: Element): HTMLTextAreaElement | null {
  return container.querySelector('.stub-editor')
}

/** Select the 改动 tab, then open a file through 打开其他文件 — these cases are
 * about editing a file, including ones this topic never changed. */
async function openFilesTool(container: Element, path = 'a.py') {
  const tab = buttons(container).find((b) => b.getAttribute('title')?.startsWith('改动'))
  expect(tab, '找不到 改动 tab').toBeTruthy()
  await fireEvent.click(tab!)
  await flush()
  await openOther(container, path)
}
/** 「打开其他文件」：在这个任务的版本里挑一份打开。 */
async function openOther(container: Element, path: string) {
  const other = buttonByText(container, '打开其他文件')
  expect(other, '找不到「打开其他文件」').toBeTruthy()
  await fireEvent.click(other!)
  await flush()
  const item = Array.from(document.querySelectorAll('.open-file__list .v-list-item')).find(
    (n) => n.textContent?.trim() === path
  )
  expect(item, `「打开其他文件」里应该有 ${path}`).toBeTruthy()
  await fireEvent.click(item!)
  await flush()
}

beforeAll(() => {
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal(
    'visualViewport',
    Object.assign(new EventTarget(), {
      width: 1024,
      height: 768,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
    })
  )
  // Vuetify 的 layout/overlay 会摸这两个浏览器 API，happy-dom 没有。
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

describe('文件面板', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getGitDiff.mockResolvedValue({ diff: '' })
    listFiles.mockResolvedValue({ data: [{ path: 'a.py', bytes: 10 }], total: 1 })
    readFile.mockResolvedValue(textFile('a.py', 'A 话题的内容\n'))
    writeFile.mockResolvedValue({ path: 'a.py', version: 'v2' })
  })

  // Two topics are two worktrees of the SAME repo, so the same path usually
  // exists in both. That is what made the carried-over draft dangerous: the open
  // path was still valid in the new topic, so nothing forced a re-read, and the
  // editor kept showing — and 保存 kept writing — the other topic's content.
  it('切到别的任务后，编辑器显示的是新任务的文件，不是上一个任务的草稿', async () => {
    const { container, rerender } = mountSwitchable('topic-A')
    await flush()
    await openFilesTool(container)

    // Edit A's copy of a.py but do not save.
    await fireEvent.update(editor(container)!, '我在 A 话题里改的\n')
    await flush()

    // Switch to B, which has its own a.py.
    readFile.mockResolvedValue(textFile('a.py', 'B 话题的内容\n', 'vB'))
    await rerender({ topic: topic('topic-B') })
    await flush()
    await openFilesTool(container) // the panel went back to 文档 with the switch

    expect(editor(container)!.value).toBe('B 话题的内容\n')
  })

  it('同一房间换到另一件任务后，上一件任务迟到的文件不会盖住它', async () => {
    let finishOldRead!: (value: FileContent) => void
    readFile.mockImplementation((_project, path, _room, task) => {
      if (task === 'task-topic-A') {
        return new Promise<FileContent>((resolve) => {
          finishOldRead = resolve
        })
      }
      return Promise.resolve(textFile(path, '第二条任务\n', 'v-task-two'))
    })
    const { container, rerender } = mountPanel('topic-A')
    await flush()
    await openFilesTool(container)
    expect(finishOldRead).toBeTypeOf('function')
    await rerender({ topic: topic('topic-A'), taskId: 'task-topic-A-two', activityTick: 0 })
    await flush()
    await openFilesTool(container)
    finishOldRead(textFile('a.py', '迟到的第一条任务\n', 'v-old'))
    await flush()
    expect(editor(container)!.value).toBe('第二条任务\n')
  })

  it('二进制文件只读打开，根本不给保存按钮', async () => {
    readFile.mockResolvedValue({
      path: 'a.py',
      content: null,
      version: 'v9',
      bytes: 2048,
      binary: true,
      too_large: false,
    })

    const { container } = mountPanel('topic-A')
    await flush()
    await openFilesTool(container)

    expect(editor(container)).toBeNull()
    expect(container.textContent).toContain('非文本文件，无法编辑')
    expect(buttonByText(container, '保存')).toBeUndefined()
  })

  it('过大的文件不进编辑器，给下载入口', async () => {
    readFile.mockResolvedValue({
      path: 'a.py',
      content: null,
      version: null,
      bytes: 52 * 1024 * 1024,
      binary: false,
      too_large: true,
    })

    const { container } = mountPanel('topic-A')
    await flush()
    await openFilesTool(container)

    expect(editor(container)).toBeNull()
    expect(container.textContent).toContain('文件过大')
    expect(buttonByText(container, '保存')).toBeUndefined()
  })

  it('an offline machine leaves committed files available through the version selector', async () => {
    listFiles.mockImplementation((_p, _room, _task, source) =>
      source === 'live'
        ? Promise.reject(new Error('任务机器尚未连接'))
        : Promise.resolve({ data: [{ path: 'a.py', bytes: 10 }], total: 1, source })
    )
    readFile.mockResolvedValue({ ...textFile('a.py', 'Committed content'), source: 'committed', editable: false })
    const { container } = mountPanel('topic-A')
    await flush()
    const tab = buttons(container).find((b) => b.getAttribute('title')?.startsWith('改动'))
    await fireEvent.click(tab!)
    await flush()
    expect(container.textContent).toContain('任务机器尚未连接')
    await fireEvent.click(buttonByText(container, '切换到已提交版本')!)
    await flush()
    await openOther(container, 'a.py')
    expect(editor(container)?.value).toBe('Committed content')
    expect(editor(container)?.readOnly).toBe(true)
    expect(writeFile).not.toHaveBeenCalled()
  })
})

// 两个半成品合成一个审查面：树上标着改了多少，点开看的是这个文件自己的 diff。
// 以前想验收得先在整块裸 diff 里认出改了哪些文件，再去另一半的树里一个个翻出来。
describe('改动 tab · 审查面', () => {
  const DIFF = `diff --git a/a.py b/a.py
index 111..222 100644
--- a/a.py
+++ b/a.py
@@ -1,2 +1,2 @@
 keep
-旧的
+新的
diff --git a/docs/new.md b/docs/new.md
new file mode 100644
--- /dev/null
+++ b/docs/new.md
@@ -0,0 +1 @@
+新文件
`

  beforeEach(() => {
    vi.clearAllMocks()
    getGitDiff.mockResolvedValue({ diff: DIFF })
    listFiles.mockResolvedValue({
      data: [
        { path: 'a.py', bytes: 10 },
        { path: 'docs/new.md', bytes: 5 },
        { path: 'untouched.txt', bytes: 3 },
      ],
      total: 3,
    })
    readFile.mockResolvedValue(textFile('a.py', 'keep\n新的\n'))
  })

  async function openChanges(container: Element) {
    // The tab bar only knows what the topic holds after the summary lands.
    await flush()
    const tab = buttons(container).find((b) => b.getAttribute('title')?.startsWith('改动'))
    expect(tab, '找不到 改动 tab').toBeTruthy()
    await fireEvent.click(tab!)
    await flush()
  }

  it('默认只列这个任务改过的文件，没动过的不在清单里', async () => {
    const { container } = mountPanel('topic-A')
    await openChanges(container)

    const names = Array.from(container.querySelectorAll('.file-item__name')).map((e) => e.textContent?.trim())
    expect(names).toContain('a.py')
    expect(names).toContain('new.md')
    expect(names).not.toContain('untouched.txt')
  })

  it('树上标着每个文件改了多少，新增的文件说「新增」', async () => {
    const { container } = mountPanel('topic-A')
    await openChanges(container)

    const marks = Array.from(container.querySelectorAll('.file-mark')).map((e) => e.textContent?.trim())
    expect(marks).toContain('+1')
    expect(marks).toContain('−1')
    expect(marks).toContain('新增')
  })

  async function openSection(container: Element, path: string) {
    const section = container.querySelector(`.diff-file[data-path="${path}"]`)
    expect(section, `改动里应该有 ${path} 这一段`).toBeTruthy()
    await fireEvent.click(buttonByText(section!, '打开')!)
    await flush()
  }

  it('点开一个文件看到的是它自己的 diff，不是整块', async () => {
    const { container } = mountPanel('topic-A')
    await openChanges(container)
    await openSection(container, 'a.py')

    const view = container.querySelector('.diff-view')
    expect(view, '没有渲染逐文件 diff').toBeTruthy()
    expect(view!.textContent).toContain('新的')
    expect(view!.textContent).not.toContain('新文件')
    // 增删各自着色——整块裸 <pre> 读不动，正是这个 tab 以前的样子。
    expect(container.querySelectorAll('.diff-line--add').length).toBe(1)
    expect(container.querySelectorAll('.diff-line--del').length).toBe(1)
  })

  it('没动过的文件没有两面可切，直接就是可编辑的全文', async () => {
    const { container } = mountPanel('topic-A')
    await openChanges(container)

    readFile.mockResolvedValue(textFile('untouched.txt', 'x\n'))
    await openOther(container, 'untouched.txt')

    expect(buttonByText(container, '差异'), '没改过的文件不该给「差异」这一面').toBeUndefined()
    expect(editor(container)).toBeTruthy()
  })
})

describe('task file navigation', () => {
  const diff = `diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-old
+new
`
  beforeEach(() => {
    vi.clearAllMocks()
    getGitDiff.mockResolvedValue({ diff })
    listFiles.mockResolvedValue({ data: [{ path: 'a.py', bytes: 10 }], total: 1 })
    readFile.mockImplementation((_project, path, _room, task) =>
      Promise.resolve(textFile(path, `file:${task}`, `v:${task}`))
    )
    writeFile.mockResolvedValue({ path: 'a.py', version: 'v2' })
  })

  async function openChangesTab(container: Element) {
    await flush()
    await fireEvent.click(buttons(container).find((b) => b.getAttribute('title')?.startsWith('改动'))!)
    await flush()
  }

  it('does not substitute project code when a task version cannot be loaded', async () => {
    listFiles.mockRejectedValue(new Error('任务版本不可用'))
    const { container } = mountPanel('topic-A')
    await openChangesTab(container)

    expect(container.textContent).toContain('任务版本不可用')
    expect(listFiles.mock.calls.length).toBeGreaterThan(0)
    expect(listFiles.mock.calls.every((call) => call[2] === 'task-topic-A')).toBe(true)
  })
})
