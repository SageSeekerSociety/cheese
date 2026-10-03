// 「给 AI 队友的指导」那几格的控件（#944）。两处页面共用它，所以这里量的是它出什么：
//   1. 默认只摆三格（对 AI 的要求、当前周次、参考资料），其余三格折在「高级选项」里；
//   2. 每改一格都把**整份**报回去 —— 不是一格一格补，接口那一头也是整份替换；
//   3. 人敲的字不要在回程里被改写（那个还没成形的空格）；
//   4. 那份默认要求只在点了按钮之后才进框，留空就是一条都不加。
import type { SpaceTeaching } from '@/types'

import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
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
  data: () => ({ value: {} as SpaceTeaching }),
  render() {
    return h(TeachingFields, {
      modelValue: this.value,
      'onUpdate:modelValue': (next: SpaceTeaching) => {
        captured = next
        this.value = next
      },
    })
  },
})

function mount() {
  captured = null
  return render(Host, { global: { plugins: [createVuetify({ components, directives })] } })
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('TeachingFields', () => {
  it('默认只摆三格；高级选项里的三格展开才出现', async () => {
    const view = mount()

    expect(view.getByLabelText('spaces.teaching.fields.systemPrompt')).toBeTruthy()
    expect(view.getByLabelText('spaces.teaching.fields.currentWeek')).toBeTruthy()
    expect(view.getByTestId('teaching-materials-empty')).toBeTruthy()
    expect(view.queryByLabelText('spaces.teaching.fields.allowedTopics')).toBeNull()

    await fireEvent.click(view.getByText('spaces.teaching.fields.advanced'))
    await waitFor(() => expect(view.getByLabelText('spaces.teaching.fields.allowedTopics')).toBeTruthy())
    expect(view.getByLabelText('spaces.teaching.fields.knowledgeIds')).toBeTruthy()
  })

  it('改一格报的是整份：填上的那格在内，其余空格落成 null / []', async () => {
    const view = mount()

    await fireEvent.update(view.getByLabelText('spaces.teaching.fields.systemPrompt'), '讲完链表了')
    await waitFor(() =>
      expect(captured).toEqual({
        systemPrompt: '讲完链表了',
        currentWeek: null,
        allowedTopics: [],
        avoidInCode: [],
        materialIds: [],
        knowledgeIds: [],
      })
    )

    await fireEvent.update(view.getByLabelText('spaces.teaching.fields.currentWeek'), '3')
    await waitFor(() => expect(captured?.currentWeek).toBe(3))
    // 前一格还在 —— 报的是整份，不是这一次改的那一格。
    expect(captured?.systemPrompt).toBe('讲完链表了')
  })

  it('高级选项里的清单按逗号切（中英文都认）', async () => {
    const view = mount()
    await fireEvent.click(view.getByText('spaces.teaching.fields.advanced'))
    await waitFor(() => expect(view.getByLabelText('spaces.teaching.fields.allowedTopics')).toBeTruthy())

    await fireEvent.update(view.getByLabelText('spaces.teaching.fields.allowedTopics'), '链表，栈, 队列')
    await waitFor(() => expect(captured?.allowedTopics).toEqual(['链表', '栈', '队列']))
  })

  it('人敲的字不在回程里被改写：还没成形的空格留着', async () => {
    const view = mount()
    const input = view.getByLabelText('spaces.teaching.fields.currentWeek') as HTMLInputElement

    await fireEvent.update(input, '03')
    expect(input.value).toBe('03')
  })

  it('那份默认要求不预填：框里空着，点了按钮才进去', async () => {
    const view = mount()
    const prompt = view.getByLabelText('spaces.teaching.fields.systemPrompt') as HTMLTextAreaElement

    expect(prompt.value).toBe('')

    await fireEvent.click(view.getByTestId('teaching-use-default-template'))
    await waitFor(() => expect(prompt.value).toBe('spaces.teaching.defaultTemplate'))
  })
})
