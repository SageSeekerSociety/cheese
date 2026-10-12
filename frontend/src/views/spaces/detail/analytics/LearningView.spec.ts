import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import LearningView from './LearningView.vue'

import BaseLoadError from '@/components/base/BaseLoadError.vue'
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
const mounted: { unmount: () => void }[] = []
function render(props: Partial<Props> = {}) {
  const view = mount(LearningView, {
    props: { ...base, ...props },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
  mounted.push(view)
  return view
}
beforeEach(() => setLocale('zh-CN'))
afterEach(() => mounted.splice(0).forEach((view) => view.unmount()))

describe('学习看板的独立画面', () => {
  it('首次读取显示进度，刷新保留队列、选择与提纲而不重现首次进度', async () => {
    const view = render({ loading: true, queues: null, questions: [] })
    expect(view.find('.v-progress-linear').exists()).toBe(true)
    expect(view.text()).not.toContain(t('spaces.analytics.learning.questions.empty'))
    await view.setProps({ queues: LEARNING_QUEUES, questions: LEARNING_QUESTIONS, selected: ['b-1'], outline: LEARNING_OUTLINE, outlineLoading: true })
    expect(view.find('.v-progress-linear').exists()).toBe(false)
    expect(view.text()).toContain(LEARNING_OUTLINE.title)
    expect(view.findAll('input:checked')).toHaveLength(2)
    expect(view.find('.v-btn--loading').exists()).toBe(true)
  })

  it('没有成员项目优先于失败，失败则替换旧内容并提供重试', async () => {
    const view = render({ learningFilters: { ...LEARNING_FILTERS, projectCount: 0 }, failed: true, errorDetail: 'timeout' })
    expect(view.text()).toContain(t('spaces.analytics.learning.noProjects'))
    expect(view.findComponent(BaseLoadError).exists()).toBe(false)
    await view.setProps({ learningFilters: LEARNING_FILTERS })
    expect(view.text()).toContain('timeout')
    expect(view.text()).not.toContain(LEARNING_QUESTIONS[0].quote)
    await view.findComponent(BaseLoadError).find('button').trigger('click')
    expect(view.emitted('load')).toEqual([[]])
  })

  it('只有读取为空时显示对应空态，不把复核信号不可用误报为空', async () => {
    const view = render({ queues: EMPTY_LEARNING_QUEUES, questions: [] })
    expect(view.text()).toContain(t('spaces.analytics.learning.queue.noFlagged'))
    expect(view.text()).toContain(t('spaces.analytics.learning.queue.noStuck'))
    expect(view.text()).toContain(t('spaces.analytics.learning.questions.empty'))
    await view.setProps({ queues: LEARNING_QUEUES })
    expect(view.text()).toContain(LEARNING_QUEUES.reviewFlag.reason)
    expect(view.text()).not.toContain(t('spaces.analytics.learning.queue.noFlagged'))
  })

  it('两个筛选独立发送模型变更，也能反映外部传入的值', async () => {
    const view = render()
    const [student, point] = view.findAllComponents(components.VSelect)
    student.vm.$emit('update:modelValue', 'alice')
    point.vm.$emit('update:modelValue', 7)
    expect(view.emitted('update:student')).toEqual([['alice']])
    expect(view.emitted('update:knowledgePoint')).toEqual([[7]])
    await view.setProps({ student: 'bob', knowledgePoint: null })
    expect(student.props('modelValue')).toBe('bob')
    expect(point.props('modelValue')).toBe(null)
  })

  it('同一发言的两处勾选共用父级状态，清除和生成不自行修改数据', async () => {
    const view = render()
    expect(view.findAll('input:checked')).toHaveLength(0)
    await view.find('input[type="checkbox"]').setValue(true)
    expect(view.emitted('setSelected')).toEqual([['b-1', true]])
    await view.setProps({ selected: ['b-1'] })
    expect(view.findAll('input:checked')).toHaveLength(2)
    const clear = view.findAll('button').find((button) => button.text() === t('spaces.analytics.learning.clear'))!
    const build = view.findAll('button').find((button) => button.text() === t('spaces.analytics.learning.buildOutline'))!
    await clear.trigger('click')
    await build.trigger('click')
    expect(view.emitted('clearSelection')).toEqual([[]])
    expect(view.emitted('buildOutline')).toEqual([[]])
    expect(view.findAll('input:checked')).toHaveLength(2)
  })

  it('没有选择时不能生成提纲', () => {
    const view = render()
    const build = view.findAll('button').find((button) => button.text() === t('spaces.analytics.learning.buildOutline'))!
    expect(build.attributes('disabled')).toBeDefined()
  })
})
