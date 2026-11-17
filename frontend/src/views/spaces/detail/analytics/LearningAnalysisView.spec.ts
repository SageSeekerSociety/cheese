import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import LearningExcerptDialog from './components/LearningExcerptDialog.vue'
import LearningExportDialog from './components/LearningExportDialog.vue'
import LearningAnalysisView from './LearningAnalysisView.vue'

import i18n, { setLocale, t } from '@/i18n'
import {
  LEARNING_CLASS_EXCERPTS,
  LEARNING_CLASS_OFF,
  LEARNING_CLASS_OVERVIEW,
  LEARNING_CLASS_POINT,
  LEARNING_CLASS_POINTS,
  LEARNING_CLASS_QUESTIONS,
  LEARNING_CLASS_TASKS,
} from '@/views/demo/catalogLearningClassFixtures'

const plugins = () => [createVuetify({ components, directives }), i18n]

function mount(props: Record<string, unknown>) {
  return render(LearningAnalysisView, {
    props: { sub: 'overview', enabled: true, overview: LEARNING_CLASS_OVERVIEW, ...props },
    slots: { legacy: () => h('div', { 'data-testid': 'legacy' }, '原有内容') },
    global: { plugins: plugins() },
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.stubGlobal(
    'visualViewport',
    Object.assign(new EventTarget(), { width: 1280, height: 800, offsetLeft: 0, offsetTop: 0, scale: 1 })
  )
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('学情分析的画面', () => {
  it('空间没开学情分析：只有去设置里开启的提示和原有内容，没有页签和导出', () => {
    const view = mount({ enabled: false, overview: LEARNING_CLASS_OFF })
    expect(view.queryByText(t('spaces.analytics.learning.class.off.title'))).not.toBeNull()
    expect(view.queryByTestId('legacy')).not.toBeNull()
    expect(view.queryByRole('tab')).toBeNull()
    expect(view.queryByRole('button', { name: t('spaces.analytics.learning.class.export') })).toBeNull()
  })

  it('七个页签按顺序排开，点一个就把它交上去；总览下面仍是原有内容', async () => {
    const view = mount({})
    const tabs = view.getAllByRole('tab').map((tab) => tab.textContent?.trim())
    expect(tabs).toEqual(['总览', '重难点', '高频问题', '高频词', '题目难度', '提问行为', '趋势'])
    expect(view.queryByTestId('legacy')).not.toBeNull()
    await fireEvent.click(view.getByRole('tab', { name: '高频问题' }))
    expect(view.emitted()['update:sub']).toEqual([['questions']])
    await fireEvent.click(
      view.getAllByRole('button', { name: t('spaces.analytics.learning.class.overview.focusLink') })[0]
    )
    expect(view.emitted().openPoint).toEqual([[2]])
  })

  it('不足 3 人的知识点和题目只写「不足 3 人，不分析」，没有人数', () => {
    const points = mount({ sub: 'points', points: LEARNING_CLASS_POINTS })
    const row = points.getByText('视图').closest('tr')!
    expect(row.textContent).toContain(t('spaces.analytics.learning.class.notAnalysed'))
    // 名字一格加「不足 3 人」一格：问到、卡住、卡住率都没有。
    expect(row.querySelectorAll('td')).toHaveLength(2)
    cleanup()
    const tasks = mount({ sub: 'tasks', tasks: LEARNING_CLASS_TASKS })
    const taskRow = tasks.getByText('T5 事务实验').closest('tr')!
    expect(taskRow.textContent).toContain(t('spaces.analytics.learning.class.notAnalysed'))
  })

  it('例句只标代号；点「看匿名对话」交出这一组；详情里导出带上这个知识点', async () => {
    const questions = mount({ sub: 'questions', questions: LEARNING_CLASS_QUESTIONS })
    expect(questions.queryAllByText(/成员 A7F3/).length).toBeGreaterThan(0)
    await fireEvent.click(
      questions.getAllByRole('button', { name: t('spaces.analytics.learning.class.questions.viewExcerpts') })[0]
    )
    expect(questions.emitted().openExcerpts).toEqual([[{ cluster: 21 }]])
    cleanup()

    const detail = mount({ pointId: 2, pointDetail: LEARNING_CLASS_POINT })
    expect(detail.queryByText('内层不知道怎样引用外层当前行')).not.toBeNull()
    expect(detail.queryByText(t('spaces.analytics.learning.class.detail.noList', { n: 22 }))).not.toBeNull()
    await fireEvent.click(detail.getByRole('button', { name: t('spaces.analytics.learning.class.detail.exportPoint') }))
    expect(detail.emitted().export).toEqual([[2]])
  })

  it('推送巩固题：交出的只有这个知识点；回来的只说人数，不足 3 人只说不足', async () => {
    const detail = mount({
      pointId: 2,
      pointDetail: { ...LEARNING_CLASS_POINT, practice: { practiced: 9, improved: 5 } },
    })
    expect(detail.getByTestId('lc-practice-line').textContent).toContain('做过巩固题的 9 人里，5 人升了一档')
    await fireEvent.click(detail.getByTestId('lc-push-button'))
    expect(detail.emitted().push).toEqual([[2]])

    await detail.rerender({ pushResult: { pushed: true, recipients: 22, minPeople: 3 } })
    expect(detail.getByTestId('lc-push-result').textContent).toBe('已推送给 22 人')
    await detail.rerender({ pushResult: { pushed: true, recipients: null, minPeople: 3 } })
    expect(detail.getByTestId('lc-push-result').textContent).toContain('不足 3 人')
    await detail.rerender({ pushResult: { pushed: false, recipients: null, minPeople: 3 } })
    expect(detail.getByTestId('lc-push-result').textContent).toContain('没有推送')
  })

  it('做过巩固题的不足 3 人：不报升档人数', () => {
    const detail = mount({ pointId: 2, pointDetail: { ...LEARNING_CLASS_POINT, practice: null } })
    expect(detail.getByTestId('lc-practice-line').textContent).toBe(
      t('spaces.analytics.learning.class.detail.practiceFew')
    )
  })
})

const Host = (component: unknown, props: Record<string, unknown>) =>
  defineComponent({ render: () => h(component as never, { open: true, ...props }) })

describe('导出报告对话框', () => {
  it('匿名摘录默认不勾，勾上才带出去；从知识点打开时默认只导出它，也能改回全部', async () => {
    const onExport = vi.fn()
    const view = render(Host(LearningExportDialog, { point: { id: 2, name: '相关子查询' }, onExport }), {
      global: { plugins: plugins() },
    })
    await fireEvent.click(
      await view.findByRole('button', { name: t('spaces.analytics.learning.class.exportDialog.submit') })
    )
    expect(onExport).toHaveBeenLastCalledWith({
      format: 'pdf',
      sections: ['tasks', 'points', 'advice'],
      knowledgePointId: 2,
    })
    const tick = async (label: string) => {
      const input = view.getByLabelText(label) as HTMLInputElement
      input.checked = true
      await fireEvent.input(input)
    }
    await tick(t('spaces.analytics.learning.class.exportDialog.sections.excerpts'))
    await tick(t('spaces.analytics.learning.class.exportDialog.formats.xlsx'))
    await tick(t('spaces.analytics.learning.class.exportDialog.scopeAll'))
    await fireEvent.click(view.getByRole('button', { name: t('spaces.analytics.learning.class.exportDialog.submit') }))
    expect(onExport).toHaveBeenLastCalledWith({
      format: 'xlsx',
      sections: ['tasks', 'points', 'advice', 'excerpts'],
      knowledgePointId: null,
    })
  })
})

describe('匿名摘录对话框', () => {
  it('只标代号；没有原文坐标时没有「查看原文」', async () => {
    const view = render(Host(LearningExcerptDialog, { data: LEARNING_CLASS_EXCERPTS }), {
      global: { plugins: plugins() },
    })
    expect((await view.findAllByText(/成员 A7F3/)).length).toBeGreaterThan(0)
    expect(view.queryByRole('button', { name: t('spaces.analytics.learning.class.excerpt.source') })).toBeNull()
  })
})
