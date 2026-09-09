/** 改动 tab · 文件半边: a save must land in the topic the user is actually
 * looking at.
 *
 * The panel kept `openPath` and the editor draft across a topic switch. Open a
 * file in topic A, switch to topic B, press 保存 — and A's draft was written
 * into B's worktree, at A's path. That is cross-topic data corruption, so it is
 * pinned here at the component level: mount the real panel, drive it through the
 * DOM, and assert on what reaches the API.
 *
 * Also pinned: binary and oversized files open read-only (no 保存 button), and a
 * save carries the version it was based on so the backend can reject a lost race.
 *
 * (Was DocPanelFiles.test.ts against the 文件 drawer. That drawer is now the 文件
 * half of the 改动 tab; every assertion below is unchanged, which is the point —
 * the切分 was supposed to move this code, not alter it.)
 */
import type { FileContent, Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

// Monaco does not load under happy-dom (and is not what is under test): stand in
// a textarea that speaks the same v-model / @save contract.
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

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    listRoomTasks: vi.fn().mockImplementation((room: string) =>
      Promise.resolve({
        data: ['one', 'two'].map((name, index) => ({
          id: index === 0 ? `task-${room}` : `task-${room}-two`,
          title: name,
          status: 'open',
          branch_name: `task/${name}`,
          presentation: { column: 'building', display_status: '运行中' },
          blocks: [],
        })),
        total: 2,
      })
    ),
    listFiles: (...a: unknown[]) => listFiles(...a),
    readFile: (...a: unknown[]) => readFile(...a),
    writeFile: (...a: unknown[]) => writeFile(...a),
    // Everything else the panel calls on mount — quiet, empty answers.
    getDoc: vi.fn().mockResolvedValue({ markdown: '', title: '' }),
    putDoc: vi.fn().mockResolvedValue({}),
    getComments: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getDocNodes: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getTranscript: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getTerminal: vi.fn().mockResolvedValue({ available: false, backend: 'none' }),
    getGitLog: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getGitDiff: (...a: unknown[]) => getGitDiff(...a),
    getPreview: vi.fn().mockResolvedValue(null),
    getTopicUsage: vi.fn().mockResolvedValue(null),
    getProjectUsage: vi.fn().mockResolvedValue(null),
    // 规则 1: the tabs a topic offers follow what it actually holds. These suites
    // are about the tabs' CONTENT, so they mount a topic that holds everything.
    getTopicWorkSummary: vi.fn().mockResolvedValue({ changed_files: ['a.py'], has_run: true }),
  }
})

import { listRoomTasks } from '../../api'
import WorkPanel from '../WorkPanel.vue'

function topic(id: string): Topic {
  return { id, project_id: 'p1', title: `话题 ${id}`, status: 'active' } as Topic
}

function textFile(path: string, content: string, version = 'v1'): FileContent {
  return { path, content, version, bytes: content.length, binary: false, too_large: false }
}

