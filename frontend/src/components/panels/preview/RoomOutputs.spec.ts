/**
 * 这个房间里的东西。
 *
 * 摆出来的东西属于这个房间，所以这一块只回答两件事：这个房间里都有什么，以及把其
 * 中一份留进资料库那个动作。跑着的应用不在里面 —— 它是一个进程，没有文件可留。
 *
 * 它挂在总览最底下、文档下面，默认收起，小标题那一行是折叠开关。文案走 i18n，所以这一份把语言钉在中文上（和
 * WorkPanelPreview.test.ts 一样）—— 断言读的是人真的看见的那一行字。
 *
 * 列表、模板、三个动作都是 props 进来的（取数在 `composables/usePanelOverview.ts`），
 * 所以这里喂数据、递替身，不看请求。「一轮结束时重读列表」在那只组合式函数的用例里。
 */
import type { Component } from 'vue'
import type { DocumentTemplate, RoomOutput } from '@/types/roomOutput'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import RoomOutputs from './RoomOutputs.vue'

import { setLocale } from '@/i18n'

const vuetify = createVuetify({ components, directives })

afterEach(cleanup)

const REPORT: RoomOutput = {
  path: 'out/评审简报.docx',
  mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  kind: 'file',
  shown_at: '2026-09-20T10:00:00Z',
}
const APP: RoomOutput = {
  path: 'Vue dev server',
  mime: 'application/x-cheese-app',
  kind: 'app',
  shown_at: '2026-09-20T09:00:00Z',
}

interface MountOpts {
  outputs?: RoomOutput[]
  templates?: DocumentTemplate[]
  loadTemplates?: () => void
  saveToLibrary?: (path: string) => Promise<string>
  createFromTemplate?: (templateId: string, path: string) => Promise<void>
}

beforeEach(() => {
  localStorage.clear()
  setLocale('zh-CN')
})

function mount(opts: MountOpts = {}, topicId = 't1') {
  return render(RoomOutputs as unknown as Component, {
    props: { topicId, outputs: [REPORT, APP], ...opts },
    global: { plugins: [vuetify] },
  })
}

/** 摆出来过的一串文件：i 越大越旧，和接口一样新的在前。 */
function manyFiles(count: number): RoomOutput[] {
  return Array.from({ length: count }, (_, i) => ({
    path: `out/文件-${i + 1}.docx`,
    mime: REPORT.mime,
    kind: 'file' as const,
    shown_at: `2026-09-20T${String(23 - (i % 24)).padStart(2, '0')}:00:00Z`,
  }))
}

const WEEKLY: DocumentTemplate = { id: 'weekly', name: '周报', suffix: 'docx', about: '本周完成、下周计划' }

/** 那几行文件，按屏幕上的顺序。 */
function rowNames(container: Element): string[] {
  return Array.from(container.querySelectorAll('.outs-row__name')).map((el) => el.textContent?.trim() ?? '')
}

function toggle(container: Element): HTMLButtonElement {
  const button = container.querySelector<HTMLButtonElement>('[data-testid="room-outputs-toggle"]')
  expect(button, '找不到折叠开关').toBeTruthy()
  return button!
}

/** 列表是不是收着：v-show 写在 style 属性上。读属性而不是 `style.display`——jsdom
 *  里 v-show 收回第二次时属性已经是 none，`style.display` 却还读出空串。 */
function listHidden(container: Element): boolean {
  const el = container.querySelector<HTMLElement>('.outs__list')
  expect(el, '找不到列表').toBeTruthy()
  return /display:\s*none/.test(el!.getAttribute('style') ?? '')
}

