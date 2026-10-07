/** 例行任务那条消息下面的一行：跑着时说在跑，没跑成说原因，能去看这次执行。 */
import type { Component } from 'vue'
import type { RoutineRunLine as Run } from '@/types/routineRun'

import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import RoutineRunLine from './RoutineRunLine.vue'

import i18n, { setLocale } from '@/i18n'

setLocale('zh-CN')

const RUN: Run = {
  routine_id: 'r1',
  run_id: 'run1',
  title: '周报整理',
  status: 'running',
  reason: '',
  outputs: [],
  started_at: null,
  finished_at: null,
  thread_id: 't1',
}

function mount(run: Partial<Run>, threaded = false) {
  return render(RoutineRunLine as unknown as Component, {
    props: { run: { ...RUN, ...run }, threaded },
    global: { plugins: [i18n] },
  })
}

describe('例行任务的一次执行', () => {
  it('还在跑时说在跑', () => {
    const ui = mount({ status: 'running' })
    expect(ui.getByTestId('routine-run-going')).toBeTruthy()
  })

  it('没跑成时给出平台说的原因', () => {
    const ui = mount({ status: 'failed', reason: '工作电脑的环境准备失败' })
    expect(ui.getByTestId('routine-run-missed').textContent).toContain('工作电脑的环境准备失败')
  })

  it('没有支线那一行时，能去看这次执行', async () => {
    const ui = mount({ status: 'succeeded' })
    await fireEvent.click(ui.getByRole('button', { name: '查看这次运行' }))
    expect(ui.emitted().open).toHaveLength(1)
  })

  it('支线那一行已经在了，就不重复给入口', () => {
    const ui = mount({ status: 'succeeded' }, true)
    expect(ui.queryByRole('button', { name: '查看这次运行' })).toBeNull()
  })
})
