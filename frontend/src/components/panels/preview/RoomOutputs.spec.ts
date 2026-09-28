/**
 * 这个房间里的东西。
 *
 * 摆出来的东西属于这个房间，所以这一块只回答两件事：这个房间里都有什么，以及把其
 * 中一份留进资料库那个动作。跑着的应用不在里面 —— 它是一个进程，没有文件可留。
 *
 * 还有列表的长度：一个跑久了的房间能摆出三十几样，全摊在这里会把上面那条应用条顶
 * 出去，所以小标题那一行是折叠开关。文案走 i18n，所以这一份把语言钉在中文上（和
 * WorkPanelPreview.test.ts 一样）—— 断言读的是人真的看见的那一行字。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import RoomOutputs from './RoomOutputs.vue'

import { setLocale } from '@/i18n'

vi.mock('@/api', () => ({
  listRoomOutputs: vi.fn(),
  saveRoomOutputToLibrary: vi.fn(),
  listDocumentTemplates: vi.fn(),
  newFromTemplate: vi.fn(),
}))

const { listRoomOutputs, saveRoomOutputToLibrary, listDocumentTemplates, newFromTemplate } = await import('@/api')

const vuetify = createVuetify({ components, directives })

afterEach(cleanup)

const REPORT = {
  path: 'out/评审简报.docx',
  mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  kind: 'file' as const,
  shown_at: '2026-09-20T10:00:00Z',
}
const APP = {
  path: 'Vue dev server',
  mime: 'application/x-cheese-app',
  kind: 'app' as const,
  shown_at: '2026-09-20T09:00:00Z',
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  setLocale('zh-CN')
  vi.mocked(listRoomOutputs).mockResolvedValue({ data: [REPORT, APP], total: 2 })
  vi.mocked(saveRoomOutputToLibrary).mockResolvedValue({ name: '评审简报.docx' })
})

function mount(topicId = 't1') {
  return render(RoomOutputs, { props: { topicId }, global: { plugins: [vuetify] } })
}

/** 摆出来过的一串文件：i 越大越旧，和接口一样新的在前。 */
function manyFiles(count: number) {
  return Array.from({ length: count }, (_, i) => ({
    path: `out/文件-${i + 1}.docx`,
    mime: REPORT.mime,
    kind: 'file' as const,
    shown_at: `2026-09-20T${String(23 - (i % 24)).padStart(2, '0')}:00:00Z`,
  }))
}

/** 那几行文件，按屏幕上的顺序。 */
function rowNames(container: Element): string[] {
  return Array.from(container.querySelectorAll('.outs-row__name')).map((el) => el.textContent?.trim() ?? '')
}

function toggle(container: Element): HTMLButtonElement {
  const button = container.querySelector<HTMLButtonElement>('[data-testid="room-outputs-toggle"]')
  expect(button, '找不到折叠开关').toBeTruthy()
  return button!
}

function expandAll(container: Element): HTMLButtonElement | null {
  return container.querySelector<HTMLButtonElement>('[data-testid="room-outputs-expand-all"]')
}

describe('这个房间里的东西', () => {
  it('列出摆出来过的文件，跑着的应用不在里面', async () => {
    const { container } = mount()

    await waitFor(() => expect(container.textContent).toContain('评审简报.docx'))
    expect(container.textContent).not.toContain('Vue dev server')
  })

  it('房间里什么都没摆出来时，仍然能从模板新建一份', async () => {
    vi.mocked(listRoomOutputs).mockResolvedValue({ data: [], total: 0 })

    const { container, getByTestId } = mount()

    await waitFor(() => expect(listRoomOutputs).toHaveBeenCalled())
    expect(getByTestId('new-from-template')).toBeTruthy()
    expect(container.querySelectorAll('.outs-row')).toHaveLength(0)
  })

  it('选一份模板，按给的名字在房间里建出来并打开它', async () => {
    vi.mocked(listDocumentTemplates).mockResolvedValue({
      data: [{ id: 'weekly', name: '周报', suffix: 'docx', about: '本周完成、下周计划' }],
      total: 1,
    })
    vi.mocked(newFromTemplate).mockResolvedValue({ path: '文档/周报.docx', version: 'v1' })
    const { getByTestId, findByText, getByText, emitted } = mount()

    await fireEvent.click(getByTestId('new-from-template'))
    await fireEvent.click(await findByText('周报（.docx）'))
    await fireEvent.click(getByText('新建并打开'))

    await waitFor(() => expect(newFromTemplate).toHaveBeenCalledWith('t1', 'weekly', '文档/周报.docx'))
    await waitFor(() => expect(emitted().open).toEqual([['文档/周报.docx']]))
  })

  it('取消就什么都不建', async () => {
    vi.mocked(listDocumentTemplates).mockResolvedValue({
      data: [{ id: 'report', name: '报告', suffix: 'docx', about: '' }],
      total: 1,
    })
    const { getByTestId, findByText, getByText } = mount()

    await fireEvent.click(getByTestId('new-from-template'))
    await fireEvent.click(await findByText('报告（.docx）'))
    await fireEvent.click(getByText('取消'))

    expect(newFromTemplate).not.toHaveBeenCalled()
  })

  it('点名字就把那一份开出来 —— 上面那块只看得到最后一样', async () => {
    const { container, getByText, emitted } = mount()
    await waitFor(() => expect(container.textContent).toContain('评审简报.docx'))

    await fireEvent.click(getByText('评审简报.docx'))

    expect(emitted().open).toEqual([['out/评审简报.docx']])
  })

  it('存进资料库之后说清它在那边叫什么', async () => {
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('评审简报.docx'))

    const save = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.trim() === '保存到资料库')
    await fireEvent.click(save!)

    await waitFor(() => expect(saveRoomOutputToLibrary).toHaveBeenCalledWith('t1', 'out/评审简报.docx'))
    // 撞名时资料库那边会加 `(2)`，所以说的是它在那里的真名。
    await waitFor(() => expect(container.textContent).toContain('已存进资料库：评审简报.docx'))
  })

  it('存不进去时说出来，而不是装作按过了', async () => {
    vi.mocked(saveRoomOutputToLibrary).mockRejectedValue(new Error('你不是这个项目的成员'))

    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('评审简报.docx'))

    const save = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.trim() === '保存到资料库')
    await fireEvent.click(save!)

    await waitFor(() => expect(container.textContent).toContain('你不是这个项目的成员'))
  })
})

