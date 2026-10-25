// 编辑器那条横条上读者按得着的东西：存、取消，和「别人先改过」时那个「载入最新的」。
//
// 这一只只画（会话那一包由宿主取），所以这里递一份记着调用的假包进去。Monaco 不装：
// 存与取消走的是横条上的按钮，和编辑器里怎么敲无关。
import type { PanelPreviewTextEdit } from '../../../composables/panelPreview/textEdit'

import { nextTick } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import PreviewMarkdownEdit from './PreviewMarkdownEdit.vue'

import { setLocale } from '@/i18n'

vi.mock('../../CodeEditor.vue', () => ({
  default: {
    name: 'CodeEditor',
    props: ['modelValue', 'filename', 'readonly'],
    emits: ['update:modelValue', 'save'],
    template: '<div data-testid="editor" :data-readonly="readonly ? \'yes\' : \'no\'" />',
  },
}))

const vuetify = createVuetify({ components, directives })

function bundle(over: Partial<PanelPreviewTextEdit> = {}): PanelPreviewTextEdit {
  return {
    draft: { value: '# 一\n' } as never,
    error: { value: '' } as never,
    saving: { value: false } as never,
    conflicted: { value: false } as never,
    pinnedPath: { value: 'notes/记录.md' } as never,
    canEdit: { value: true } as never,
    begin: vi.fn(),
    cancel: vi.fn(),
    save: vi.fn(async () => true),
    reload: vi.fn(async () => {}),
    setDraft: vi.fn(),
    ...over,
  } as PanelPreviewTextEdit
}

function mount(edit: PanelPreviewTextEdit) {
  return render(PreviewMarkdownEdit, {
    props: { edit },
    global: { plugins: [vuetify] },
  })
}

beforeEach(() => setLocale('zh-CN'))
afterEach(() => vi.clearAllMocks())

it('横条上写的是钉住的那一份，不是屏幕上现在摆的那一份', () => {
  // 编辑器开着时面板可能已经把这一格换成了别的文件；标题跟着面板走，读者就会以为
  // 自己在改另一份。
  mount(bundle())
  expect(screen.getByText('notes/记录.md')).toBeTruthy()
})

it('存：按下去叫会话存，存成了报 saved，没存成不报', async () => {
  const save = vi.fn(async () => true)
  const { emitted } = mount(bundle({ save }))

  await fireEvent.click(screen.getByTestId('text-edit-save'))

  expect(save).toHaveBeenCalled()
  expect(emitted().saved).toBeTruthy()
})

it('存不下去时不报 saved：编辑还开着，读者还能接着改', async () => {
  const { emitted } = mount(bundle({ save: vi.fn(async () => false) }))

  await fireEvent.click(screen.getByTestId('text-edit-save'))

  expect(emitted().saved).toBeFalsy()
})

it('别人先改过：提示条上就有一个「载入最新的」，按下去读最新那一版', async () => {
  // 提示条让人「载入最新的」，那就得当场能载入：这一格（指定了文件的那一栏）没有顶栏
  // 上那个刷新按钮，指着一件点不到的事等于没救。
  const reload = vi.fn(async () => {})
  mount(bundle({ conflicted: { value: true } as never, reload }))

  expect(screen.getByText(/被别人改过/)).toBeTruthy()
  await fireEvent.click(screen.getByTestId('text-edit-reload'))

  expect(reload).toHaveBeenCalled()
})

it('没冲突时不摆那个按钮：平常这一点地方不该多一个能点坏东西的键', () => {
  mount(bundle())
  expect(screen.queryByTestId('text-edit-reload')).toBeNull()
})

it('手机上编辑器只读、桌面可写：软键盘配 Monaco 不是一件值得存下去的事', async () => {
  // 和「改动」那一格同一条（那里是 `:readonly="!mdAndUp"`）。
  const width = window.innerWidth
  const set = async (px: number) => {
    window.innerWidth = px
    window.dispatchEvent(new Event('resize'))
    await nextTick()
  }
  try {
    mount(bundle())
    await set(1600)
    expect(screen.getByTestId('editor').getAttribute('data-readonly')).toBe('no')
    await set(400)
    expect(screen.getByTestId('editor').getAttribute('data-readonly')).toBe('yes')
  } finally {
    window.innerWidth = width
    window.dispatchEvent(new Event('resize'))
  }
})

it('冲突优先于普通报错：两句话同时成立时，读者看见的是能动手的那一句', () => {
  mount(
    bundle({
      conflicted: { value: true } as never,
      error: { value: '保存失败' } as never,
    })
  )
  expect(screen.getByText(/被别人改过/)).toBeTruthy()
  expect(screen.queryByText('保存失败')).toBeNull()
})
