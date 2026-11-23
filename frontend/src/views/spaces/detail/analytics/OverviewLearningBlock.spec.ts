/**
 * 总览顶部「成员学得怎么样」那一块（`OverviewView.vue`）：标题下一行小字、比例条加三栏数字、
 * 薄弱知识点逐行、「看完整的学情分析」在标题行右侧；不够人数、没有判断、没开学情分析各是什么样。
 */
import type { LearningClassOverview } from '@/types/apiSpaces'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import OverviewView from './OverviewView.vue'

import i18n, { setLocale } from '@/i18n'
import { analyticsOverview, analyticsPublishers } from '@/views/demo/catalogScenesSpacesDetailFixtures'

const LEARNING = {
  enabled: true,
  minPeople: 3,
  updatedAt: null,
  members: 4,
  completion: { rate: null, submitted: 0, expected: 0 },
  messagesThisWeek: 0,
  weakPoints: 3,
  tasks: [],
  focus: [
    { knowledgePointId: 7, name: '二叉树遍历', stuck: 4, involved: 4 },
    { knowledgePointId: 8, name: '递归', stuck: 3, involved: 4 },
  ],
  mastery: {
    members: 4,
    reported: 38,
    points: 40,
    levels: { mastered: 74, shaky: 44, weak: 30 },
    judged: 148,
    peopleJudged: 4,
  },
} as unknown as LearningClassOverview

function mount(learning: LearningClassOverview | null) {
  return render(OverviewView, {
    props: {
      loading: false,
      failed: false,
      errorDetail: null,
      overview: analyticsOverview(),
      alerts: null,
      publishers: analyticsPublishers().map((p) => ({ title: p.publisherName, value: p.publisherId })),
      publishersLoading: false,
      publisherId: null,
      groupBy: 'category',
      learning,
    },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

describe('总览·成员学得怎么样', () => {
  it('标题下一行小字写成员数、知识点数、判断数，不再提「下面这一条」', () => {
    const view = mount(LEARNING)
    expect(view.getByTestId('overview-learning-sub').textContent?.trim()).toBe('4 位成员 · 38 个知识点 · 148 条判断')
    expect(view.getByTestId('overview-learning').textContent).not.toContain('绘成下面这一条')
  })

  it('比例条按三档人数分段，三栏数字带占比', () => {
    const view = mount(LEARNING)
    const bar = view.getByTestId('overview-learning-bar')
    expect(bar.children).toHaveLength(3)
    expect([...bar.children].map((el) => (el as HTMLElement).style.flexGrow)).toEqual(['74', '44', '30'])
    const text = view.getByTestId('overview-learning').textContent ?? ''
    for (const word of ['掌握', '不稳', '薄弱', '74', '44', '30', '50.0%', '29.7%', '20.3%'])
      expect(text).toContain(word)
  })

  it('薄弱知识点一行一个，写「N / M 人卡住」，点一行报上去它的编号', async () => {
    const view = mount(LEARNING)
    expect(view.getAllByRole('listitem')).toHaveLength(2)
    expect(view.getByText('4 / 4 人卡住')).toBeTruthy()
    await fireEvent.click(view.getByText('递归'))
    expect(view.emitted().openLearningPoint).toEqual([[8]])
  })

  it('「看完整的学情分析」在标题行里，点了报 openLearning', async () => {
    const view = mount(LEARNING)
    const button = view.getByTestId('overview-learning-open')
    expect(button.closest('.learning-now__head')).not.toBeNull()
    await fireEvent.click(button)
    expect(view.emitted().openLearning).toHaveLength(1)
  })

  it('成员不够 3 人、知识点不报数时不画比例条，说明原因，也没有进入按钮', () => {
    const view = mount({
      ...LEARNING,
      focus: [],
      mastery: { ...LEARNING.mastery!, reported: 0, judged: 0, levels: { mastered: 0, shaky: 0, weak: 0 } },
    })
    expect(view.queryByTestId('overview-learning-bar')).toBeNull()
    expect(view.queryByTestId('overview-learning-open')).toBeNull()
    expect(view.queryByTestId('overview-learning-sub')).toBeNull()
    expect(view.getByTestId('overview-learning-few').textContent).toContain('人数还不够')
    expect(view.getByTestId('overview-learning').textContent).toContain('每个知识点至少 3 人')
  })

  it('四格小方块：4 人里 3 人卡住就亮 3 格，人多时最多 8 格按比例亮', () => {
    const view = mount(LEARNING)
    const rows = view.getAllByRole('listitem')
    expect(rows[1]!.querySelectorAll('.learning-now__pips i')).toHaveLength(4)
    expect(rows[1]!.querySelectorAll('.learning-now__pips i.on')).toHaveLength(3)
    cleanup()
    const many = mount({
      ...LEARNING,
      focus: [{ knowledgePointId: 9, name: '大班', stuck: 15, involved: 30 }],
    })
    const pips = many.getAllByRole('listitem')[0]!
    expect(pips.querySelectorAll('.learning-now__pips i')).toHaveLength(8)
    expect(pips.querySelectorAll('.learning-now__pips i.on')).toHaveLength(4)
  })

  it('没开学情分析（读不到判断）时只画空状态', () => {
    const view = mount(null)
    const block = view.getByTestId('overview-learning')
    expect(block.textContent).toContain('还没有成员的作答情况')
    expect(view.queryByTestId('overview-learning-bar')).toBeNull()
  })
})
