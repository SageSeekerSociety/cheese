/** 「改动」这一格的主要行为：一棵标着增删的文件树、默认那一面把全部改动连着排、
 *  单独打开一份文件时的差异 / 全文两面、空态、错误态、保存与冲突。
 *
 * 它 1708 行、直接调 9 个接口函数、会写文件（writeFile 失败会丢用户的编辑），而
 * 在这份 spec 之前只有一个 75 行的 PanelChanges.noRepo.spec.ts，只覆盖「项目没接
 * 代码仓库」那一条分支。取数全走 `@/api`，所以这里把那一层整个换成受控的桩：
 * 这一格的行为就是「拿到什么画什么」，而这些桩就是「拿到什么」的那一半。
 *
 * 桩的数据里那条 diff 是真的 git 输出形状，不是随手拼的字符串——树上那个 +N −M
 * 和点开看的那一段是同一份 diff 切出来的（`lib/diff.splitDiffByFile`），喂假的
 * 只会让两边一起错、测试还全绿。
 */
import type { Component } from 'vue'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api'
import { setLocale } from '@/i18n'

// 断言读的是中文界面上的那一行字，语言钉在中文上。
beforeEach(() => setLocale('zh-CN'))

const getForgeConnection = vi.fn()
const getGitDiff = vi.fn()
const listFiles = vi.fn()
const listRoomTasks = vi.fn()
const readFile = vi.fn()
const writeFile = vi.fn()
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getForgeConnection: (...a: unknown[]) => getForgeConnection(...a),
    getGitDiff: (...a: unknown[]) => getGitDiff(...a),
    listFiles: (...a: unknown[]) => listFiles(...a),
    listRoomTasks: (...a: unknown[]) => listRoomTasks(...a),
    readFile: (...a: unknown[]) => readFile(...a),
    writeFile: (...a: unknown[]) => writeFile(...a),
  }
})

// Monaco 不属于这一格的单测：编辑器换成一根「改一下」「按保存」都点得动的桩，
// 它报出来的正是真编辑器会报的那两个事件。
vi.mock('../CodeEditor.vue', () => ({
  default: {
    name: 'CodeEditorStub',
    props: ['modelValue', 'filename', 'readonly'],
    emits: ['update:modelValue', 'save'],
    template:
      '<div class="editor-stub">' +
      '<span class="editor-stub__path">{{ filename }}</span>' +
      '<button type="button" class="editor-stub__edit" @click="$emit(\'update:modelValue\', modelValue + \'// edit\')">改</button>' +
      '<button type="button" class="editor-stub__save" @click="$emit(\'save\')">存</button>' +
      '</div>',
  },
}))

import PanelChangesHost from '../work/PanelChangesHost.vue'

let vuetify: ReturnType<typeof createVuetify>

/** 两处改动、两种状态：改过的文件，和这一支新加的文件。 */
const DIFF = [
  'diff --git a/src/app.ts b/src/app.ts',
  'index 0000000..1111111 100644',
  '--- a/src/app.ts',
  '+++ b/src/app.ts',
  '@@ -1,3 +1,4 @@',
  ' const a = 1',
  '-const b = 2',
  '+const b = 3',
  '+const c = 4',
  'diff --git a/README.md b/README.md',
  'new file mode 100644',
  'index 0000000..2222222 100644',
  '--- /dev/null',
  '+++ b/README.md',
  '@@ -0,0 +1,2 @@',
  '+# hi',
  '+text',
].join('\n')

const FILES = [
  { path: 'src/app.ts', bytes: 120 },
  { path: 'README.md', bytes: 40 },
]

function fileContent(path: string) {
  return {
    path,
    content: 'const a = 1\n',
    version: 'v1',
    bytes: 120,
    binary: false,
    too_large: false,
    source: 'live' as const,
    editable: true,
  }
}

/** 一条开着分支、还在跑的活：改动那一格默认看的就是它。 */
function openTask(id = 't-1', title = '把登录页的报错说清楚') {
  return {
    id,
    project_id: 'p1',
    room_id: 'room-1',
    title,
    status: 'open',
    branch_name: 'cheese/login-copy',
    presentation: { column: 'building', phrase: 'running' },
  }
}

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  getForgeConnection.mockReset().mockResolvedValue({ kind: 'forgejo', connected: true, repo: 'a/b', url: 'x' })
  getGitDiff.mockReset().mockResolvedValue({ diff: DIFF })
  listFiles.mockReset().mockResolvedValue({ data: FILES, total: FILES.length, source: 'live' })
  listRoomTasks.mockReset().mockResolvedValue({ data: [openTask()], total: 1 })
  readFile.mockReset().mockImplementation((_pid: string, path: string) => Promise.resolve(fileContent(path)))
  writeFile.mockReset().mockResolvedValue({ path: 'src/app.ts', version: 'v2' })
})

