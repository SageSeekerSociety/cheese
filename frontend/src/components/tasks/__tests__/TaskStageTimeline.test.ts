import { createVuetify } from 'vuetify'
import { render, within } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import TaskStageTimeline from '../TaskStageTimeline.vue'

import i18n, { setLocale } from '@/i18n'

const STAGES = [
  { name: '需求分析', description: '', dueAt: new Date(2026, 10, 1, 23, 59).getTime() },
  { name: '实现', description: '', dueAt: null },
]

const mount = (props: Record<string, unknown>) =>
  render(TaskStageTimeline, { props: { stages: STAGES, ...props }, global: { plugins: [createVuetify(), i18n] } })

beforeEach(() => setLocale('zh-CN'))

describe('阶段时间轴', () => {
  it('按顺序列出每个阶段和它的截止时间', () => {
    const steps = within(mount({}).getByTestId('task-stage-timeline')).getAllByRole('listitem')

    expect(steps.map((s) => s.textContent)).toEqual([
      expect.stringContaining('需求分析'),
      expect.stringContaining('实现'),
    ])
    expect(steps[0].textContent).toContain('截止 11月1日 23:59')
    expect(steps[1].textContent).toContain('不设截止')
  })

  it('学生看到自己每个阶段走到哪一步', () => {
    const steps = mount({ statuses: ['PASSED', 'PENDING_REVIEW'] }).getAllByRole('listitem')

    expect(steps[0].textContent).toContain('已通过')
    expect(steps[1].textContent).toContain('待批改')
  })

  it('出题人看到每个阶段上各状态的人数', () => {
    const counts = [
      { NOT_SUBMITTED: 1, PENDING_REVIEW: 2, PASSED: 3, REJECTED: 0 },
      { NOT_SUBMITTED: 6, PENDING_REVIEW: 0, PASSED: 0, REJECTED: 0 },
    ]
    const steps = mount({ counts }).getAllByRole('listitem')

    expect(steps[0].textContent).toMatch(/未提交\s*1/)
    expect(steps[0].textContent).toMatch(/待批改\s*2/)
    expect(steps[0].textContent).toMatch(/已通过\s*3/)
    expect(steps[1].textContent).toMatch(/未提交\s*6/)
  })
})
