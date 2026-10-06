/** 「改动」这一格的主要行为：一棵标着增删的文件树、一份文件的差异 / 全文两面、
 *  选中、空态、错误态、保存与冲突。
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
const getGitLog = vi.fn()
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
    getGitLog: (...a: unknown[]) => getGitLog(...a),
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
  getGitLog.mockReset().mockResolvedValue({ data: [], total: 0 })
  getGitDiff.mockReset().mockResolvedValue({ diff: DIFF })
  listFiles.mockReset().mockResolvedValue({ data: FILES, total: FILES.length, source: 'live' })
  listRoomTasks.mockReset().mockResolvedValue({ data: [], total: 0 })
  readFile.mockReset().mockImplementation((_pid: string, path: string) => Promise.resolve(fileContent(path)))
  writeFile.mockReset().mockResolvedValue({ path: 'src/app.ts', version: 'v2' })
})

function mount(props: Record<string, unknown> = {}) {
  return render(PanelChangesHost as unknown as Component, {
    props: { topicId: 'room-1', projectId: 'p1', active: true, ...props },
    global: { plugins: [vuetify] },
  })
}

/** 一棵树里的文件行，按 `row.name`（最后一段）找。 */
function fileRow(name: string): HTMLElement {
  const row = Array.from(document.querySelectorAll('.file-item')).find((el) => el.textContent?.includes(name))
  expect(row, `树上应该有 ${name} 这一行`).toBeTruthy()
  return row as HTMLElement
}

describe('树上有什么，点开的是什么', () => {
  it('每个文件标着它改了多少，右边点开的是这一份自己的差异', async () => {
    mount()
    // 树: 这一支改过的文件都在，增删标在行上。
    expect(await screen.findByText('app.ts')).toBeTruthy()
    expect(screen.getByText('README.md')).toBeTruthy()
    expect(screen.getByText('+2')).toBeTruthy()
    expect(screen.getByText('−1')).toBeTruthy()
    // 新加的文件说的是「新增」，不是 +2。
    expect(screen.getByText('新增')).toBeTruthy()

    // 没打开过任何文件时的第一份，是清单里的第一份改过的文件。
    await waitFor(() => expect(readFile).toHaveBeenCalledWith('p1', 'src/app.ts', 'room-1', null, 'committed'))
    expect(screen.getByText('src/app.ts')).toBeTruthy()
    // 逐文件 diff：增删各自一行，hunk 头也在。
    expect(screen.getByText('-const b = 2')).toBeTruthy()
    expect(screen.getByText('+const c = 4')).toBeTruthy()
    expect(screen.getByText('@@ -1,3 +1,4 @@')).toBeTruthy()
  })

  it('点树上的另一份文件，右边换成它自己的差异', async () => {
    mount()
    await screen.findByText('app.ts')
    await fireEvent.click(fileRow('README.md'))
    expect(await screen.findByText('+# hi')).toBeTruthy()
    expect(readFile).toHaveBeenLastCalledWith('p1', 'README.md', 'room-1', null, 'committed')
    // 上一份的差异不该还留在屏幕上。
    expect(screen.queryByText('-const b = 2')).toBeNull()
  })

  it('没有差异的文件只有一面：不摆「差异 / 全文」这个开关', async () => {
    listFiles.mockResolvedValue({
      data: [...FILES, { path: 'notes.txt', bytes: 10 }],
      total: 3,
      source: 'live',
    })
    mount()
    await screen.findByText('app.ts')
    expect(screen.getByText('差异')).toBeTruthy()
    await fireEvent.click(fileRow('notes.txt'))
    await waitFor(() => expect(readFile).toHaveBeenLastCalledWith('p1', 'notes.txt', 'room-1', null, 'committed'))
    expect(screen.queryByText('差异')).toBeNull()
    expect(document.querySelector('.editor-stub__path')?.textContent).toBe('notes.txt')
  })
})

describe('这一支的活', () => {
  beforeEach(() => {
    listRoomTasks.mockResolvedValue({ data: [openTask()], total: 1 })
  })

  it('活还在跑时，打开的是它的工作树，来源写在横条上', async () => {
    mount()
    expect(await screen.findByText('把登录页的报错说清楚')).toBeTruthy()
    await waitFor(() => expect(readFile).toHaveBeenCalledWith('p1', 'src/app.ts', 'room-1', 't-1', 'live'))
    expect(listFiles).toHaveBeenCalledWith('p1', 'room-1', 't-1', 'live')
    expect(getGitDiff).toHaveBeenCalledWith('p1', 'room-1', 't-1', 'live')
  })

  it('改文件保存：把内容连同读到的那一版一起写回去', async () => {
    mount()
    await waitFor(() => expect(readFile).toHaveBeenCalled())
    // 默认看差异，所以先切到「编辑」（活还开着，这一面是能改的）。
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
    expect(await screen.findByText('暂无改动')).toBeTruthy()
    expect(readFile).not.toHaveBeenCalled()
  })

  it('一帧新数据来了（refreshTick）就静默重取提交与差异', async () => {
    const { rerender } = mount()
    await waitFor(() => expect(readFile).toHaveBeenCalled())
    const before = getGitDiff.mock.calls.length
    await rerender({ topicId: 'room-1', projectId: 'p1', active: true, refreshTick: 1 })
    await waitFor(() => expect(getGitDiff.mock.calls.length).toBeGreaterThan(before))
    expect(getGitDiff).toHaveBeenLastCalledWith('p1', 'room-1', 't-1', 'live')
  })
})

describe('房间改动这一页', () => {
  it('每个任务一行，铺开看它改了哪些文件，点一份进那件活', async () => {
    listRoomTasks.mockResolvedValue({
      data: [openTask('t-1', '把登录页的报错说清楚'), openTask('t-2', '补上导出按钮')],
      total: 2,
    })
    mount({ taskId: undefined, active: true })
    expect(await screen.findByText('把登录页的报错说清楚')).toBeTruthy()
    expect(screen.getByText('补上导出按钮')).toBeTruthy()
    // 每件活各标着自己改了几个文件；改了哪些是展开了才知道的（三十件活各铺一屏
    // 就没法找了）。
    expect(screen.getAllByText('2 个文件')).toHaveLength(2)
    expect(screen.queryByText('src/app.ts')).toBeNull()

    const toggle = document.querySelector('.task-change-toggle') as HTMLElement
    await fireEvent.click(toggle)
    expect(await screen.findByText('src/app.ts')).toBeTruthy()

    await fireEvent.click(screen.getByText('src/app.ts'))
    await waitFor(() => expect(readFile).toHaveBeenCalledWith('p1', 'src/app.ts', 'room-1', 't-1', 'live'))
  })

  it('「项目当前代码」是另一条来源，点它回到只读的项目代码', async () => {
    listRoomTasks.mockResolvedValue({
      data: [openTask('t-1', '把登录页的报错说清楚'), openTask('t-2', '补上导出按钮')],
      total: 2,
    })
    mount()
    await screen.findByText('把登录页的报错说清楚')
    await fireEvent.click(screen.getByText('项目当前代码'))
    await waitFor(() => expect(readFile).toHaveBeenCalledWith('p1', 'src/app.ts', 'room-1', null, 'committed'))
  })
})
