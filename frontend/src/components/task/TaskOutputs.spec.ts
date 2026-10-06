/**
 * 任务的产出。
 *
 * AI 队友在这件任务里摆出来的东西属于这件任务，所以这一块只回答两件事：这件任务里都
 * 有什么，以及把其中一份留进资料库那个动作。跑着的应用不在里面 —— 它是一个进程，没有
 * 文件可留。文案走 i18n，所以这一份把语言钉在中文上 —— 断言读的是人真的看见的那一行字。
 */
import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import TaskOutputs from './TaskOutputs.vue'

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
  setLocale('zh-CN')
  vi.mocked(listRoomOutputs).mockResolvedValue({ data: [REPORT, APP], total: 2 })
  vi.mocked(saveRoomOutputToLibrary).mockResolvedValue({ name: '评审简报.docx' })
})

function mount(topicId = 't1') {
  return render(TaskOutputs, { props: { topicId }, global: { plugins: [vuetify] } })
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

describe('任务的产出', () => {
  it('列出摆出来过的文件，跑着的应用不在里面', async () => {
    const { container } = mount()

    await waitFor(() => expect(container.textContent).toContain('评审简报.docx'))
    expect(container.textContent).not.toContain('Vue dev server')
  })

  it('任务里什么都没摆出来时，仍然能从模板新建一份', async () => {
    vi.mocked(listRoomOutputs).mockResolvedValue({ data: [], total: 0 })

    const { container, getByTestId } = mount()

    await waitFor(() => expect(listRoomOutputs).toHaveBeenCalled())
    expect(getByTestId('new-from-template')).toBeTruthy()
    expect(container.querySelectorAll('.outs-row')).toHaveLength(0)
  })

  it('选一份模板，按给的名字在任务里建出来并打开它', async () => {
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

describe('任务的产出：列表', () => {
  it('摆出来过的文件全都列着，新的在前', async () => {
    vi.mocked(listRoomOutputs).mockResolvedValue({ data: manyFiles(32), total: 32 })

    const { container } = mount()
    await waitFor(() => expect(rowNames(container)).toHaveLength(32))

    expect(rowNames(container).slice(0, 2)).toEqual(['文件-1.docx', '文件-2.docx'])
  })

  it('reload 重读列表：一轮结束时新摆出来的那一样要出现', async () => {
    vi.mocked(listRoomOutputs).mockResolvedValue({ data: manyFiles(1), total: 1 })
    // 概览拿着 ref 调它的 reload，这里照样用一个 ref 去按。
    const outputs = ref<{ reload: () => Promise<void> } | null>(null)
    const Host = defineComponent(() => () => h(TaskOutputs, { ref: outputs, topicId: 't1' }))
    const { container } = render(Host, { global: { plugins: [vuetify] } })
    await waitFor(() => expect(rowNames(container)).toHaveLength(1))

    vi.mocked(listRoomOutputs).mockResolvedValue({ data: manyFiles(2), total: 2 })
    await outputs.value!.reload()

    await waitFor(() => expect(rowNames(container)).toHaveLength(2))
  })
})
