import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import LearningView from './LearningView.vue'

import i18n, { setLocale, t } from '@/i18n'
import {
  EMPTY_LEARNING_QUEUES,
  LEARNING_FILTERS,
  LEARNING_OUTLINE,
  LEARNING_QUESTIONS,
  LEARNING_QUEUES,
} from '@/views/demo/catalogScenesMisc3Fixtures'

type Props = InstanceType<typeof LearningView>['$props']
const base: Props = {
  loading: false,
  outlineLoading: false,
  learningFilters: LEARNING_FILTERS,
  queues: LEARNING_QUEUES,
  questions: LEARNING_QUESTIONS,
  outline: null,
  failed: false,
  errorDetail: null,
  selected: [],
  student: null,
  knowledgePoint: null,
}
function mount(props: Partial<Props> = {}) {
  return render(LearningView, {
    props: { ...base, ...props },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}
beforeEach(() => {
  setLocale('zh-CN')
  vi.stubGlobal(
    'visualViewport',
    Object.assign(new EventTarget(), { width: 1280, height: 800, offsetLeft: 0, offsetTop: 0, scale: 1 })
  )
  vi.stubGlobal('devicePixelRatio', 1)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('学习看板的独立画面', () => {
  it('首次读取显示进度，刷新保留队列、选择与提纲而不重现首次进度', async () => {
    const view = mount({ loading: true, queues: null, questions: [] })
    expect(view.container.querySelector('.an-section > .v-progress-linear')).not.toBeNull()
    expect(view.queryByText(t('spaces.analytics.learning.questions.empty'))).toBeNull()
    await view.rerender({
      queues: LEARNING_QUEUES,
      questions: LEARNING_QUESTIONS,
      selected: ['b-1'],
      outline: LEARNING_OUTLINE,
      outlineLoading: true,
    })
    expect(view.container.querySelector('.an-section > .v-progress-linear')).toBeNull()
    expect(view.queryByText(LEARNING_OUTLINE.title)).not.toBeNull()
    expect(view.container.querySelectorAll('input:checked')).toHaveLength(2)
    expect(view.container.querySelector('.v-btn--loading')).not.toBeNull()
  })

  it('没有成员项目优先于失败，失败则替换旧内容并提供重试', async () => {
    const view = mount({
      learningFilters: { ...LEARNING_FILTERS, projectCount: 0 },
      failed: true,
      errorDetail: 'timeout',
    })
    expect(view.queryByText(t('spaces.analytics.learning.noProjects'))).not.toBeNull()
    expect(view.queryByText('timeout')).toBeNull()
    await view.rerender({ learningFilters: LEARNING_FILTERS })
    expect(view.queryByText('timeout')).not.toBeNull()
    expect(view.queryByText(LEARNING_QUESTIONS[0].quote)).toBeNull()
    await fireEvent.click(view.getByRole('button', { name: t('global.loadError.retry') }))
    expect(view.emitted().load).toEqual([[]])
  })

  it('只有读取为空时显示对应空态，不把复核信号不可用误报为空', async () => {
    const view = mount({ queues: EMPTY_LEARNING_QUEUES, questions: [] })
    expect(view.queryByText(t('spaces.analytics.learning.queue.noFlagged'))).not.toBeNull()
    expect(view.queryByText(t('spaces.analytics.learning.queue.noStuck'))).not.toBeNull()
    expect(view.queryByText(t('spaces.analytics.learning.questions.empty'))).not.toBeNull()
    await view.rerender({ queues: LEARNING_QUEUES })
    expect(view.queryByText(LEARNING_QUEUES.reviewFlag.reason)).not.toBeNull()
    expect(view.queryByText(t('spaces.analytics.learning.queue.noFlagged'))).toBeNull()
  })

  it('两个筛选独立发送模型变更，也能反映外部传入的值', async () => {
    const view = mount()
    await fireEvent.mouseDown(view.getByRole('combobox', { name: t('spaces.analytics.learning.student') }))
    await fireEvent.click(await screen.findByRole('option', { name: '爱丽丝' }))
    expect(view.emitted()['update:student']).toEqual([['alice']])
    await fireEvent.mouseDown(view.getByRole('combobox', { name: t('spaces.analytics.learning.knowledgePoint') }))
    await fireEvent.click(await screen.findByRole('option', { name: '瑞利散射' }))
    expect(view.emitted()['update:knowledgePoint']).toEqual([[7]])
    await view.rerender({ student: 'alice', knowledgePoint: 7 })
    await view.rerender({ student: 'bob', knowledgePoint: null })
    expect(view.container.querySelectorAll('.v-select__selection-text')[0]?.textContent).toBe('鲍勃')
    expect(view.container.querySelectorAll('.v-select__selection-text')[1]?.textContent).toContain(
      t('spaces.analytics.learning.allKnowledgePoints')
    )
  })

  it('同一发言的两处勾选共用父级状态，清除和生成不自行修改数据', async () => {
    const view = mount()
    expect(view.container.querySelectorAll('input:checked')).toHaveLength(0)
    const checkbox = view.getAllByRole('checkbox')[0] as HTMLInputElement
    checkbox.checked = true
    await fireEvent.input(checkbox)
    expect(view.emitted().setSelected).toEqual([['b-1', true]])
    await view.rerender({ selected: ['b-1'] })
    expect(view.container.querySelectorAll('input:checked')).toHaveLength(2)
    await fireEvent.click(view.getByRole('button', { name: t('spaces.analytics.learning.clear') }))
    await fireEvent.click(view.getByRole('button', { name: t('spaces.analytics.learning.buildOutline') }))
    expect(view.emitted().clearSelection).toEqual([[]])
    expect(view.emitted().buildOutline).toEqual([[]])
    expect(view.container.querySelectorAll('input:checked')).toHaveLength(2)
  })

  it('没有选择时不能生成提纲', () => {
    const view = mount()
    expect(
      (view.getByRole('button', { name: t('spaces.analytics.learning.buildOutline') }) as HTMLButtonElement).disabled
    ).toBe(true)
  })
})