function mount(props: Record<string, unknown> = {}) {
  return render(PanelChangesHost as unknown as Component, {
    props: { topicId: 'room-1', taskId: 't-1', projectId: 'p1', active: true, ...props },
    global: { plugins: [vuetify] },
  })
}

/** 一棵树里的文件行，按 `row.name`（最后一段）找。 */
function fileRow(name: string): HTMLElement {
  const row = Array.from(document.querySelectorAll('.file-item')).find((el) => el.textContent?.includes(name))
  expect(row, `树上应该有 ${name} 这一行`).toBeTruthy()
  return row as HTMLElement
}

/** 全部改动那一面里某个文件那一段的「打开」：单独看这一份。 */
async function openSection(path: string) {
  const section = await waitFor(() => {
    const el = document.querySelector(`.diff-file[data-path="${path}"]`)
    expect(el, `改动里应该有 ${path} 这一段`).toBeTruthy()
    return el as HTMLElement
  })
  const open = Array.from(section.querySelectorAll('button')).find((b) => b.textContent?.trim() === '打开')
  await fireEvent.click(open!)
}

describe('树上有什么，点开的是什么', () => {
  it('每个文件标着它改了多少；默认那一面把全部改动连着排，不先替人打开哪一份', async () => {
    mount()
    // 树: 这一支改过的文件都在，增删标在行上。
    await waitFor(() => expect(document.querySelector('.file-item')).toBeTruthy())
    expect(fileRow('app.ts').textContent).toContain('+2')
    expect(fileRow('app.ts').textContent).toContain('−1')
    // 新加的文件说的是「新增」，不是 +2。
    expect(fileRow('README.md').textContent).toContain('新增')

    // 两个文件的差异都在，一个接一个；git 的头信息不在屏幕上，@@ 换成了行号区间。
    expect(await screen.findByText('-const b = 2')).toBeTruthy()
    expect(screen.getByText('+const c = 4')).toBeTruthy()
    expect(screen.getByText('+# hi')).toBeTruthy()
    expect(screen.queryByText('@@ -1,3 +1,4 @@')).toBeNull()
    expect(screen.queryByText('index 0000000..1111111 100644')).toBeNull()
    expect(screen.getByText('1–4')).toBeTruthy()
    expect(readFile).not.toHaveBeenCalled()
  })

  it('点树上改过的文件，滚到它那一段，不另开一份', async () => {
    const scrolled: Element[] = []
    const spy = vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(function (this: Element) {
      scrolled.push(this)
    })
    mount()
    await screen.findByText('+# hi')
    await fireEvent.click(fileRow('README.md'))
    await waitFor(() => expect(scrolled.some((el) => el.getAttribute('data-path') === 'README.md')).toBe(true))
    expect(readFile).not.toHaveBeenCalled()
    spy.mockRestore()
  })

  it('点一段的「打开」，单独看这一份；返回之后又是全部改动', async () => {
    mount()
    await openSection('README.md')
    await waitFor(() => expect(readFile).toHaveBeenLastCalledWith('p1', 'README.md', 'room-1', 't-1', 'live'))
    expect(await screen.findByText('+# hi')).toBeTruthy()
    // 别的文件的差异不该还留在屏幕上。
    expect(screen.queryByText('-const b = 2')).toBeNull()

    await fireEvent.click(screen.getByRole('button', { name: '返回全部改动' }))
    expect(await screen.findByText('-const b = 2')).toBeTruthy()
    expect(screen.getByText('+# hi')).toBeTruthy()
  })

  it('没有差异的文件只有一面：不摆「差异 / 全文」这个开关', async () => {
    listFiles.mockResolvedValue({
      data: [...FILES, { path: 'notes.txt', bytes: 10 }],
      total: 3,
      source: 'live',
    })
    // 对话里一枚 chip 指着一份这件任务没改过的文件。
    const panel = ref<{ openFile: (path: string) => Promise<void> } | null>(null)
    const Host = defineComponent({
      setup: () => () =>
        h(PanelChangesHost as unknown as Component, {
          ref: panel,
          topicId: 'room-1',
          taskId: 't-1',
          projectId: 'p1',
          active: true,
        }),
    })
    render(Host, { global: { plugins: [vuetify] } })
    await openSection('src/app.ts')
    expect(await screen.findByText('差异')).toBeTruthy()
    await panel.value?.openFile('notes.txt')
    await waitFor(() => expect(readFile).toHaveBeenLastCalledWith('p1', 'notes.txt', 'room-1', 't-1', 'live'))
    expect(screen.queryByText('差异')).toBeNull()
    expect(document.querySelector('.editor-stub__path')?.textContent).toBe('notes.txt')
  })
})