describe('这个房间里的东西：多了就得收得住', () => {
  it('不过五样的时候全摊开——没有可收的东西', async () => {
    vi.mocked(listRoomOutputs).mockResolvedValue({ data: manyFiles(3), total: 3 })

    const { container } = mount()
    await waitFor(() => expect(rowNames(container)).toHaveLength(3))

    expect(toggle(container).getAttribute('aria-expanded')).toBe('true')
    expect(expandAll(container)).toBeNull()
  })

  it('超过五样时默认只露最近五样，末尾给「展开全部 N 项」', async () => {
    vi.mocked(listRoomOutputs).mockResolvedValue({ data: manyFiles(32), total: 32 })

    const { container, findByText } = mount()
    await waitFor(() => expect(rowNames(container)).toHaveLength(5))

    // 新的在前，所以露的是最近这五样，不是随便五样。
    expect(rowNames(container)).toEqual(['文件-1.docx', '文件-2.docx', '文件-3.docx', '文件-4.docx', '文件-5.docx'])
    expect(toggle(container).getAttribute('aria-expanded')).toBe('false')
    expect(await findByText('展开全部 32 项')).toBeTruthy()
  })

  it('点小标题那一行就摊开全部，再点一次收回去', async () => {
    vi.mocked(listRoomOutputs).mockResolvedValue({ data: manyFiles(8), total: 8 })

    const { container } = mount()
    await waitFor(() => expect(rowNames(container)).toHaveLength(5))

    await fireEvent.click(toggle(container))
    await waitFor(() => expect(rowNames(container)).toHaveLength(8))
    expect(toggle(container).getAttribute('aria-expanded')).toBe('true')
    // 全在屏幕上了，就不再说「还有多少」。
    expect(expandAll(container)).toBeNull()

    await fireEvent.click(toggle(container))
    await waitFor(() => expect(rowNames(container)).toHaveLength(5))
    expect(toggle(container).getAttribute('aria-expanded')).toBe('false')
  })

  it('点「展开全部」也摊开，和点那一行是一回事', async () => {
    vi.mocked(listRoomOutputs).mockResolvedValue({ data: manyFiles(7), total: 7 })

    const { container } = mount()
    await waitFor(() => expect(rowNames(container)).toHaveLength(5))

    await fireEvent.click(expandAll(container)!)
    await waitFor(() => expect(rowNames(container)).toHaveLength(7))
    expect(toggle(container).getAttribute('aria-expanded')).toBe('true')
  })

  it('短列表收起来就是收起来——不留五行做引子', async () => {
    vi.mocked(listRoomOutputs).mockResolvedValue({ data: manyFiles(3), total: 3 })

    const { container } = mount()
    await waitFor(() => expect(rowNames(container)).toHaveLength(3))

    await fireEvent.click(toggle(container))
    await waitFor(() => expect(rowNames(container)).toHaveLength(0))
    expect(toggle(container).getAttribute('aria-expanded')).toBe('false')
    // 收起来是这一块的事，上面那条应用条、还有「从模板新建」都不受影响。
    expect(container.querySelector('[data-testid="new-from-template"]')).toBeTruthy()
  })

  it('收起／摊开按话题记住：重挂还是那个样子，换个话题各记各的', async () => {
    vi.mocked(listRoomOutputs).mockResolvedValue({ data: manyFiles(9), total: 9 })

    const first = mount('t-a')
    await waitFor(() => expect(rowNames(first.container)).toHaveLength(5))
    await fireEvent.click(toggle(first.container))
    await waitFor(() => expect(rowNames(first.container)).toHaveLength(9))
    first.unmount()

    // 同一个话题：记住的是摊开过。
    const again = mount('t-a')
    await waitFor(() => expect(rowNames(again.container)).toHaveLength(9))
    expect(toggle(again.container).getAttribute('aria-expanded')).toBe('true')
    again.unmount()

    // 另一个话题没按过，回到默认的收起。
    const other = mount('t-b')
    await waitFor(() => expect(rowNames(other.container)).toHaveLength(5))
    expect(toggle(other.container).getAttribute('aria-expanded')).toBe('false')
  })

  it('收起来过的短列表也记得住', async () => {
    vi.mocked(listRoomOutputs).mockResolvedValue({ data: manyFiles(2), total: 2 })

    const first = mount('t-c')
    await waitFor(() => expect(rowNames(first.container)).toHaveLength(2))
    await fireEvent.click(toggle(first.container))
    await waitFor(() => expect(rowNames(first.container)).toHaveLength(0))
    first.unmount()

    const again = mount('t-c')
    await waitFor(() => expect(listRoomOutputs).toHaveBeenCalled())
    await waitFor(() => expect(rowNames(again.container)).toHaveLength(0))
    expect(toggle(again.container).getAttribute('aria-expanded')).toBe('false')
  })
})
