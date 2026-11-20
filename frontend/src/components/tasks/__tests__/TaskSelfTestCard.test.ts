// 提交页的「自测样例」：剩几次、用完了说什么时候重置、过了截止点不了；排不上时照原样说后端
// 那句话；跑完了画样例的结果和跑的是哪一版代码。
import type { TaskJudgeRun, TaskSelfTestView } from '@/types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import TaskSelfTestCard from '../TaskSelfTestCard.vue'

import i18n, { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

const done: TaskJudgeRun = {
  state: 'done',
  purpose: 'self_test',
  codeSha: '0123456789abcdef',
  suiteRevision: 2,
  passedPoints: 10,
  totalPoints: 10,
  createdAt: 0,
  startedAt: 0,
  finishedAt: 0,
  machineMs: 800,
  cases: [{ name: '样例 1', visibility: 'SAMPLE', status: 'ok', points: 10, input: '1 2', expected: '3', actual: '3' }],
  passedCases: 1,
  caseCount: 1,
}

const view = (patch: Partial<TaskSelfTestView> = {}): TaskSelfTestView => ({
  selfTest: null,
  dailyLimit: 5,
  usedToday: 2,
  resetsAt: Date.UTC(2026, 10, 18, 0, 0),
  ...patch,
})

function mount(props: Record<string, unknown>) {
  return render(TaskSelfTestCard, {
    props,
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

const button = () => screen.getByTestId('task-self-test-run') as HTMLButtonElement

describe('自测样例', () => {
  it('还有次数：说今天还剩几次，点一下往上报', async () => {
    const card = mount({ view: view() })
    expect(screen.getByTestId('task-self-test-count').textContent).toContain('今天还能自测 3 次（每天 5 次）')
    expect(button().disabled).toBe(false)
    await fireEvent.click(button())
    expect(card.emitted('run')).toEqual([[]])
  })

  it('次数用完：点不了，说什么时候重置', () => {
    mount({ view: view({ usedToday: 5 }) })
    expect(button().disabled).toBe(true)
    expect(screen.getByTestId('task-self-test-count').textContent).toContain('今天的 5 次自测用完了')
  })

  it('过了截止：点不了，说已过截止时间', () => {
    mount({ view: view(), closed: true })
    expect(button().disabled).toBe(true)
    expect(screen.getByTestId('task-self-test-count').textContent).toBe('已过截止时间，不能再自测')
  })

  it('上一次还在跑：按钮转圈点不了；还没读到次数时也点不了', () => {
    mount({ view: view({ selfTest: { ...done, state: 'running', cases: [] } }) })
    expect(button().disabled).toBe(true)
    expect(screen.getByTestId('task-judge-pending')).toBeTruthy()
    cleanup()
    mount({ view: null })
    expect(button().disabled).toBe(true)
  })

  it('排不上：照原样显示后端那句话', () => {
    mount({ view: view(), error: '今天的自测次数用完了' })
    expect(screen.getByTestId('task-self-test-error').textContent).toBe('今天的自测次数用完了')
  })

  it('跑完了：画样例的结果和跑的是哪一版代码', () => {
    mount({ view: view({ selfTest: done }) })
    expect(screen.getByTestId('task-self-test-sha').textContent).toContain('01234567')
    expect(screen.getByTestId('task-judge-passed').textContent).toBe('1/1')
  })
})
