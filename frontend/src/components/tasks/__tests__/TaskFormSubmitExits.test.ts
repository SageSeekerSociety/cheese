/**
 * 表单交出去之前的那道实名确认，和几节在不在、交出去的是什么。
 *
 * 钉的是：
 *
 * 1. **实名确认**：第一次打开实名要求再交，先弹隐私确认框；取消（什么都不发，且实名
 *    那一勾退回去）、确认（把拦下的那份原样发出去）、放行之后不再问第二遍。
 * 2. **哪几节在**：一次发好几道（`parametersOnly`）时没有名称和描述，交出去的
 *    `description` / `intro` 是空串；从 PDF 导入时存下的 Markdown 描述，改完存回去的是
 *    编辑器的文档，字一个不少。
 * 3. **点了发布才标红**：打开时不报，交过一次还拦着才报几项。
 *
 * `useI18n` 键透传（按**标签原文**找字段）、`createVuetify`、stub `ResizeObserver` /
 * `visualViewport`、tiptap 换成壳。
 */
import type { Component } from 'vue'

import { nextTick } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-i18n')>()
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

vi.mock('@/components/common/Editor/TipTapEditor.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'TipTapEditorStub',
      setup(_, { expose }) {
        expose({ editor: { getText: () => '正文（测试）。' } })
        return () => h('div', { class: 'tiptap-editor' })
      },
    }),
  }
})

import TaskForm from '../TaskForm.vue'

import i18n from '@/i18n'

beforeAll(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    pageLeft: 0,
    pageTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
})

afterEach(cleanup)

/** 三栏必填都填好的样子（其余走 initialValues 的默认值）。 */
const FILLED = { name: '用 gdb 定位一次段错误', submitterType: 'USER', rank: 1, categoryId: 3 }

interface MountOptions {
  initialData?: Record<string, unknown>
  isEditing?: boolean
  parametersOnly?: boolean
}