describe('这个房间里的东西', () => {
  it('列出摆出来过的文件，跑着的应用不在里面', () => {
    const { container } = mount()

    expect(container.textContent).toContain('评审简报.docx')
    expect(container.textContent).not.toContain('Vue dev server')
  })

  it('房间里什么都没摆出来时，仍然能从模板新建一份', () => {
    const { container, getByTestId } = mount({ outputs: [] })

    expect(getByTestId('new-from-template')).toBeTruthy()
    expect(container.querySelectorAll('.outs-row')).toHaveLength(0)
  })

  it('选一份模板，按给的名字在房间里建出来', async () => {
    const createFromTemplate = vi.fn().mockResolvedValue(undefined)
    const { getByTestId, findByText, getByText } = mount({ templates: [WEEKLY], createFromTemplate })

    await fireEvent.click(getByTestId('new-from-template'))
    await fireEvent.click(await findByText('周报（.docx）'))
    await fireEvent.click(getByText('新建并打开'))

    await waitFor(() => expect(createFromTemplate).toHaveBeenCalledWith('weekly', '文档/周报.docx'))
  })

  it('取消就什么都不建', async () => {
    const createFromTemplate = vi.fn().mockResolvedValue(undefined)
    const { getByTestId, findByText, getByText } = mount({ templates: [WEEKLY], createFromTemplate })

    await fireEvent.click(getByTestId('new-from-template'))
    await fireEvent.click(await findByText('周报（.docx）'))
    await fireEvent.click(getByText('取消'))

    expect(createFromTemplate).not.toHaveBeenCalled()
  })

  it('摊开「从模板新建」时去要一次模板', async () => {
    const loadTemplates = vi.fn()
    const { getByTestId } = mount({ loadTemplates })

    await fireEvent.click(getByTestId('new-from-template'))

    expect(loadTemplates).toHaveBeenCalledTimes(1)
  })

  it('点名字就把那一份开出来 —— 上面那块只看得到最后一样', async () => {
    const { getByText, emitted } = mount()

    await fireEvent.click(getByText('评审简报.docx'))

    expect(emitted().open).toEqual([['out/评审简报.docx']])
  })

  it('存进资料库之后说清它在那边叫什么', async () => {
    const saveToLibrary = vi.fn().mockResolvedValue('评审简报.docx')
    const { container } = mount({ saveToLibrary })

    const save = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.trim() === '保存到资料库')
    await fireEvent.click(save!)

    await waitFor(() => expect(saveToLibrary).toHaveBeenCalledWith('out/评审简报.docx'))
    // 撞名时资料库那边会加 `(2)`，所以说的是它在那里的真名。
    await waitFor(() => expect(container.textContent).toContain('已存进资料库：评审简报.docx'))
  })

  it('存不进去时说出来，而不是装作按过了', async () => {
    const saveToLibrary = vi.fn().mockRejectedValue(new Error('你不是这个项目的成员'))
    const { container } = mount({ saveToLibrary })

    const save = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.trim() === '保存到资料库')
    await fireEvent.click(save!)

    await waitFor(() => expect(container.textContent).toContain('你不是这个项目的成员'))
  })
})

describe('这个房间里的东西：挂在总览底下，默认收起', () => {
  it('没按过就收起：只露小标题和件数，列表不显示', () => {
    const { container } = mount({ outputs: manyFiles(3) })

    expect(toggle(container).textContent).toContain('3')
    expect(toggle(container).getAttribute('aria-expanded')).toBe('false')
    expect(listHidden(container)).toBe(true)
    // 收起来是列表的事，「从模板新建」照样在。
    expect(container.querySelector('[data-testid="new-from-template"]')).toBeTruthy()
  })

  it('点小标题那一行摊开全部，再点一次收回去', async () => {
    const { container } = mount({ outputs: manyFiles(32) })

    await fireEvent.click(toggle(container))
    expect(toggle(container).getAttribute('aria-expanded')).toBe('true')
    await waitFor(() => expect(listHidden(container)).toBe(false))
    // 新的在前。
    expect(rowNames(container).slice(0, 2)).toEqual(['文件-1.docx', '文件-2.docx'])

    await fireEvent.click(toggle(container))
    expect(toggle(container).getAttribute('aria-expanded')).toBe('false')
    await waitFor(() => expect(listHidden(container)).toBe(true))
  })

  it('收起／摊开按话题记住：重挂还是那个样子，换个话题各记各的', async () => {
    const first = mount({ outputs: manyFiles(9) }, 't-a')
    await fireEvent.click(toggle(first.container))
    first.unmount()

    const again = mount({ outputs: manyFiles(9) }, 't-a')
    expect(toggle(again.container).getAttribute('aria-expanded')).toBe('true')
    again.unmount()

    const other = mount({ outputs: manyFiles(9) }, 't-b')
    expect(toggle(other.container).getAttribute('aria-expanded')).toBe('false')
  })

  it('它还在预览格里时存下的摊开状态不沿用', () => {
    localStorage.setItem('cheesex.roomOutputsExpanded.v1:t-old', '1')

    const { container } = mount({}, 't-old')

    expect(toggle(container).getAttribute('aria-expanded')).toBe('false')
  })
})
