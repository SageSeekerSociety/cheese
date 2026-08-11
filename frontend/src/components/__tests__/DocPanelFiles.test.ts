/** 文件 panel: a save must land in the topic the user is actually looking at.
 *
 * The panel kept `openPath` and the editor draft across a topic switch. Open a
 * file in topic A, switch to topic B, press 保存 — and A's draft was written
 * into B's worktree, at A's path. That is cross-topic data corruption, so it is
 * pinned here at the component level: mount the real panel, drive it through the
 * DOM, and assert on what reaches the API.
 *
 * Also pinned: binary and oversized files open read-only (no 保存 button), and a
 * save carries the version it was based on so the backend can reject a lost race.
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
      '<textarea class="stub-editor" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
}))

const listFiles = vi.fn()
const readFile = vi.fn()
const writeFile = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
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
    getGitDiff: vi.fn().mockResolvedValue({ diff: '' }),
    getPreview: vi.fn().mockResolvedValue(null),
    getTopicUsage: vi.fn().mockResolvedValue(null),
    getProjectUsage: vi.fn().mockResolvedValue(null),
  }
})

import DocPanel from '../DocPanel.vue'

function topic(id: string): Topic {
  return { id, project_id: 'p1', title: `话题 ${id}`, status: 'active' } as Topic
}

function textFile(path: string, content: string, version = 'v1'): FileContent {
  return { path, content, version, bytes: content.length, binary: false, too_large: false }
}

function mountPanel(id: string) {
  const vuetify = createVuetify({ components, directives })
  return render(DocPanel, {
    props: { topic: topic(id), activityTick: 0 },
    global: { plugins: [vuetify] },
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

/** Open the 文件 drawer. */
async function openFilesTool(container: Element) {
  const btn = buttons(container).find((b) => b.getAttribute('title')?.includes('文件'))
  expect(btn, '找不到 文件 工具按钮').toBeTruthy()
  await fireEvent.click(btn!)
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
    await openFilesTool(container) // the drawer closed with the switch

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
    expect(writeFile).toHaveBeenCalledWith('p1', 'a.py', '在 B 里改的\n', 'topic-B', 'vB')
  })

  it('保存时带上读到的版本，好让后端拦住抢跑的写', async () => {
    const { container } = mountPanel('topic-A')
    await flush()
    await openFilesTool(container)

    await fireEvent.update(editor(container)!, '人改过的\n')
    await flush()
    await fireEvent.click(buttonByText(container, '保存')!)
    await flush()

    expect(writeFile).toHaveBeenCalledWith('p1', 'a.py', '人改过的\n', 'topic-A', 'v1')
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
    expect(container.textContent).toContain('二进制文件，不能当文本编辑')
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
    expect(writeFile).toHaveBeenLastCalledWith('p1', 'a.py', '人改过的\n', 'topic-A', null)
  })
})