function mountForm(options: MountOptions = {}) {
  return render(TaskForm as Component, {
    props: {
      initialData: options.initialData ?? {},
      isEditing: options.isEditing ?? false,
      parametersOnly: options.parametersOnly ?? false,
      classificationTopics: [],
      categories: [{ id: 3, name: '课程作业', displayOrder: 0 }],
    },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

type View = ReturnType<typeof mountForm>

/** 交一次。表单自己拦着（校验不过 / 闸没放行）时提交根本不发生。 */
async function trySubmit(view: View) {
  await fireEvent.submit(view.container.querySelector('form')!)
}

/** 交一次并且真的发出去了，拿回那份 payload。 */
async function submitted(view: View) {
  await trySubmit(view)
  await waitFor(() => expect(view.emitted().submit).toBeTruthy())
  return (view.emitted().submit as unknown[][])[0][0] as Record<string, unknown>
}

/** 弹窗是不是真开着：Vuetify 的浮层第一次画出来之后内容留在 DOM 里（关掉只是把它
 *  藏起来），所以看的是那一层浮层有没有 `--active`，不是「那句话还在不在」。 */
function dialogOpen(title: string): boolean {
  return Array.from(document.querySelectorAll('.v-overlay--active')).some((el) =>
    (el.textContent ?? '').includes(title)
  )
}

/** 那个勾选框。走**原生 click**：`fireEvent.click` 派发的是合成事件，`checked` 不会跟着翻。 */
async function toggle(input: HTMLInputElement) {
  input.click()
  await nextTick()
}

/** 实名那一勾收在「更多设置」里，先点开。 */
async function realNameSwitch(view: View) {
  if (!view.queryByTestId('task-form-real-name')) {
    await fireEvent.click(view.getByTestId('task-form-more'))
  }
  return view.getByLabelText('tasks.form.more.realName', { exact: false }) as HTMLInputElement
}

describe('发题表单：实名要求的隐私确认闸', () => {
  it('第一次勾上实名再交：先弹确认框，一个 submit 都不发', async () => {
    const view = mountForm({ initialData: FILLED })
    await toggle(await realNameSwitch(view))

    await trySubmit(view)
    await waitFor(() => expect(dialogOpen('tasks.form.privacy.title')).toBe(true))
    expect(view.emitted().submit).toBeUndefined()
  })

  it('点「取消」：勾退回去（这道题还是匿名发），一个 submit 都不发', async () => {
    const view = mountForm({ initialData: FILLED })
    await toggle(await realNameSwitch(view))
    await trySubmit(view)
    await waitFor(() => expect(dialogOpen('tasks.form.privacy.title')).toBe(true))

    await fireEvent.click(view.getByText('global.cancel'))

    await waitFor(() => expect(dialogOpen('tasks.form.privacy.title')).toBe(false))
    expect((await realNameSwitch(view)).checked).toBe(false)
    expect(view.emitted().submit).toBeUndefined()
  })

  it('点「了解并接受」：把刚才拦下的那一份原样发出去，带着 requireRealName', async () => {
    const view = mountForm({ initialData: FILLED })
    await toggle(await realNameSwitch(view))
    await trySubmit(view)
    await waitFor(() => expect(dialogOpen('tasks.form.privacy.title')).toBe(true))

    await fireEvent.click(view.getByText('tasks.form.privacy.understood'))

    await waitFor(() => expect(view.emitted().submit).toBeTruthy())
    const payload = (view.emitted().submit as unknown[][])[0][0] as { requireRealName?: boolean }
    expect(payload.requireRealName).toBe(true)
    expect(dialogOpen('tasks.form.privacy.title')).toBe(false)
  })

  it('答应了之后不再问第二遍：同一份表单再交一次直接出去', async () => {
    const view = mountForm({ initialData: FILLED })
    await toggle(await realNameSwitch(view))
    await trySubmit(view)
    await waitFor(() => expect(dialogOpen('tasks.form.privacy.title')).toBe(true))
    await fireEvent.click(view.getByText('tasks.form.privacy.understood'))
    await waitFor(() => expect(view.emitted().submit).toBeTruthy())

    await trySubmit(view)

    await waitFor(() => expect((view.emitted().submit as unknown[][]).length).toBe(2))
    expect(dialogOpen('tasks.form.privacy.title')).toBe(false)
  })

  it('本来就开着实名（打开时就是要求的）：交的时候不拦，直接出去', async () => {
    const view = mountForm({ initialData: { ...FILLED, requireRealName: true } })

    await trySubmit(view)

    await waitFor(() => expect(view.emitted().submit).toBeTruthy())
    const payload = (view.emitted().submit as unknown[][])[0][0] as { requireRealName?: boolean }
    expect(payload.requireRealName).toBe(true)
    expect(dialogOpen('tasks.form.privacy.title')).toBe(false)
  })
})

describe('发题表单：几节在不在、交出去的是什么', () => {
  it('一次发好几道：没有名称和描述，交出去的正文是空的', async () => {
    const view = mountForm({ initialData: { submitterType: 'USER', rank: 1, categoryId: 3 }, parametersOnly: true })

    expect(view.queryByText('tasks.form.name')).toBeNull()
    expect(view.container.querySelector('.tiptap-editor')).toBeNull()

    const payload = await submitted(view)
    expect(payload.description).toBe('')
    expect(payload.intro).toBe('')
  })

  it('从 PDF 导入时存下的 Markdown：存回去的是编辑器的文档，字一个不少', async () => {
    const view = mountForm({
      initialData: { ...FILLED, description: '## 要求\n\n- 提交代码仓库链接' },
      isEditing: true,
    })

    const payload = await submitted(view)
    const saved = JSON.stringify(JSON.parse(payload.description as string))
    expect(JSON.parse(payload.description as string).type).toBe('doc')
    expect(saved).toContain('"heading"')
    expect(saved).toContain('要求')
    expect(saved).toContain('提交代码仓库链接')
    expect(saved).not.toContain('##')
  })

  it('打开时不报缺什么；交过一次还拦着，才报几项', async () => {
    const view = mountForm({ initialData: { submitterType: 'USER' } })

    await waitFor(() => expect(view.emitted().invalid?.at(-1)).toEqual([0]))
    expect(view.queryByText('tasks.form.validation.nameRequired')).toBeNull()

    await trySubmit(view)

    await waitFor(() => expect((view.emitted().invalid?.at(-1) as number[])[0]).toBeGreaterThan(0))
    expect(view.getByText('tasks.form.validation.nameRequired')).toBeTruthy()
    expect(view.emitted().submit).toBeUndefined()
  })
})

describe('发题表单：队伍那几栏只在团队时算数', () => {
  it('团队：三栏都在，交出去带着它们', async () => {
    const view = mountForm({ initialData: { ...FILLED, submitterType: 'TEAM' } })

    expect(view.getByLabelText('tasks.form.minTeamSize')).toBeTruthy()
    expect(view.getByLabelText('tasks.form.maxTeamSize')).toBeTruthy()

    const payload = await submitted(view)

    expect(payload.minTeamSize).toBe(1)
    expect(payload.maxTeamSize).toBe(10)
    expect(payload.teamLockingPolicy).toBe('NO_LOCK')
  })

  it('个人：那三栏根本不画，交出去的也不带它们', async () => {
    const view = mountForm({ initialData: FILLED })

    expect(view.queryByLabelText('tasks.form.minTeamSize')).toBeNull()
    expect(view.queryByLabelText('tasks.form.maxTeamSize')).toBeNull()

    const payload = await submitted(view)

    expect(payload.minTeamSize).toBeUndefined()
    expect(payload.maxTeamSize).toBeUndefined()
    expect(payload.teamLockingPolicy).toBeUndefined()
  })

  it('最大比最小还小：表单拦着，一个 submit 都不发', async () => {
    const view = mountForm({
      initialData: { ...FILLED, submitterType: 'TEAM', minTeamSize: 5, maxTeamSize: 2 },
    })

    await trySubmit(view)

    await waitFor(() => expect(view.queryByText('tasks.form.validation.teamSizeOrder')).toBeTruthy())
    expect(view.emitted().submit).toBeUndefined()
  })
})

describe('发题表单：改题时那条截止时间', () => {
  it('改题默认不设截止（不交 hasDeadline: true）', async () => {
    const view = mountForm({ initialData: FILLED, isEditing: true })

    const payload = await submitted(view)

    expect(payload.deadline).toBeNull()
    expect(payload.hasDeadline).toBe(false)
  })

  it('新发一道题：默认两周后截止', async () => {
    const view = mountForm({ initialData: FILLED })

    const payload = await submitted(view)

    expect(payload.deadline).toBeTypeOf('number')
    expect(payload.hasDeadline).toBeUndefined()
  })
})