function mountPanel(id: string) {
  const vuetify = createVuetify({ components, directives })
  return render(WorkPanel, {
    props: { topic: topic(id), activityTick: 0 },
    global: {
      plugins: [vuetify],
      stubs: {
        VSelect: {
          props: ['modelValue', 'items'],
          emits: ['update:modelValue'],
          template: `<select class="task-select" :value="modelValue" @change="$emit('update:modelValue', $event.target.value)">
          <option value="">Accepted code</option><option v-for="task in items" :key="task.id" :value="task.id">{{ task.title }}</option>
        </select>`,
        },
      },
    },
  })
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

/** Select the 改动 tab, then widen its tree to the whole worktree — these cases
 * are about editing a file, including ones this topic never changed. */
async function openFilesTool(container: Element) {
  const tab = buttons(container).find((b) => b.getAttribute('title')?.startsWith('改动'))
  expect(tab, '找不到 改动 tab').toBeTruthy()
  await fireEvent.click(tab!)
  await flush()
  const selector = container.querySelector('.task-select') as HTMLSelectElement
  await fireEvent.update(selector, selector.options[1].value)
  await flush()
  const seg = buttonByText(container, '全部文件')
  expect(seg, '找不到 全部文件 范围').toBeTruthy()
  await fireEvent.click(seg!)
  await flush()
}

beforeAll(() => {
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
  it('切到别的话题后，编辑器显示的是新话题的文件，不是上个话题的草稿', async () => {
    const { container, rerender } = mountPanel('topic-A')
    await flush()
    await openFilesTool(container)

    // Edit A's copy of a.py but do not save.
    await fireEvent.update(editor(container)!, '我在 A 话题里改的\n')
    await flush()

    // Switch to B, which has its own a.py.
    readFile.mockResolvedValue(textFile('a.py', 'B 话题的内容\n', 'vB'))
    await rerender({ topic: topic('topic-B'), activityTick: 0 })
    await flush()
    await openFilesTool(container) // the panel went back to 文档 with the switch

    expect(editor(container)!.value).toBe('B 话题的内容\n')
  })

  it('切话题后按保存，写的是新话题的内容和版本，不会把上个话题的草稿写进来', async () => {
    const { container, rerender } = mountPanel('topic-A')
    await flush()
    await openFilesTool(container)
    await fireEvent.update(editor(container)!, '我在 A 话题里改的\n')
    await flush()

    readFile.mockResolvedValue(textFile('a.py', 'B 话题的内容\n', 'vB'))
    await rerender({ topic: topic('topic-B'), activityTick: 0 })
    await flush()
    await openFilesTool(container)

    // Nothing has been edited in B, so 保存 is disabled — there is no unsaved
    // work here, and the draft from A must not have become B's unsaved work.
    const save = buttonByText(container, '保存')!
    expect(save.disabled).toBe(true)

    await fireEvent.update(editor(container)!, '在 B 里改的\n')
    await flush()
    await fireEvent.click(save)
    await flush()

    expect(writeFile).toHaveBeenCalledTimes(1)
    expect(writeFile).toHaveBeenCalledWith('p1', 'a.py', '在 B 里改的\n', 'topic-B', 'vB', 'task-topic-B')
  })

  it('保存时带上读到的版本，好让后端拦住抢跑的写', async () => {
    const { container } = mountPanel('topic-A')
    await flush()
    await openFilesTool(container)

    await fireEvent.update(editor(container)!, '人改过的\n')
    await flush()
    await fireEvent.click(buttonByText(container, '保存')!)
    await flush()

    expect(writeFile).toHaveBeenCalledWith('p1', 'a.py', '人改过的\n', 'topic-A', 'v1', 'task-topic-A')
  })

  it('项目已采纳的文本仍显示全文，并保持只读', async () => {
    const { container } = mountPanel('topic-A')
    await flush()
    await openFilesTool(container)
    const selector = container.querySelector('.task-select') as HTMLSelectElement
    await fireEvent.update(selector, '')
    await flush()
    await fireEvent.click(buttonByText(container, '全部文件')!)
    await flush()
    expect(editor(container)!.value).toBe('A 话题的内容\n')
    expect(editor(container)!.readOnly).toBe(true)
    expect(container.textContent).not.toContain('二进制文件，不能按文本编辑')
    expect(writeFile).not.toHaveBeenCalled()
  })

  it('任务关闭后刷新会保留文本，但禁止继续保存', async () => {
    const { container } = mountPanel('topic-A')
    await flush()
    await openFilesTool(container)
    expect(editor(container)!.readOnly).toBe(false)
    vi.mocked(listRoomTasks).mockResolvedValueOnce({
      data: [
        {
          id: 'task-topic-A',
          project_id: 'p1',
          room_id: 'topic-A',
          title: 'one',
          status: 'closed',
          branch_name: 'task/one',
          blocks: [],
          created_at: '2026-09-09T00:00:00Z',
          updated_at: '2026-09-09T00:00:00Z',
          presentation: { column: 'done', display_status: '已完成' },
        },
      ],
      total: 1,
    })
    await fireEvent.click(container.querySelector('button[title="刷新"]')!)
    await flush()
    expect(editor(container)!.readOnly).toBe(true)
    expect(editor(container)!.value).toBe('A 话题的内容\n')
    await fireEvent.update(editor(container)!, 'late edit')
    const save = buttonByText(container, '保存')
    if (save) await fireEvent.click(save)
    await flush()
    expect(writeFile).not.toHaveBeenCalled()
  })

  it('房间归档后，已打开的任务文件变成只读', async () => {
    const { container, rerender } = mountPanel('topic-A')
    await flush()
    await openFilesTool(container)
    expect(editor(container)!.readOnly).toBe(false)
    await rerender({ topic: { ...topic('topic-A'), status: 'archived' }, activityTick: 0 })
    await flush()
    expect(editor(container)!.readOnly).toBe(true)
    expect(editor(container)!.value).toBe('A 话题的内容\n')
  })

  it('同一房间切换任务后，旧任务的迟到文件响应不会覆盖新任务', async () => {
    let finishOldRead!: (value: FileContent) => void
    readFile.mockImplementation((_project, path, _room, task) => {
      if (task === 'task-topic-A') {
        return new Promise<FileContent>((resolve) => {
          finishOldRead = resolve
        })
      }
      return Promise.resolve(textFile(path, '第二条任务\n', 'v-task-two'))
    })
    const { container } = mountPanel('topic-A')
    await flush()
    await openFilesTool(container)
    expect(finishOldRead).toBeTypeOf('function')
    const selector = container.querySelector('.task-select') as HTMLSelectElement
    await fireEvent.update(selector, 'task-topic-A-two')
    await flush()
    await fireEvent.click(buttonByText(container, '全部文件')!)
    await flush()
    finishOldRead(textFile('a.py', '迟到的第一条任务\n', 'v-old'))
    await flush()
    expect(editor(container)!.value).toBe('第二条任务\n')
    await fireEvent.update(editor(container)!, '只修改第二条任务\n')
    await flush()
    await fireEvent.click(buttonByText(container, '保存')!)
    await flush()
    expect(writeFile).toHaveBeenCalledWith(
      'p1',
      'a.py',
      '只修改第二条任务\n',
      'topic-A',
      'v-task-two',
      'task-topic-A-two'
    )
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
    expect(container.textContent).toContain('二进制文件，不能按文本编辑')
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
    expect(container.textContent).toContain('文件太大')
    expect(buttonByText(container, '保存')).toBeUndefined()
  })

  it('保存冲突时不静默胜出，把冲突亮给人并给两条出路', async () => {
    const { ApiError } = await vi.importActual<typeof import('../../api')>('../../api')
    writeFile.mockRejectedValue(new ApiError(409, 'HTTP 409'))

    const { container } = mountPanel('topic-A')
    await flush()
    await openFilesTool(container)
    await fireEvent.update(editor(container)!, '人改过的\n')
    await flush()
    await fireEvent.click(buttonByText(container, '保存')!)
    await flush()

    expect(container.textContent).toContain('这个文件在你编辑期间被改过')
    const overwrite = buttons(container).find((b) => b.textContent?.includes('仍然覆盖保存'))
    expect(overwrite).toBeTruthy()

    // 覆盖 is the human's explicit choice — it goes out with no version.
    writeFile.mockResolvedValue({ path: 'a.py', version: 'v3' })
    await fireEvent.click(overwrite!)
    await flush()
    expect(writeFile).toHaveBeenLastCalledWith('p1', 'a.py', '人改过的\n', 'topic-A', null, 'task-topic-A')
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
    const selector = container.querySelector('.task-select') as HTMLSelectElement
    await fireEvent.update(selector, selector.options[1].value)
    await flush()
  }

  it('默认只列这个话题改过的文件，没动过的不在清单里', async () => {
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

  it('点开一个文件看到的是它自己的 diff，不是整块', async () => {
    const { container } = mountPanel('topic-A')
    await openChanges(container)

    const view = container.querySelector('.diff-view')
    expect(view, '没有渲染逐文件 diff').toBeTruthy()
    expect(view!.textContent).toContain('新的')
    expect(view!.textContent).not.toContain('新文件')
    // 增删各自着色——整块裸 <pre> 读不动，正是这个 tab 以前的样子。
    expect(container.querySelectorAll('.diff-line--add').length).toBe(1)
    expect(container.querySelectorAll('.diff-line--del').length).toBe(1)
  })

  it('要微调就切到编辑，保存那条路一个字没变', async () => {
    writeFile.mockResolvedValue({ path: 'a.py', version: 'v2' })
    const { container } = mountPanel('topic-A')
    await openChanges(container)

    await fireEvent.click(buttonByText(container, '编辑')!)
    await flush()
    const box = editor(container)
    expect(box, '切到编辑没给出编辑器').toBeTruthy()

    await fireEvent.update(box!, '改一行\n')
    await fireEvent.click(buttonByText(container, '保存')!)
    await flush()

    expect(writeFile).toHaveBeenCalledWith('p1', 'a.py', '改一行\n', 'topic-A', 'v1', 'task-topic-A')
  })

  it('没动过的文件没有两面可切，直接就是可编辑的全文', async () => {
    const { container } = mountPanel('topic-A')
    await openChanges(container)
    await fireEvent.click(buttonByText(container, '全部文件')!)
    await flush()

    readFile.mockResolvedValue(textFile('untouched.txt', 'x\n'))
    const row = Array.from(container.querySelectorAll('.file-item')).find((b) =>
      b.textContent?.includes('untouched.txt')
    )
    await fireEvent.click(row!)
    await flush()

    expect(buttonByText(container, '差异'), '没改过的文件不该给「差异」这一面').toBeUndefined()
    expect(editor(container)).toBeTruthy()
  })
})
