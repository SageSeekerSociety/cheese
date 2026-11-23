/**
 * 任务页的两种摆法：选哪一种按任务记住，默认是对话；没得选的时候（手机、平板横放的浮层）永远是
 * 对话。进编辑器收起项目侧栏，离开时放回原样；读者在编辑器里自己展开了侧栏，就照他的来。
 */
import type { AppNav } from '@/components/common/Navigation/appNav'

import { computed, defineComponent, h, nextTick, ref } from 'vue'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import { useSearchShortcut, useTaskForm } from './editorForm'

import { provideAppNav } from '@/components/common/Navigation/appNav'

/** A desktop shell whose sidebar folds and unfolds, and a task page in it. */
function mount(opts: { taskId?: string; sidebarFolded?: boolean } = {}) {
  const taskId = ref(opts.taskId ?? 'task-1')
  const available = ref(true)
  const folded = ref(opts.sidebarFolded ?? false)
  const shown = ref(true)
  let form!: ReturnType<typeof useTaskForm>
  const Page = defineComponent(() => {
    form = useTaskForm({ taskId: () => taskId.value, available: () => available.value })
    return () => h('div')
  })
  render(
    defineComponent(() => {
      provideAppNav({
        foldSidebar: computed(() => () => (folded.value = true)),
        unfoldSidebar: computed(() => (folded.value ? () => (folded.value = false) : null)),
      } as unknown as AppNav)
      return () => (shown.value ? h(Page) : null)
    })
  )
  return { form: () => form, taskId, available, folded, shown }
}

beforeEach(() => localStorage.clear())

describe('the task page form', () => {
  it('opens a task in 对话 until the reader picks 编辑器, and remembers that per task', async () => {
    const page = mount()
    expect(page.form().editor.value).toBe(false)
    page.form().choose('editor')
    expect(page.form().editor.value).toBe(true)

    page.taskId.value = 'task-2'
    await nextTick()
    expect(page.form().editor.value).toBe(false)

    page.taskId.value = 'task-1'
    await nextTick()
    expect(page.form().editor.value).toBe(true)
  })

  it('is always 对话 where the page cannot be laid out side by side', async () => {
    const page = mount()
    page.form().choose('editor')
    page.available.value = false
    await nextTick()
    expect(page.form().editor.value).toBe(false)
    page.available.value = true
    await nextTick()
    expect(page.form().editor.value).toBe(true)
  })

  it('folds the project sidebar in 编辑器 and puts it back on leaving', async () => {
    const page = mount()
    page.form().choose('editor')
    await nextTick()
    expect(page.folded.value).toBe(true)
    page.form().choose('chat')
    await nextTick()
    expect(page.folded.value).toBe(false)

    page.form().choose('editor')
    await nextTick()
    page.shown.value = false
    await nextTick()
    expect(page.folded.value).toBe(false)
  })

  it('once the reader has unfolded the sidebar inside 编辑器, leaving does not touch it again', async () => {
    const page = mount()
    page.form().choose('editor')
    await nextTick()
    // The reader unfolds it, then folds it again themselves.
    page.folded.value = false
    await nextTick()
    page.folded.value = true
    await nextTick()
    page.form().choose('chat')
    await nextTick()
    expect(page.folded.value).toBe(true)
  })

  it('leaves a sidebar that was already folded folded when leaving 编辑器', async () => {
    const page = mount({ sidebarFolded: true })
    page.form().choose('editor')
    await nextTick()
    page.form().choose('chat')
    await nextTick()
    expect(page.folded.value).toBe(true)
  })
})

describe('the search shortcut', () => {
  function page(enabled: () => boolean) {
    let asked = 0
    render(
      defineComponent(() => {
        useSearchShortcut({ enabled, run: () => (asked += 1) })
        return () => h('textarea', { 'aria-label': 'composer' })
      })
    )
    return { asked: () => asked }
  }

  it('asks for the search on Ctrl+Shift+F or ⌘⇧F from anywhere on the page, even inside a field', async () => {
    const p = page(() => true)
    const field = document.querySelector('textarea')!
    await fireEvent.keyDown(field, { key: 'F', code: 'KeyF', ctrlKey: true, shiftKey: true })
    expect(p.asked()).toBe(1)
    await fireEvent.keyDown(document.body, { key: 'f', code: 'KeyF', metaKey: true, shiftKey: true })
    expect(p.asked()).toBe(2)
    // Ctrl+F alone is the browser's find in page.
    await fireEvent.keyDown(field, { key: 'f', code: 'KeyF', ctrlKey: true })
    expect(p.asked()).toBe(2)
  })

  it('does nothing where the page has no 编辑器 form (a phone, the floating panel)', async () => {
    const p = page(() => false)
    await fireEvent.keyDown(document.body, { key: 'F', code: 'KeyF', ctrlKey: true, shiftKey: true })
    expect(p.asked()).toBe(0)
  })
})
