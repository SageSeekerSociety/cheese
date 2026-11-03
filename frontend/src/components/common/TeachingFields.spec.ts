// 「AI 队友辅导」那几格的控件。空间设置和发题页、改题页共用它，所以这里量的是它出什么：
//   1. 方式四选一；题目那一层多一个「沿用上层设置」，底下给出空间那一份要求；
//   2. 每改一格都把**整份**报回去；
//   3. 人敲的字不要在回程里被改写（那个还没成形的空格）；
//   4. 选上的技能在格子里带着它的说明。
import type { SkillChoice } from '@/composables/useSkillCatalog'
import type { SpaceMaterial, SpaceMaterialsState, SpaceTeaching } from '@/types'

import { defineComponent, h, type PropType } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import TeachingFields from './TeachingFields.vue'

/** 收下报回来的那一份的壳。**真的把回程接回去**（像两个页面那样 v-model 一圈），
 *  否则「人敲的字不被回程改写」那条量不到。 */
let captured: SpaceTeaching | null = null

const Host = defineComponent({
  props: {
    materials: { type: Array as PropType<SpaceMaterial[]>, default: () => [] },
    materialsState: {
      type: String as PropType<SpaceMaterialsState>,
      default: 'ready',
    },
    modelValue: {
      type: Object as PropType<SpaceTeaching | null>,
      default: undefined,
    },
    inherit: { type: Boolean, default: false },
    spaceTeaching: { type: Object as PropType<SpaceTeaching | null>, default: null },
    skillCatalog: { type: Array as PropType<SkillChoice[]>, default: undefined },
  },
  data() {
    return { value: (this.modelValue ?? {}) as SpaceTeaching }
  },
  render() {
    return h(TeachingFields, {
      modelValue: this.value,
      materials: this.materials,
      materialsState: this.materialsState,
      inherit: this.inherit,
      spaceTeaching: this.spaceTeaching,
      skillCatalog: this.skillCatalog,
      'onUpdate:modelValue': (next: SpaceTeaching) => {
        captured = next
        this.value = next
      },
    })
  },
})

function mount(props: Record<string, unknown> = {}) {
  captured = null
  return render(Host, {
    props,
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('TeachingFields', () => {
  it('选上的技能在格子里带着它的说明，不只是一个标识', () => {
    const view = mount({
      modelValue: { skills: ['documents'] },
      skillCatalog: [
        { name: 'documents', description: '读写或修改 Word、PowerPoint、Excel、PDF 文件时用' },
        { name: 'wolfram', description: '用户问可以算出来的问题时用' },
      ],
    })

    const field = view.getByTestId('teaching-skills')
    expect(within(field).getByText('读写或修改 Word、PowerPoint、Excel、PDF 文件时用')).toBeTruthy()
    expect(within(field).queryByText('用户问可以算出来的问题时用')).toBeNull()
  })

  it('三样：方式、补充要求、可读取的资料；方式是四选一', () => {
    const view = mount()

    for (const mode of ['guide', 'example', 'spec', 'free'])
      expect(view.getByText(`spaces.teaching.mode.${mode}`)).toBeTruthy()
    expect(view.queryByText('spaces.teaching.mode.inherit')).toBeNull()
    expect(view.getByLabelText('spaces.teaching.fields.systemPrompt')).toBeTruthy()
    expect(view.getByTestId('teaching-materials-toggle')).toBeTruthy()
  })

  it('参考资料那一栏收起：清单和「库里还没有」都不占地方，点开才出', async () => {
    const view = mount()

    expect(view.getByText('spaces.teaching.fields.materialsNone')).toBeTruthy()
    expect(view.queryByTestId('teaching-materials')).toBeNull()
    expect(view.queryByTestId('teaching-materials-empty')).toBeNull()

    await fireEvent.click(view.getByTestId('teaching-materials-toggle'))
    await waitFor(() => expect(view.getByTestId('teaching-materials-empty')).toBeTruthy())
  })

  it('候选里只有「所有成员」那一档：仅管理员的课件不出现', async () => {
    const view = mount({
      materials: [
        { id: 11, name: '讲义', visibility: 'members' },
        { id: 12, name: '答案', visibility: 'admins' },
      ],
    })

    await fireEvent.click(view.getByTestId('teaching-materials-toggle'))
    await waitFor(() => expect(view.getByTestId('teaching-materials')).toBeTruthy())

    expect(view.getByText('讲义')).toBeTruthy()
    expect(view.queryByText('答案')).toBeNull()
  })

  it('取数状态递到了选择器：读取中就摆读取中，不摆候选', async () => {
    const view = mount({ materialsState: 'loading' })

    expect(view.getByTestId('teaching-materials-loading')).toBeTruthy()
    expect(view.queryByTestId('teaching-materials-toggle')).toBeNull()
  })

  it('改一格报的是整份：选了的方式和写下的要求都在', async () => {
    const view = mount()

    await fireEvent.click(view.getByRole('radio', { name: /spaces.teaching.mode.spec/ }))
    await waitFor(() => expect(captured?.mode).toBe('spec'))
    await fireEvent.update(view.getByLabelText('spaces.teaching.fields.systemPrompt'), '讲完链表了')
    await waitFor(() =>
      expect(captured).toEqual({
        mode: 'spec',
        systemPrompt: '讲完链表了',
        privatePrompt: null,
        learningGoals: [],
        materialIds: [],
        documentIds: [],
        skills: [],
      })
    )
  })

  it('题目那一层：多一个「沿用上层设置」，底下给出空间那一份要求', async () => {
    const view = mount({ inherit: true, spaceTeaching: { mode: 'guide', systemPrompt: '先问他试过什么。' } })

    expect(view.getByText('spaces.teaching.mode.inherit')).toBeTruthy()
    expect(view.getByLabelText('spaces.teaching.fields.taskPrompt')).toBeTruthy()
    expect(view.getByTestId('teaching-space-prompt').textContent).toContain('先问他试过什么。')
  })

  it('人敲的字不在回程里被改写：还没成形的空格留着', async () => {
    const view = mount()
    const prompt = view.getByLabelText('spaces.teaching.fields.systemPrompt') as HTMLTextAreaElement

    await fireEvent.update(prompt, '先写测试 ')
    expect(prompt.value).toBe('先写测试 ')
  })
})