describe('这一支的活', () => {
  it('活还在跑时，看的是它的工作树', async () => {
    mount()
    await openSection('src/app.ts')
    await waitFor(() => expect(readFile).toHaveBeenCalledWith('p1', 'src/app.ts', 'room-1', 't-1', 'live'))
    expect(listFiles).toHaveBeenCalledWith('p1', 'room-1', 't-1', 'live')
    expect(getGitDiff).toHaveBeenCalledWith('p1', 'room-1', 't-1', 'live')
  })

  it('改文件保存：把内容连同读到的那一版一起写回去', async () => {
    mount()
    await openSection('src/app.ts')
    await waitFor(() => expect(readFile).toHaveBeenCalled())
    // 打开时看差异，所以先切到「编辑」（活还开着，这一面是能改的）。
    await fireEvent.click(await screen.findByText('编辑'))
    await fireEvent.click(await screen.findByText('改'))
    await fireEvent.click(screen.getByText('保存'))
    await waitFor(() =>
      expect(writeFile).toHaveBeenCalledWith('p1', 'src/app.ts', 'const a = 1\n// edit', 'room-1', 'v1', 't-1')
    )
  })

  it('保存撞上 409：说清冲突，两条出路都由人点', async () => {
    writeFile.mockRejectedValue(new ApiError(409, '这个文件已经被改过了'))
    mount()
    await openSection('src/app.ts')
    await waitFor(() => expect(readFile).toHaveBeenCalled())
    await fireEvent.click(await screen.findByText('编辑'))
    await fireEvent.click(await screen.findByText('改'))
    await fireEvent.click(screen.getByText('保存'))
    expect(await screen.findByText(/这个文件已被修改/)).toBeTruthy()
    // 冲突不是报错：那一格没有挂红。
    expect(screen.queryByText('这个文件已经被改过了')).toBeNull()

    readFile.mockClear()
    await fireEvent.click(screen.getByText('载入最新版本'))
    // 「载入最新版本」= 丢掉自己的改动，重读这一版。
    await waitFor(() => expect(readFile).toHaveBeenCalledWith('p1', 'src/app.ts', 'room-1', 't-1', 'live'))
    await waitFor(() => expect(screen.queryByText(/这个文件已被修改/)).toBeNull())
  })

  it('列文件失败：那句错挂出来，并给一条换版本的出路', async () => {
    listFiles.mockRejectedValue(new Error('文件服务挂了'))
    mount()
    expect(await screen.findByText('文件服务挂了')).toBeTruthy()
    getGitDiff.mockClear()
    await fireEvent.click(screen.getByText('切换到已提交版本'))
    await waitFor(() => expect(getGitDiff).toHaveBeenCalledWith('p1', 'room-1', 't-1', 'committed'))
  })

  it('这一支没有改动时，树说的是「暂无改动」而不是空着', async () => {
    getGitDiff.mockResolvedValue({ diff: '' })
    listFiles.mockResolvedValue({ data: [], total: 0, source: 'live' })
    mount()
    expect((await screen.findAllByText('暂无改动')).length).toBeGreaterThan(0)
    expect(readFile).not.toHaveBeenCalled()
  })

  it('一帧新数据来了（refreshTick）就静默重取差异', async () => {
    const { rerender } = mount()
    await waitFor(() => expect(getGitDiff).toHaveBeenCalled())
    const before = getGitDiff.mock.calls.length
    await rerender({ topicId: 'room-1', taskId: 't-1', projectId: 'p1', active: true, refreshTick: 1 })
    await waitFor(() => expect(getGitDiff.mock.calls.length).toBeGreaterThan(before))
    expect(getGitDiff).toHaveBeenLastCalledWith('p1', 'room-1', 't-1', 'live')
  })
})
