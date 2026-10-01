/**
 * 表单交出去之前的那两道闸，和几张卡在不在。
 *
 * `TaskForm.vue` 要拆成一件接线用的容器 + 一堆只吃 props 的画法（#2143），而下面这几
 * 件事今天一条测试都没有：模板挪个位置、`v-if` 少写一个、`emit` 挪到别处，现有的
 * 三份 spec 全都照样绿。
 *
 * 钉的是两件事：
 *
 * 1. **两道确认闸**（都是「先拦住，问一句，再决定发不发」）：第一次打开实名要求时交
 *    卷要弹隐私确认框，视频链接不是哔哩哔哩要弹提示框；两道闸各自的三条出路 ——
 *    取消（什么都不发，且实名那一勾要退回去）、确认（把上一次拦下的那份原样发出去）、
 *    以及放行之后不再问第二遍。
 * 2. **哪几张卡在**：`parametersOnly`（PDF 批量发布）里没有题目名、没有赛题详情、
 *    没有视频链接，交出去的 `description` / `intro` 是空串；`descriptionFormat:
 *    'markdown'` 走纯文本那一路，存的就是那段 markdown 本身。
 *
 * 玩法照 `TaskFormPublishChecks.test.ts`：`useI18n` 键透传（按**标签原文**找字段）、
 * `createVuetify`、stub `ResizeObserver` / `visualViewport`、tiptap 换成壳。
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
  descriptionFormat?: 'markdown' | 'tiptap'
  originalDescription?: string
}

function mountForm(options: MountOptions = {}) {
  return render(TaskForm as Component, {
    props: {
      submitButtonText: '提交',
      initialData: options.initialData ?? {},
      isEditing: options.isEditing ?? false,
      parametersOnly: options.parametersOnly ?? false,
      descriptionFormat: options.descriptionFormat ?? 'tiptap',
      originalDescription: options.originalDescription ?? '',
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

const realNameSwitch = (view: View) =>
  view.getByLabelText('tasks.form.requireRealName', { exact: false }) as HTMLInputElement

describe('发题表单：实名要求的隐私确认闸', () => {
  it('第一次勾上实名再交：先弹确认框，一个 submit 都不发', async () => {
    const view = mountForm({ initialData: FILLED })
    await toggle(realNameSwitch(view))

    await trySubmit(view)
    await waitFor(() => expect(dialogOpen('tasks.form.privacy.title')).toBe(true))
    expect(view.emitted().submit).toBeUndefined()
  })

  it('点「取消」：勾退回去（这道题还是匿名发），一个 submit 都不发', async () => {
    const view = mountForm({ initialData: FILLED })
    await toggle(realNameSwitch(view))
    await trySubmit(view)
    await waitFor(() => expect(dialogOpen('tasks.form.privacy.title')).toBe(true))

    await fireEvent.click(view.getByText('global.cancel'))

    await waitFor(() => expect(dialogOpen('tasks.form.privacy.title')).toBe(false))
    expect(realNameSwitch(view).checked).toBe(false)
    expect(view.emitted().submit).toBeUndefined()
  })

  it('点「了解并接受」：把刚才拦下的那一份原样发出去，带着 requireRealName', async () => {
    const view = mountForm({ initialData: FILLED })
    await toggle(realNameSwitch(view))
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
    await toggle(realNameSwitch(view))
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

describe('发题表单：视频链接的确认闸', () => {
  it('填写能当视频看的链接（哔哩哔哩）：不拦，直接出去', async () => {
    const view = mountForm({ initialData: FILLED })
    await fireEvent.update(view.getByLabelText('tasks.form.video.label'), 'https://www.bilibili.com/video/BV1xx411c7mD')

    await trySubmit(view)

    await waitFor(() => expect(view.emitted().submit).toBeTruthy())
    expect(dialogOpen('tasks.form.video.dialogTitle')).toBe(false)
  })

  it('https 但不是能播的视频：先弹提示框，一个 submit 都不发', async () => {
    const view = mountForm({ initialData: FILLED })
    await fireEvent.update(view.getByLabelText('tasks.form.video.label'), 'https://example.com/video')

    await trySubmit(view)

    await waitFor(() => expect(dialogOpen('tasks.form.video.dialogTitle')).toBe(true))
    expect(view.emitted().submit).toBeUndefined()
  })

  it('点「取消」：什么都不发，链接还摆在那儿，再交一次照样拦', async () => {
    const view = mountForm({ initialData: FILLED })
    await fireEvent.update(view.getByLabelText('tasks.form.video.label'), 'https://example.com/video')
    await trySubmit(view)
    await waitFor(() => expect(dialogOpen('tasks.form.video.dialogTitle')).toBe(true))

    await fireEvent.click(view.getByText('global.cancel'))
    await waitFor(() => expect(dialogOpen('tasks.form.video.dialogTitle')).toBe(false))
    expect(view.emitted().submit).toBeUndefined()

    await trySubmit(view)
    await waitFor(() => expect(dialogOpen('tasks.form.video.dialogTitle')).toBe(true))
    expect(view.emitted().submit).toBeUndefined()
  })

  it('点「继续保存」：链接原样带走，这一份真的发出去了', async () => {
    const view = mountForm({ initialData: FILLED })
    await fireEvent.update(view.getByLabelText('tasks.form.video.label'), 'https://example.com/video')
    await trySubmit(view)
    await waitFor(() => expect(dialogOpen('tasks.form.video.dialogTitle')).toBe(true))

    await fireEvent.click(view.getByText('tasks.form.video.dialogContinue'))

    await waitFor(() => expect(view.emitted().submit).toBeTruthy())
    const payload = (view.emitted().submit as unknown[][])[0][0] as { videoUrl?: string | null }
    expect(payload.videoUrl).toBe('https://example.com/video')
  })
})

describe('发题表单：几张卡在不在', () => {
  it('parametersOnly：没有题目名、没有赛题详情、没有视频链接', () => {
    const view = mountForm({ parametersOnly: true, initialData: FILLED })

    expect(view.queryByLabelText('tasks.form.taskName')).toBeNull()
    expect(view.queryByLabelText('tasks.form.markdownDescription')).toBeNull()
    expect(view.queryByLabelText('tasks.form.video.label')).toBeNull()
  })

  it('parametersOnly：名字是那份「PDF 批量发布参数」，交出去的正文是空的', async () => {
    // 名字这一栏在批量发布里是表单自己填的，草稿那份里没有 `name`。
    const view = mountForm({ parametersOnly: true, initialData: { ...FILLED, name: undefined } })

    const payload = await submitted(view)

    expect(payload.name).toBe('tasks.form.pdfParametersName')
    expect(payload.description).toBe('')
    expect(payload.intro).toBe('')
  })

  it('普通发题：三张卡都在（题目名、赛题详情、视频链接）', () => {
    const view = mountForm({ initialData: FILLED })

    expect(view.getByLabelText('tasks.form.taskName')).toBeTruthy()
    expect(view.queryByLabelText('tasks.form.markdownDescription')).toBeNull()
    expect(view.container.querySelector('.tiptap-editor')).toBeTruthy()
    expect(view.getByLabelText('tasks.form.video.label')).toBeTruthy()
  })

  it('markdown 格式：走纯文本那一路，存下来的就是那段 markdown 本身', async () => {
    const view = mountForm({
      initialData: FILLED,
      descriptionFormat: 'markdown',
      originalDescription: '# 课程资料\n\n先读这一篇。',
    })

    const box = view.getByLabelText('tasks.form.markdownDescription') as HTMLTextAreaElement
    expect(box.value).toBe('# 课程资料\n\n先读这一篇。')

    await fireEvent.update(box, '# 改过的标题')
    const payload = await submitted(view)

    expect(payload.description).toBe('# 改过的标题')
    expect(payload.intro).toBe('# 改过的标题')
  })

  it('tiptap 格式：存下来的是那份 JSON，intro 是编辑器里的正文', async () => {
    const view = mountForm({ initialData: FILLED })

    const payload = await submitted(view)

    expect(JSON.parse(payload.description as string)).toEqual({ type: 'doc', content: [{ type: 'paragraph' }] })
    expect(payload.intro).toBe('正文（测试）。')
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
