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
  startModeLocked?: boolean
}

function mountForm(options: MountOptions = {}) {
  return render(TaskForm as Component, {
    props: {
      initialData: options.initialData ?? {},
      isEditing: options.isEditing ?? false,
      parametersOnly: options.parametersOnly ?? false,
      startModeLocked: options.startModeLocked ?? false,
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

  it('个人：那三栏根本不画', async () => {
    const view = mountForm({ initialData: FILLED })

    expect(view.queryByLabelText('tasks.form.minTeamSize')).toBeNull()
    expect(view.queryByLabelText('tasks.form.maxTeamSize')).toBeNull()
  })

  it('团队改成个人再改回团队：填过的人数还在，交出去的也是它', async () => {
    const view = mountForm({ initialData: { ...FILLED, submitterType: 'TEAM', minTeamSize: 3, maxTeamSize: 5 } })

    await fireEvent.click(view.getByRole('radio', { name: 'tasks.form.individual' }))
    await waitFor(() => expect(view.queryByLabelText('tasks.form.minTeamSize')).toBeNull())
    await fireEvent.click(view.getByRole('radio', { name: 'tasks.form.team' }))

    await waitFor(() => expect((view.getByLabelText('tasks.form.minTeamSize') as HTMLInputElement).value).toBe('3'))
    expect((view.getByLabelText('tasks.form.maxTeamSize') as HTMLInputElement).value).toBe('5')
    const payload = await submitted(view)
    expect(payload.submitterType).toBe('TEAM')
    expect([payload.minTeamSize, payload.maxTeamSize]).toEqual([3, 5])
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

describe('发题表单：分了阶段时没指阶段的提交项和评分要点', () => {
  it('归第一个阶段：在那一组里看得到，交出去也写着那个阶段', async () => {
    const view = mountForm({
      isEditing: true,
      initialData: {
        ...FILLED,
        stages: [
          { id: 'need', name: '需求分析', description: '', dueAt: null },
          { id: 'impl', name: '实现', description: '', dueAt: null },
        ],
        submissionSchema: [
          { prompt: '需求文档', type: 'TEXT', stage: null },
          { prompt: '代码', type: 'FILE', stage: 'impl' },
        ],
        rubric: [{ item: '完整', points: 10, criteria: '', stage: null }],
      },
    })

    const groups = view.getByTestId('task-form-submission-items').children
    expect((groups[0].querySelector('input') as HTMLInputElement).value).toBe('需求文档')
    expect(view.getByTestId('task-form-rubric').children[0].querySelector('input')).not.toBeNull()

    const sent = await submitted(view)
    expect(sent.submissionSchema).toEqual([
      { prompt: '需求文档', type: 'TEXT', stage: 'need' },
      { prompt: '代码', type: 'FILE', stage: 'impl' },
    ])
    expect(sent.rubric).toEqual([{ item: '完整', points: 10, criteria: '', stage: 'need' }])
  })
})

describe('发题表单：两个阶段一个一个删掉', () => {
  it('先删的阶段带走它的提交项和评分要点，最后一个阶段的留下，交出去时不分阶段', async () => {
    const view = mountForm({
      isEditing: true,
      initialData: {
        ...FILLED,
        stages: [
          { id: 'need', name: '需求分析', description: '', dueAt: null },
          { id: 'impl', name: '实现', description: '', dueAt: null },
        ],
        submissionSchema: [
          { prompt: '需求文档', type: 'TEXT', stage: 'need' },
          { prompt: '代码', type: 'FILE', stage: 'impl' },
        ],
        rubric: [
          { item: '完整', points: 10, criteria: '', stage: 'need' },
          { item: '正确', points: 20, criteria: '', stage: 'impl' },
        ],
      },
    })

    const removes = () => view.getAllByRole('button', { name: 'tasks.form.stages.remove' })
    // 删一个阶段先问一句，答「删除」才删。
    const confirm = async () =>
      fireEvent.click(await waitFor(() => view.getByRole('button', { name: 'tasks.form.stages.removeConfirm' })))
    await fireEvent.click(removes()[1])
    await confirm()
    await waitFor(() => expect(removes()).toHaveLength(1))
    await fireEvent.click(removes()[0])
    await confirm()
    const sent = await submitted(view)
    expect(sent.stages).toEqual([])
    expect(sent.submissionSchema).toEqual([{ prompt: '需求文档', type: 'TEXT', stage: null }])
    expect(sent.rubric).toEqual([{ item: '完整', points: 10, criteria: '', stage: null }])
  })
})

describe('发题表单：直接开始的题', () => {
  it('截止日期写成提交截止、可以不填：空着也发得出去，就是没有截止', async () => {
    const view = mountForm({ isEditing: true, initialData: { ...FILLED, startMode: 'DIRECT', deadline: null } })

    expect(view.container.textContent).toContain('tasks.form.dueOn')
    expect(view.container.textContent).not.toContain('tasks.form.registration')
    const sent = await submitted(view)

    expect(sent.startMode).toBe('DIRECT')
    expect(sent.deadline ?? null).toBeNull()
  })

  it('新发一道题：不选就是直接开始', async () => {
    const view = mountForm({ initialData: FILLED })

    const sent = await submitted(view)

    expect(sent.startMode).toBe('DIRECT')
  })

  it('已经有人申请或开始做了：开始方式灰着改不了，写明为什么', async () => {
    const view = mountForm({ isEditing: true, startModeLocked: true, initialData: { ...FILLED, startMode: 'APPLY' } })

    const radios = Array.from(
      view.container.querySelectorAll<HTMLInputElement>('[data-agent-field="start_mode"] input[type="radio"]')
    )
    expect(radios.length).toBe(2)
    expect(radios.every((r) => r.disabled)).toBe(true)
    expect(view.container.textContent).toContain('tasks.form.start.locked')
  })

  it.each([
    ['直接开始', 'DIRECT', ['free', 'freeHintStart', 'lockedStart', 'lockedHintStart']],
    ['申请制', 'APPLY', ['free', 'freeHint', 'locked', 'lockedHint']],
  ])('%s的团队题：队伍成员变更说的是从哪一刻起', async (_, startMode, said) => {
    const view = mountForm({ initialData: { ...FILLED, submitterType: 'TEAM', startMode } })

    const choices = Array.from(view.container.querySelectorAll('.tf-choices .v-radio'))
      .map((r) => r.textContent ?? '')
      .join(' ')
    expect(choices.match(/tasks\.form\.teamLocking\.\w+/g)).toEqual(said.map((k) => `tasks.form.teamLocking.${k}`))
  })
})
