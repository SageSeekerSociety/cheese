import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
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
    // 学生那一格画的是「脸 + 名字」：名字落在共用的身份视图里，不再是一段裸文字。
    const cells = view.container.querySelectorAll('.an-bar .v-select')
    expect(cells[0].querySelector('.identity-view__name')?.textContent).toBe('鲍勃')
    expect(cells[1].querySelector('.v-select__selection-text')?.textContent).toContain(
      t('spaces.analytics.learning.allKnowledgePoints')
    )
  })

  it('学生下拉每一行都画成员的脸，选中的那一格也画', async () => {
    const view = mount()
    await fireEvent.mouseDown(view.getByRole('combobox', { name: t('spaces.analytics.learning.student') }))
    const rows = await waitFor(() => {
      const found = Array.from(document.querySelectorAll<HTMLElement>('.v-overlay--active .v-list-item'))
      expect(found).toHaveLength(3)
      return found
    })
    // 「全部成员」那一行背后没有具体的人，仍旧是纯文字。
    expect(rows[0].querySelector('.identity-view')).toBeNull()
    // 爱丽丝自己挑过头像（素材 id 5）→ 画的就是那张图，不是彩色首字母。
    expect(rows[1].querySelector('.identity-view__face img')?.getAttribute('src')).toContain('/api/avatars/5')
    // 鲍勃没挑过 → 退回按 handle 派生的彩色首字母，不是一张空白格。
    expect(rows[2].querySelector('.user-avatar-char')?.textContent).toBe('鲍')
    // 名字只画一次：抹掉 `title` 之后 Vuetify 自己那行标题不再叠在脸上。
    expect(rows[1].textContent?.match(/爱丽丝/g)).toHaveLength(1)

    await view.rerender({ student: 'alice' })
    const cell = view.container.querySelectorAll('.an-bar .v-select')[0]
    expect(cell.querySelector('.identity-view__name')?.textContent).toBe('爱丽丝')
    expect(cell.querySelector('.identity-view__face img')?.getAttribute('src')).toContain('/api/avatars/5')
  })

  it('引用里那位学生也画脸，用的是他自己的素材 id 和 handle', () => {
    const view = mount()
    // 消息那一段的两条引用各来自一位学生：爱丽丝挑过头像，鲍勃没有 —— 两支降级都在这一屏里。
    const students = view.container.querySelectorAll<HTMLElement>('.lr__quotes .quote-item__student')
    expect(students[0].querySelector('.identity-view__name')?.textContent).toBe('爱丽丝')
    expect(students[0].querySelector('.identity-view__face img')?.getAttribute('src')).toContain('/api/avatars/5')
    expect(students[1].querySelector('.identity-view__name')?.textContent).toBe('鲍勃')
    expect(students[1].querySelector('.user-avatar-char')?.textContent).toBe('鲍')
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
