// 「涉及的知识点」那一格的行为（学情分析的底座上，发题 / 改题共用的一件）：
//   1. 采用建议里清单已有一条，是把它的编号报回去；清单里没有的（isNew）报名字，
//      由调用方先加进清单再挂上（这一格没有建知识点的权限）；
//   2. 「忽略」只是这一屏不摆它，不动已挂的编号；
//   3. 已经挂上的名字，「采用」不再问一次；建议都处理完时给一句话；
//   4. 清单读不出来时不判失效：已挂的编号照旧，只写一句读不到，也不摆候选。
import type { SpaceKnowledgePoint, SpaceKnowledgeSuggestion } from '@/types'

import { defineComponent, h, type PropType } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import KnowledgePointField from './KnowledgePointField.vue'

/** 收下报回去的编号和往上发的两件事；**真的把回程接回去**（像两个页面那样 v-model 一圈）。 */
let ids: number[] = []
let adoptedNew: string[] = []
let suggested = 0

const LIST: SpaceKnowledgePoint[] = [
  { id: 1, name: '相关子查询', displayOrder: 0 },
  { id: 2, name: '索引', displayOrder: 1 },
]

const Host = defineComponent({
  props: {
    modelValue: { type: Array as PropType<number[]>, default: () => [] },
    knowledgePoints: { type: Array as PropType<SpaceKnowledgePoint[]>, default: () => [] },
    state: { type: String as PropType<'loading' | 'ready' | 'error'>, default: 'ready' },
    suggestions: { type: Array as PropType<SpaceKnowledgeSuggestion[]>, default: () => [] },
    suggesting: { type: Boolean, default: false },
    suggestFailed: { type: Boolean, default: false },
    canSuggest: { type: Boolean, default: false },
  },
  data() {
    return { chosen: [...this.modelValue] as number[] }
  },
  render() {
    return h(KnowledgePointField, {
      modelValue: this.chosen,
      knowledgePoints: this.knowledgePoints,
      state: this.state,
      suggestions: this.suggestions,
      suggesting: this.suggesting,
      suggestFailed: this.suggestFailed,
      canSuggest: this.canSuggest,
      'onUpdate:modelValue': (next: number[]) => {
        ids = next
        this.chosen = next
      },
      'onAdopt-new': (name: string) => adoptedNew.push(name),
      onSuggest: () => (suggested += 1),
    })
  },
})

function mount(props: Record<string, unknown> = {}) {
  ids = []
  adoptedNew = []
  suggested = 0
  return render(Host, {
    props: { canSuggest: true, ...props },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('KnowledgePointField', () => {
  it('采用一条清单里已有的建议：报回它的编号，这一条就不再摆出来', async () => {
    const view = mount({
      knowledgePoints: LIST,
      suggestions: [{ name: '索引', why: '题面提到建索引', confidence: 'high', isNew: false }],
    })

    expect(view.getByTestId('knowledge-point-suggestion')).toBeTruthy()
    await fireEvent.click(view.getByTestId('knowledge-point-adopt'))
    await waitFor(() => expect(ids).toEqual([2]))
    await waitFor(() => expect(view.queryByTestId('knowledge-point-suggestion')).toBeNull())
    expect(view.getByTestId('knowledge-points-all-handled')).toBeTruthy()
  })

  it('采用一条清单里没有的建议：不自己建，把名字往上发', async () => {
    const view = mount({
      knowledgePoints: LIST,
      suggestions: [{ name: '游标', why: '题面提到游标', confidence: 'medium', isNew: true }],
    })

    expect(view.getByTestId('knowledge-point-suggestion').textContent).toContain(
      'spaces.learningAnalysis.field.notInList'
    )
    await fireEvent.click(view.getByTestId('knowledge-point-adopt'))
    expect(adoptedNew).toEqual(['游标'])
    expect(ids).toEqual([])
  })

  it('忽略一条：只从这一屏收走，不动已挂的编号', async () => {
    const view = mount({
      modelValue: [1],
      knowledgePoints: LIST,
      suggestions: [
        { name: '索引', why: '题面提到建索引', confidence: 'high', isNew: false },
        { name: '游标', why: '题面提到游标', confidence: 'medium', isNew: true },
      ],
    })

    expect(view.getAllByTestId('knowledge-point-suggestion')).toHaveLength(2)
    await fireEvent.click(view.getAllByTestId('knowledge-point-ignore')[0])
    await waitFor(() => expect(view.getAllByTestId('knowledge-point-suggestion')).toHaveLength(1))
    expect(ids).toEqual([])
    expect(view.getByTestId('knowledge-point-chip').textContent).toContain('相关子查询')
  })

  it('chip 上的 ✕ 去掉那一条，报回的编号里没有它', async () => {
    const view = mount({ modelValue: [1, 2], knowledgePoints: LIST })

    await fireEvent.click(view.getByTestId('knowledge-point-chip-remove-1'))
    await waitFor(() => expect(ids).toEqual([2]))
  })

  it('「从清单添加」摊开整份清单，勾一条加一个编号', async () => {
    const view = mount({ knowledgePoints: LIST })

    expect(view.queryByTestId('knowledge-point-list')).toBeNull()
    await fireEvent.click(view.getByTestId('knowledge-point-add'))
    await waitFor(() => expect(view.getByTestId('knowledge-point-list')).toBeTruthy())
    await fireEvent.input(view.getByRole('checkbox', { name: '索引' }), { target: { checked: true } })
    await waitFor(() => expect(ids).toEqual([2]))
  })

  it('清单读不出来：已挂的照旧，只写一句读不到，也不摆候选', async () => {
    const view = mount({ modelValue: [1], knowledgePoints: LIST, state: 'error' })

    expect(view.getByTestId('knowledge-points-error')).toBeTruthy()
    expect(view.queryByTestId('knowledge-point-list')).toBeNull()
    expect(ids).toEqual([])
  })

  it('不能读题面时不摆那颗按钮', () => {
    const view = mount({ canSuggest: false, knowledgePoints: LIST })

    expect(view.queryByTestId('knowledge-point-suggest')).toBeNull()
  })

  it('读题面：按钮往上发一次，读不出时在那一栏里说一句', async () => {
    const view = mount({ knowledgePoints: LIST })
    await fireEvent.click(view.getByTestId('knowledge-point-suggest'))
    expect(suggested).toBe(1)

    cleanup()
    const failed = mount({ knowledgePoints: LIST, suggestFailed: true })
    expect(failed.getByTestId('knowledge-points-suggest-failed')).toBeTruthy()
  })
})
